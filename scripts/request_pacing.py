"""Persistent host limits, bounded transient retries and Retry-After handling."""
import json
import random
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
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
        self.metrics = {'requests': 0, 'retries': 0, 'failed': 0, 'seconds': 0}

    def save(self):
        if self.path:
            from build import atomic_write
            atomic_write(self.path, json.dumps(self.state, ensure_ascii=False, indent=2))

    def request(self, download, url, *args, min_interval=0, **kwargs):
        for attempt in range(3):
            try:
                return self._request(download, url, *args, min_interval=min_interval, **kwargs)
            except HTTPError as exc:
                if exc.code not in (408, 500, 502, 503, 504):
                    raise
                wait = retry_seconds(exc.headers.get('Retry-After'), time.time())
                # Persist long server cooldowns instead of tying up the worker.
                if wait > 60:
                    key = self.host_key(url)
                    self.state[key]['retryAt'] = time.time() + wait
                    self.stopped.add(key)
                    self.save()
                    raise RateLimited('Retry-After: источник временно приостановлен') from exc
                if attempt == 2:
                    # Keep even a short Retry-After for the next card/run.
                    if wait:
                        self.state[self.host_key(url)]['retryAt'] = time.time() + wait
                        self.save()
                    raise
            except (URLError, TimeoutError, ConnectionError):
                if attempt == 2:
                    raise
                wait = 0
            self.metrics['retries'] += 1
            time.sleep(max(wait, min(30, 2 ** attempt + random.uniform(0, 1))))

    @staticmethod
    def host_key(url):
        host = urlsplit(url).hostname
        if not host:
            raise ValueError('URL без имени сервера')
        host = host.removeprefix('www.')
        return 'profsafe.ru' if host == 'profsafe.ru' or host.endswith('.profsafe.ru') else host

    def _request(self, download, url, *args, min_interval=0, **kwargs):
        host = urlsplit(url).hostname
        # Share limits between regional subdomains of this dealer.
        key = self.host_key(url)
        entry = self.state.setdefault(key, {})
        now = time.time()
        if key in self.stopped or entry.get('retryAt', 0) > now:
            raise RateLimited('Источник приостановлен после HTTP 429; повтор не ранее ' + str(entry.get('retryAt')))
        delay = max(0, entry.get('lastRequest', 0) + max(min_interval, random.uniform(15, 30)) - now)
        if delay:
            time.sleep(delay)
        started = time.monotonic()
        self.metrics['requests'] += 1
        try:
            result = download(url, *args, **kwargs)
            entry.update(failures=0, retryAt=0)
            return result
        except HTTPError as exc:
            self.metrics['failed'] += 1
            exc.close()
            if exc.code == 429:
                count = entry.get('failures', 0) + 1
                wait = max(retry_seconds(exc.headers.get('Retry-After'), time.time()), min(86400, 900 * 2 ** min(count - 1, 7)))
                entry.update(failures=count, retryAfter=exc.headers.get('Retry-After'), retryAt=time.time() + wait)
                self.stopped.add(key)
                raise RateLimited('HTTP 429: источник остановлен; пауза минимум ' + str(round(wait)) + ' секунд') from exc
            raise
        except (URLError, TimeoutError, ConnectionError):
            self.metrics['failed'] += 1
            raise
        finally:
            self.metrics['seconds'] += time.monotonic() - started
            entry['lastRequest'] = time.time()
            self.save()
