"""Persistent, sequential pacing and Retry-After handling; no automatic retry loops."""
import json
import random
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlsplit

class RateLimited(RuntimeError):
    pass

def retry_seconds(value, now):
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, dt.timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 0

class RequestPacing:
    def __init__(self, path=None):
        self.path = path
        self.state = json.loads(path.read_text(encoding='utf8')) if path and path.exists() else {}
        self.stopped = set()

    def save(self):
        if self.path:
            from build import atomic_write
            atomic_write(self.path, json.dumps(self.state, ensure_ascii=False, indent=2))

    def request(self, download, url, *args, **kwargs):
        host = urlsplit(url).hostname
        # Share limits between regional subdomains of this dealer.
        key = 'profsafe.ru' if host == 'profsafe.ru' or host.endswith('.profsafe.ru') else host
        entry = self.state.setdefault(key, {})
        now = time.time()
        if key in self.stopped or entry.get('retryAt', 0) > now:
            raise RateLimited('Источник приостановлен после HTTP 429; повтор не ранее ' + str(entry.get('retryAt')))
        delay = max(0, entry.get('lastRequest', 0) + random.uniform(15, 30) - now)
        if delay:
            time.sleep(delay)
        try:
            return download(url, *args, **kwargs)
        except HTTPError as exc:
            if exc.code == 429:
                count = entry.get('failures', 0) + 1
                wait = max(retry_seconds(exc.headers.get('Retry-After'), time.time()), min(86400, 900 * 2 ** min(count - 1, 7)))
                entry.update(failures=count, retryAfter=exc.headers.get('Retry-After'), retryAt=time.time() + wait)
                self.stopped.add(key)
                raise RateLimited('HTTP 429: источник остановлен; пауза минимум ' + str(round(wait)) + ' секунд') from exc
            raise
        finally:
            entry['lastRequest'] = time.time()
            self.save()
