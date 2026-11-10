"""Conservative collection of product structured data. Python 3.10+, no packages."""
import argparse
import copy
try:
    import fcntl
except ImportError:  # Windows development; production uses flock on Ubuntu.
    fcntl = None
import html
from http.cookiejar import CookieJar
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from urllib.request import Request, urlopen, HTTPRedirectHandler, HTTPCookieProcessor, build_opener
from urllib.robotparser import RobotFileParser

from build import ROOT, atomic_write, render
from request_pacing import RequestPacing, RateLimited
from merge_release import merge_release
from data_quality import number, same_page, apply_result, compact_history, lock_run, canonical_url, timestamp
from monitoring import Checkpoint

AGENT = 'PrometPriceMonitor/1.0'
HOSTS = {'www.vseinstrumenti.ru', 'vseinstrumenti.ru', 'www.komus.ru',
         'dimaxmet.ru', 'www.dimaxmet.ru', 'evrosafe.ru', 'www.evrosafe.ru',
         'komus.ru', 'profsafe.ru', 'www.profsafe.ru', 'spb.profsafe.ru',
         'xn----7sbenacbbl2bhik1tlb.xn--p1ai',
         'www.xn----7sbenacbbl2bhik1tlb.xn--p1ai'}
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
        'meta', 'param', 'source', 'track', 'wbr'}
STATUSES = {'InStock': 'В наличии', 'OutOfStock': 'Нет в наличии',
            'SoldOut': 'Нет в наличии', 'Discontinued': 'Нет в наличии',
            'PreOrder': 'Под заказ', 'BackOrder': 'Под заказ',
            'LimitedAvailability': 'В наличии', 'OnlineOnly': 'В наличии'}

def validate_url(url):
    u = urlsplit(url)
    if u.scheme != 'https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None, 443):
        raise ValueError('Источник вне разрешённого списка HTTPS')
    return url

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def download(url, limit=8_000_000, headers=None, opener=None):
    validate_url(url)
    req = Request(url, headers={'User-Agent': AGENT, 'Accept-Language': 'ru-RU,ru;q=0.9',
                                'Accept': 'text/html,application/xhtml+xml', **(headers or {})})
    with (opener or build_opener(SafeRedirect())).open(req, timeout=25) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Слишком большой ответ')
        charset = response.headers.get_content_charset() or 'utf-8'
        return body.decode(charset, errors='replace')

class ProductHTML(HTMLParser):
    """Only collect Offer metadata inside Product; related products stay separate."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.products, self.scripts = [], [], []
        self.script = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        parent = self.stack[-1][1] if self.stack else None
        product = parent
        if a.get('itemtype', '').rstrip('/').endswith('/Product'):
            product = {'offers': {}}
            self.products.append(product)
        prop = a.get('itemprop', '')
        value = a.get('content') or a.get('href')
        if product is not None and value:
            if prop in ('price', 'priceCurrency', 'availability'):
                product['offers'].setdefault(prop, []).append(value)
            elif prop in ('url', 'sku'):
                product.setdefault(prop, value)
        if tag == 'script' and a.get('type', '').lower() == 'application/ld+json':
            self.script = []
        if tag not in VOID:
            self.stack.append((tag, product))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            try:
                self.scripts.append(json.loads(''.join(self.script)))
            except (ValueError, TypeError):
                pass
            self.script = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

def json_products(value):
    if isinstance(value, list):
        for item in value:
            yield from json_products(item)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if isinstance(types, str):
            types = [types]
        if 'Product' in types:
            yield value
            return
        # Do not traverse recommendations/ItemList: these may be other products.
        yield from json_products(value.get('@graph', []))
        main = value.get('mainEntity')
        if main:
            yield from json_products(main)

def parse_product(source, url):
    parser = ProductHTML()
    parser.feed(source)
    candidates = []
    for script in parser.scripts:
        candidates.extend(json_products(script))
    candidates.extend(parser.products)
    matching = [p for p in candidates if isinstance(p.get('url'), str) and same_page(p['url'], url)]
    if matching:
        candidates = matching
    elif len(candidates) != 1 or any(p.get('url') for p in candidates):
        raise ValueError('Не найдена однозначная разметка основного товара')
    observations = []
    for product in candidates:
        offers = product.get('offers', [])
        if isinstance(offers, dict):
            offers = [offers]
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            # AggregateOffer lowPrice and highPrice are deliberately not prices.
            def single(key):
                v = offer.get(key)
                if isinstance(v, list):
                    values = {str(x) for x in v}
                    if len(values) != 1:
                        raise ValueError('Несколько разных цен или условий в карточке')
                    return v[0]
                return v
            if str(single('priceCurrency')).strip().upper() not in ('RUB', 'RUR'):
                continue
            raw_availability = single('availability')
            availability = str(raw_availability).rsplit('/', 1)[-1]
            if availability not in STATUSES:
                continue
            raw_price = single('price')
            if raw_price is None and STATUSES[availability] != 'Нет в наличии':
                continue
            observations.append((number(raw_price) if raw_price is not None else None, STATUSES[availability]))
    unique = set(observations)
    if len(unique) != 1:
        raise ValueError('Цена RUB и наличие не определены однозначно')
    price, status = unique.pop()
    sold = None
    if 'xn----7sbenacbbl2bhik1tlb' in urlsplit(url).hostname:
        match = re.search(r'class=[\"\'][^\"\']*product-delivery__quantity-number[^\"\']*[\"\'][^>]*>([^<]+)', source)
        if match:
            digits = re.sub(r'\s', '', html.unescape(match.group(1)))
            if digits.isdigit():
                sold = int(digits)
    return {'price': price if status != 'Нет в наличии' else None,
            'unavailablePrice': price if status == 'Нет в наличии' else None,
            'statusObserved': status, 'soldUnits': sold}

def regional_adapter(host, city):
    if city not in ('Москва', 'Санкт-Петербург'):
        raise ValueError('Неизвестный город')
    if host in ('komus.ru', 'www.komus.ru'):
        return {'initUrl': 'https://www.komus.ru/region/change?region=' + ('77' if city == 'Москва' else '78'),
                'cityPattern': r'gtm-current-region[^>]*>[\s\S]{0,500}?b-enterAccount__link[^>]*>\s*(?P<city>[^<]+)'}
    if host in ('profsafe.ru', 'www.profsafe.ru', 'spb.profsafe.ru'):
        return {'host': 'profsafe.ru' if city == 'Москва' else 'spb.profsafe.ru',
                'cityPattern': r'js_city_chooser[^>]*>\s*<span>(?P<city>[^<]+)</span>'}
    return None

class Collector:
    def __init__(self, region_config=None, pacing=None):
        self.pacing = pacing or RequestPacing()
        self.robots, self.last_request = {}, {}
        self.region_config = region_config or {}
        self.sessions = {}

    def fetch(self, url, city):
        validate_url(url)
        original_host = urlsplit(url).hostname
        region = self.region_config.get(city, {}).get(original_host) or regional_adapter(original_host, city)
        if not region or not region.get("cityPattern"):
            raise ValueError("Регион не настроен: требуется проверенный адаптер выбора города " + city)
        if region.get('host'):
            url = urlsplit(url)._replace(netloc=region['host']).geturl()
            validate_url(url)
        u = urlsplit(url)
        origin = f'{u.scheme}://{u.netloc}'
        if origin not in self.robots:
            robot = RobotFileParser()
            try:
                robot.parse(self.pacing.request(download, origin + '/robots.txt', 500_000).splitlines())
            except RateLimited:
                raise
            except Exception as exc:
                # A robots fetch failure is not permission to crawl.
                self.robots[origin] = str(exc)
            else:
                self.robots[origin] = robot
        robot = self.robots[origin]
        if isinstance(robot, str):
            raise ValueError('Не удалось проверить robots.txt: ' + robot)
        if not robot.can_fetch(AGENT, url):
            raise ValueError('Сбор запрещён robots.txt')
        interval = max(2, robot.crawl_delay(AGENT) or 0)
        # Slow sources are handled in the background by the systemd service.
        if interval > 600:
            raise ValueError('Слишком большой crawl-delay; источник пропущен')
        time.sleep(max(0, self.last_request.get(origin, 0) + interval - time.monotonic()))
        try:
            headers = {}
            if region.get('cookie'):
                cookie = region['cookie']
                if '\r' in cookie or '\n' in cookie:
                    raise ValueError('Некорректный региональный cookie')
                headers['Cookie'] = cookie
            session_key = (city, original_host)
            if session_key not in self.sessions:
                opener = build_opener(SafeRedirect(), HTTPCookieProcessor(CookieJar()))
                if region.get('initUrl'):
                    validate_url(region['initUrl'])
                    if not robot.can_fetch(AGENT, region['initUrl']):
                        raise ValueError('Выбор региона запрещён robots.txt')
                    self.pacing.request(download, region['initUrl'], opener=opener, min_interval=interval)
                    time.sleep(interval)
                self.sessions[session_key] = opener
            source = self.pacing.request(download, url, headers=headers, opener=self.sessions[session_key], min_interval=interval)
            match = re.search(region['cityPattern'], source, re.S)
            if not match or 'city' not in match.groupdict() or html.unescape(match.group('city')).strip() != city:
                raise ValueError('Выбранный город не подтверждён в ответе дилера: ' + city)
            result = parse_product(source, url)
            result.update(city=city, cityVerified=True, regionalSourceUrl=url)
            return result
        finally:
            self.last_request[origin] = time.monotonic()

CITIES = ('Москва', 'Санкт-Петербург')

def run(args, fetch=None):
    if args.limit < 0:
        raise ValueError('Лимит не может быть отрицательным')
    resume_since = timestamp(args.resume_since) if getattr(args, 'resume_since', None) else None
    cities = getattr(args, 'cities', None) or list(CITIES)
    if any(city not in CITIES for city in cities):
        raise ValueError('Неизвестный город')
    cities = list(dict.fromkeys(cities))
    skus = set(getattr(args, 'skus', None) or [])
    def selected(row):
        return not skus or bool(skus.intersection(row.get('skus', [row.get('sku')])))
    state = args.state
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'refresh.lock').open('a+') as lock:
        lock_run(lock)
        path = state / 'observations.json'
        data = json.loads((path if path.exists() else ROOT / 'data/observations.json').read_text(encoding='utf-8'))
        original = copy.deepcopy(data)
        data = merge_release(json.loads((ROOT / 'data/observations.json').read_text(encoding='utf-8')), data)
        now = datetime.now(timezone.utc).isoformat()
        report = {'attemptedAt': now, 'running': True, 'updated': 0, 'failed': 0,
                  'skipped': 0, 'expected': 0, 'cities': {}, 'errors': []}
        atomic_write(state / 'backups' / (now.replace(':', '-') + '.json'), json.dumps(original, ensure_ascii=False))
        data['refreshSummary'] = report
        datasets = {}
        for city in cities:
            if city == 'Москва':
                dataset = data
            else:
                dataset = data.setdefault('cities', {}).get(city)
                if dataset is None:
                    # Copy only the approved card registry, never Moscow prices/history.
                    dataset = {k: copy.deepcopy(data[k]) for k in ('selected', 'pairs')}
                    for kind in ('own', 'analogs'):
                        dataset[kind] = [{k: copy.deepcopy(v) for k,v in row.items()
                                         if k in ('id','sku','skus','model','maker','site','url')}
                                        for row in data[kind]]
                    dataset.update(history={}, manufacturerPrices=[])
                    data['cities'][city] = dataset
            datasets[city] = dataset
            total = sum(selected(row) for k in ('own', 'analogs') for row in dataset[k])
            city_report = {'attemptedAt': now, 'running': True, 'expected': total,
                           'updated': 0, 'failed': 0, 'skipped': 0, 'sources': {}, 'errors': []}
            dataset['refresh'] = city_report
            report['cities'][city] = city_report
            report['expected'] += total
        checkpoint = Checkpoint(state, args.publish, render, data, report)
        config_path = getattr(args, 'region_config', None) or state / 'regions.json'
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        pacing = RequestPacing(state / 'request-pacing.json')
        report['network'] = pacing.metrics
        fetch = fetch or Collector(config, pacing).fetch
        cache = {}
        checkpoint()
        try:
            for city, dataset in datasets.items():
                cr = report['cities'][city]
                for kind in ('own', 'analogs'):
                    history = dataset.setdefault('history', {}).setdefault(kind, [])
                    for row in sorted(dataset[kind], key=lambda r: r['site'] == 'profsafe.ru'):
                        if not selected(row):
                            continue
                        ident = (city, canonical_url(row['url']))
                        stats = cr['sources'].setdefault(row['site'], {'expected': 0, 'updated': 0, 'failed': 0, 'skipped': 0})
                        stats['expected'] += 1
                        if (getattr(args, 'sites', None) and row['site'] not in args.sites) or (resume_since and row.get('lastAttemptStatus') == 'verified' and row.get('checkedAt') and timestamp(row['checkedAt']) >= resume_since):
                            cr['skipped'] += 1; report['skipped'] += 1; stats['skipped'] += 1
                            continue
                        if args.limit and len(cache) >= args.limit and ident not in cache:
                            cr['skipped'] += 1; report['skipped'] += 1; stats['skipped'] += 1
                            continue
                        if ident not in cache:
                            try:
                                result = fetch(row['url'], city)
                            except Exception as exc:
                                result = str(exc)[:350]
                            cache[ident] = (result, datetime.now(timezone.utc).isoformat())
                        result, checked = cache[ident]
                        try:
                            if isinstance(result, str):
                                raise ValueError(result)
                            if result.get('city') != city or result.get('cityVerified') is not True:
                                raise ValueError('Город результата не подтверждён: ' + city)
                            old = apply_result(row, result, checked, args.allow_large_change)
                            if old.get('checkedAt'):
                                history.append(old)
                            cr['updated'] += 1; report['updated'] += 1; stats['updated'] += 1
                        except Exception as exc:
                            row.update(lastAttemptAt=checked, refreshError=str(exc)[:350], lastAttemptStatus='failed')
                            cr['failed'] += 1; report['failed'] += 1; stats['failed'] += 1
                            error = {'city': city, 'site': row['site'], 'url': row['url'], 'error': row['refreshError']}
                            cr['errors'].append(error); report['errors'].append(error)
                        print(json.dumps({'event': 'observation', 'runId': now, 'at': checked, 'city': city, 'site': row['site'], 'id': row['id'], 'error': row.get('refreshError')}, ensure_ascii=False), flush=True)
                        checkpoint()
                    if not skus:
                        history[:] = compact_history(history)
                cr['running'] = False
                cr['completedAt'] = datetime.now(timezone.utc).isoformat()
                cr['complete'] = cr['updated'] == cr['expected'] and not cr['skipped']
                checkpoint()
        finally:
            report['running'] = False
            report['completedAt'] = datetime.now(timezone.utc).isoformat()
            for cr in report['cities'].values():
                if cr['running']:
                    cr['running'] = False
                    cr['interrupted'] = True
                    cr['complete'] = False
            report['complete'] = all(cr.get('complete', False) for cr in report['cities'].values())
            checkpoint()
        for old in sorted((state / 'backups').glob('*.json'))[:-30]:
            old.unlink()
        print(json.dumps({k: v for k, v in report.items() if k not in ('errors', 'cities')}, ensure_ascii=False), flush=True)
        # 0 = complete; 3 = partial; 2 = no confirmed updates.
        return 0 if report['complete'] else (3 if report['updated'] else 2)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / 'runtime')
    parser.add_argument('--publish', type=Path, default=ROOT / 'public')
    parser.add_argument('--limit', type=int, default=0, help='Limit unique URLs for diagnostics')
    parser.add_argument('--allow-large-change', action='store_true')
    parser.add_argument('--cities', nargs='+', choices=CITIES, default=list(CITIES))
    parser.add_argument('--region-config', type=Path, help='Local region selection and proof configuration (not committed)')
    parser.add_argument('--sites', nargs='+', help='Only specified dealer site IDs')
    parser.add_argument('--skus', nargs='+', help='Only own models and analogs linked to these PROMET SKUs')
    parser.add_argument('--resume-since', help='Skip verified observations since ISO timestamp/date')
    args = parser.parse_args()
    try:
        raise SystemExit(run(args))
    except BlockingIOError:
        raise SystemExit('Обновление уже выполняется')
