import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from request_pacing import RequestPacing, RateLimited, retry_seconds

class PacingTests(unittest.TestCase):
    def test_retry_after_formats(self):
        self.assertEqual(retry_seconds('120', 0),120)
        self.assertEqual(retry_seconds('Thu, 01 Jan 1970 00:02:00 GMT',0),120)
        self.assertEqual(retry_seconds('bad',0),0)

    def test_stops_and_persists_across_regions_and_runs(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'pacing.json'
            calls=[]
            def blocked(url):
                calls.append(url)
                raise HTTPError(url,429,'limited',{'Retry-After':'3600'},None)
            with patch('request_pacing.time.time',return_value=10000):
                p=RequestPacing(path)
                with self.assertRaises(RateLimited):p.request(blocked,'https://spb.profsafe.ru/a')
                with self.assertRaises(RateLimited):p.request(blocked,'https://profsafe.ru/b')
                with self.assertRaises(RateLimited):RequestPacing(path).request(blocked,'https://spb.profsafe.ru/a')
                self.assertEqual(len(calls),1)
                self.assertEqual(p.state['profsafe.ru']['retryAt'],13600)

    def test_interval_and_increasing_backoff(self):
        p=RequestPacing()
        p.state['example.com']={'lastRequest':100,'failures':1}
        with patch('request_pacing.time.time',return_value=110),patch('request_pacing.random.uniform',return_value=20),patch('request_pacing.time.sleep') as sleep:
            def blocked(url):raise HTTPError(url,429,'limited',{},None)
            with self.assertRaises(RateLimited):p.request(blocked,'https://example.com/a')
            sleep.assert_called_once_with(10)
            self.assertEqual(p.state['example.com']['retryAt'],1910)
