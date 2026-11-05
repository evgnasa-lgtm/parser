"""Shared, conservative normalization. Missing prices are never imputed."""
import copy
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urljoin


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Некорректная цена')
    raw = re.sub(r'[\s\u00a0\u202f]', '', str(value)).replace(',', '.')
    if not re.fullmatch(r'\d+(?:\.\d+)?', raw):
        raise ValueError('Некорректная цена')
    try:
        amount = Decimal(raw).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError('Некорректная цена') from exc
    if not amount.is_finite() or not 0 < amount < 10_000_000:
        raise ValueError('Некорректная цена')
    return float(amount)


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def canonical_url(value):
    u = urlsplit(value.strip())
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('Некорректный URL товара')
    host = u.hostname.encode('idna').decode('ascii').lower()
    if u.port and u.port != {'http': 80, 'https': 443}[u.scheme]:
        host += ':' + str(u.port)
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'yclid')]
    return urlunsplit((u.scheme, host, u.path or '/', urlencode(sorted(query)), ''))


def same_page(first, second):
    # Keep host, region and variant query parameters; only tracking is irrelevant.
    return canonical_url(urljoin(second, first)) == canonical_url(second)


def observation_key(row):
    return (row.get('site'), canonical_url(row['url']), row.get('sku'))


def validate_registry(data):
    if not isinstance(data, dict):
        raise ValueError('Набор данных должен быть объектом')
    for kind in ('own', 'analogs'):
        if not isinstance(data.get(kind), list):
            raise ValueError('Нет списка ' + kind)
        identities = {}
        for row in data[kind]:
            if not isinstance(row, dict) or not row.get('site') or not row.get('url') or row.get('id') is None:
                raise ValueError('Карточка без id, site или url')
            identity = observation_key(row)
            for field in ('price', 'unavailablePrice', 'oldPrice', 'conditionalPrice'):
                if row.get(field) is not None:
                    number(row[field])
            previous = identities.get(identity)
            if previous is not None and previous != row:
                raise ValueError('Конфликтующие дубликаты карточки: ' + row['url'])
            identities[identity] = row
    for region in data.get('cities', {}).values():
        validate_registry(region)


def apply_result(row, result, now, allow_large=False):
    if not isinstance(result, dict):
        raise ValueError('Результат должен быть объектом')
    checked = timestamp(now)
    if row.get('checkedAt') and checked < timestamp(row['checkedAt']):
        raise ValueError('Устаревшее наблюдение')
    status = result.get('statusObserved')
    if status not in ('В наличии', 'Нет в наличии', 'Под заказ', 'Неизвестно'):
        raise ValueError('Некорректное наличие')
    normalized = {key: copy.deepcopy(value) for key, value in result.items()
                  if key in ('price', 'unavailablePrice', 'statusObserved', 'soldUnits',
                             'city', 'cityVerified', 'regionalSourceUrl')}
    for field in ('price', 'unavailablePrice'):
        normalized[field] = number(result[field]) if result.get(field) is not None else None
    if status == 'Нет в наличии':
        normalized['unavailablePrice'] = normalized['unavailablePrice'] or normalized['price']
        normalized['price'] = None
    elif normalized['price'] is None:
        raise ValueError('Цена не подтверждена')
    elif normalized['unavailablePrice'] is not None:
        raise ValueError('Противоречивые цены наличия')
    previous = row.get('price') or row.get('unavailablePrice')
    current = normalized['price'] or normalized['unavailablePrice']
    if previous and current and abs(current / number(previous) - 1) > .5 and not allow_large:
        raise ValueError('Изменение цены более 50%; требуется ручная проверка')
    sold = result.get('soldUnits')
    if sold is not None and (isinstance(sold, bool) or not isinstance(sold, int) or sold < 0):
        raise ValueError('Некорректный счётчик продаж')
    old = copy.deepcopy(row)
    # A missing counter is not a zero nor a new confirmed counter.
    if sold is None:
        normalized.pop('soldUnits', None)
    row.update(normalized)
    row.update(checkedAt=checked.isoformat(), lastAttemptAt=checked.isoformat(),
               refreshError=None, collectionError=None, lastAttemptStatus='verified',
               collectionMethod='Product/Offer structured data', availability=status,
               delivery='Автоматически не проверено', oldPrice=None, conditionalPrice=None,
               conditionalType='', promo='Акции автоматически не проверены',
               promoVerified=False, availabilityVerified=status != 'Неизвестно',
               priceMissing=normalized['price'] is None, soldUnitsVerified=sold is not None,
               conditions='Публичная цена из разметки карточки. Регион и условия доставки уточняйте у продавца.')
    return old


def compact_history(history):
    """Deduplicate exact snapshots, preserving every change, including within a week.

    No age-based destruction: retention requires a separately verified archive.
    """
    seen = set()
    result = []
    for row in history:
        key = json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def lock_run(lock):
    try:
        import fcntl
    except ImportError:
        import msvcrt
        lock.seek(0)
        lock.write('0')
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError('Обновление уже выполняется') from exc
    else:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
