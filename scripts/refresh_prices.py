"""Conservative collection of product structured data. Python 3.10+, no packages."""
import argparse
import copy
import fcntl
import html
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.robotparser import RobotFileParser

from build import ROOT, atomic_write, render
from merge_release import merge_release

AGENT = 'PrometPriceMonitor/1.0'
HOSTS = {'www.vseinstrumenti.ru', 'vseinstrumenti.ru', 'www.komus.ru',
         'komus.ru', 'profsafe.ru', 'www.profsafe.ru',
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

def download(url, limit=8_000_000):
    validate_url(url)
    req = Request(url, headers={'User-Agent': AGENT, 'Accept-Language': 'ru-RU,ru;q=0.9',
                                'Accept': 'text/html,application/xhtml+xml'})
    with build_opener(SafeRedirect()).open(req, timeout=25) as response:
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

def number(value):
    if isinstance(value, bool):
        raise ValueError('Некорректная цена')
    raw = re.sub(r'[\s\u00a0\u202f]', '', str(value)).replace(',', '.')
    result = float(raw)
    if not math.isfinite(result) or not 0 < result < 10_000_000:
        raise ValueError('Некорректная цена')
    return round(result, 2)

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

def same_page(first, second):
    return urlsplit(first).path.rstrip('/') == urlsplit(second).path.rstrip('/')

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
    elif len(candidates) != 1:
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
            if str(single('priceCurrency')).upper() not in ('RUB', 'RUR'):
                continue
            raw_availability = single('availability')
            availability = str(raw_availability).rsplit('/', 1)[-1]
            if availability not in STATUSES or single('price') is None:
                continue
            observations.append((number(single('price')), STATUSES[availability]))
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

class Collector:
    def __init__(self):
        self.robots, self.last_request = {}, {}

    def fetch(self, url):
        validate_url(url)
        u = urlsplit(url)
        origin = f'{u.scheme}://{u.netloc}'
        if origin not in self.robots:
            robot = RobotFileParser()
            try:
                robot.parse(download(origin + '/robots.txt', 500_000).splitlines())
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
            return parse_product(download(url), url)
        finally:
            self.last_request[origin] = time.monotonic()

def apply_result(row, result, now, allow_large=False):
    previous = row.get('price') or row.get('unavailablePrice')
    current = result.get('price') or result.get('unavailablePrice')
    if previous and abs(current / previous - 1) > .5 and not allow_large:
        raise ValueError('Изменение цены более 50%; требуется ручная проверка')
    old = copy.deepcopy(row)
    row.update(result)
    row.update(checkedAt=now, lastAttemptAt=now, refreshError=None, collectionError=None, lastAttemptStatus="verified",
               collectionMethod='Product/Offer structured data', availability=result['statusObserved'],
               delivery='Автоматически не проверено', oldPrice=None, conditionalPrice=None,
               conditionalType='', promo='Акции автоматически не проверены',
               conditions='Публичная цена из разметки карточки. Регион и условия доставки уточняйте у продавца.')
    return old

def run(args, fetch=None):
    state = args.state
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'refresh.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = state / 'observations.json'
        data = json.loads((path if path.exists() else ROOT / 'data/observations.json').read_text())
        original = copy.deepcopy(data)
        data = merge_release(json.loads((ROOT / "data/observations.json").read_text(encoding="utf-8")), data)
        now = datetime.now(timezone.utc).isoformat()
        report = {'attemptedAt': now, 'running': True, 'updated': 0, 'failed': 0, 'sources': {}, 'errors': []}
        backup = state / 'backups' / (now.replace(':', '-') + '.json')
        atomic_write(backup, json.dumps(original, ensure_ascii=False))
        data['refresh'] = report
        def checkpoint():
            page = render(data)
            atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
            atomic_write(args.publish / 'index.html', page)
            atomic_write(state / 'last-refresh.json', json.dumps(report, ensure_ascii=False, indent=2))
        checkpoint()
        fetch = fetch or Collector().fetch
        cache = {}
        for kind in ('own', 'analogs'):
            history = data.setdefault('history', {}).setdefault(kind, [])
            # Process faster sources before the five-minute profsafe crawl delay.
            for row in sorted(data[kind], key=lambda r: r['site'] == 'profsafe.ru'):
                if args.limit and len(cache) >= args.limit and row['url'] not in cache:
                    continue
                url = row['url']
                if url not in cache:
                    try:
                        result = fetch(url)
                    except Exception as exc:
                        result = str(exc)[:350]
                    cache[url] = (result, datetime.now(timezone.utc).isoformat())
                result, checked = cache[url]
                stats = report['sources'].setdefault(row['site'], {'updated': 0, 'failed': 0})
                try:
                    if isinstance(result, str):
                        raise ValueError(result)
                    old = apply_result(row, result, checked, args.allow_large_change)
                    history.append(old)
                    report['updated'] += 1
                    stats['updated'] += 1
                except Exception as exc:
                    row['lastAttemptAt'] = checked
                    row['refreshError'] = str(exc)[:350]
                    # Preserve price AND checkedAt. Failure is not a new observation.
                    report['failed'] += 1
                    stats['failed'] += 1
                    report['errors'].append({'url': url, 'error': row['refreshError']})
                print(json.dumps({'site': row['site'], 'id': row['id'], 'error': row.get('refreshError')}, ensure_ascii=False), flush=True)
                checkpoint()
            # Keep one final observation per week and offer for two years.
            weekly = {}
            for old in history:
                stamp = datetime.fromisoformat(old['checkedAt'].replace('Z', '+00:00'))
                year, week, _ = stamp.astimezone(ZoneInfo('Europe/Moscow')).isocalendar()
                key = (old['url'], old.get('sku'), year, week)
                if key not in weekly or old['checkedAt'] > weekly[key]['checkedAt']:
                    weekly[key] = old
            history[:] = sorted(weekly.values(), key=lambda x: x['checkedAt'])
            history[:] = [x for x in history if (datetime.now(timezone.utc) - datetime.fromisoformat(x['checkedAt'].replace('Z', '+00:00'))).days < 735]
        report['running'] = False
        report['completedAt'] = datetime.now(timezone.utc).isoformat()
        checkpoint()
        for old in sorted((state / 'backups').glob('*.json'))[:-30]:
            old.unlink()
        print(json.dumps({k: v for k, v in report.items() if k != 'errors'}, ensure_ascii=False), flush=True)
        return 0 if report['updated'] else 2

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / 'runtime')
    parser.add_argument('--publish', type=Path, default=ROOT / 'public')
    parser.add_argument('--limit', type=int, default=0, help='Limit unique URLs for diagnostics')
    parser.add_argument('--allow-large-change', action='store_true')
    args = parser.parse_args()
    try:
        raise SystemExit(run(args))
    except BlockingIOError:
        raise SystemExit('Обновление уже выполняется')
