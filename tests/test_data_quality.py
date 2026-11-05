import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import closing
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from data_quality import number, same_page, canonical_url, apply_result, compact_history, timestamp, validate_registry
from refresh_prices import parse_product
from monitoring import archive, Checkpoint
from request_pacing import RequestPacing, RateLimited
from merge_release import merge_release

URL = 'https://profsafe.ru/p/1/'
NOW = '2026-11-05T12:00:00+00:00'


class QualityTests(unittest.TestCase):
    def test_decimal_rounding_and_spaces(self):
        self.assertEqual(number('12\u202f030,505'), 12030.51)
        for value in (False, None, 'NaN', 'inf', '-1', '1e3', 0, '0.001'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value)

    def test_product_identity_keeps_host_region_variant(self):
        self.assertTrue(same_page('/p/1/', URL))
        self.assertFalse(same_page('https://evil.test/p/1/', URL))
        self.assertFalse(same_page(URL + '?variant=2', URL))
        self.assertFalse(same_page('https://spb.profsafe.ru/p/1/', URL))
        self.assertEqual(canonical_url(URL + '?utm_source=x#top'), URL)

    def test_explicit_other_product_rejected(self):
        source = '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', 'url': 'https://evil.test/p/1/',
            'offers': {'price': 100, 'priceCurrency': 'RUB', 'availability': 'https://schema.org/InStock'}}) + '</script>'
        with self.assertRaises(ValueError):
            parse_product(source, URL)

    def test_out_of_stock_without_price_is_valid(self):
        source = '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', 'url': URL,
            'offers': {'priceCurrency': 'RUB', 'availability': 'https://schema.org/OutOfStock'}}) + '</script>'
        result = parse_product(source, URL)
        row = {'price': 100}
        apply_result(row, result, NOW)
        self.assertIsNone(row['price'])
        self.assertTrue(row['priceMissing'])
        self.assertEqual(row['availability'], 'Нет в наличии')

    def test_invalid_result_is_atomic(self):
        row = {'price': 100, 'checkedAt': NOW}
        for result in ({'price': -1, 'statusObserved': 'В наличии'},
                       {'price': 110, 'statusObserved': 'invalid'},
                       {'price': 110, 'statusObserved': 'В наличии', 'soldUnits': -1}):
            before = copy.deepcopy(row)
            with self.assertRaises(ValueError):
                apply_result(row, result, NOW)
            self.assertEqual(row, before)

    def test_result_cannot_replace_product_identity(self):
        row = {'id': 1, 'url': URL, 'sku': 'A', 'price': 100}
        apply_result(row, {'price': 110, 'statusObserved': 'В наличии', 'id': 2, 'sku': 'B'}, NOW)
        self.assertEqual((row['id'], row['sku']), (1, 'A'))

    def test_missing_counter_and_unverified_promotions(self):
        row = {'price': 100, 'soldUnits': 42, 'oldPrice': 130, 'conditionalPrice': 90}
        apply_result(row, {'price': 110, 'statusObserved': 'В наличии'}, NOW)
        self.assertEqual(row['soldUnits'], 42)
        self.assertFalse(row['soldUnitsVerified'])
        self.assertIsNone(row['oldPrice'])
        self.assertIsNone(row['conditionalPrice'])
        self.assertFalse(row['promoVerified'])

    def test_stale_result_rejected(self):
        with self.assertRaises(ValueError):
            apply_result({'price': 100, 'checkedAt': NOW}, {'price': 100, 'statusObserved': 'В наличии'}, '2026-11-04')

    def test_timestamp_normalizes_timezone(self):
        self.assertEqual(timestamp('2026-11-05T15:00:00+03:00'), timestamp(NOW))
        self.assertEqual(timestamp('2026-11-05T12:00:00'), timestamp(NOW))

    def test_history_keeps_every_change_and_is_idempotent(self):
        a = {'url': URL, 'price': 100, 'checkedAt': NOW}
        b = dict(a, price=110, checkedAt='2026-11-05T13:00:00Z')
        self.assertEqual(compact_history([a, b, a]), [a, b])
        self.assertEqual(compact_history(compact_history([a, b, a])), [a, b])

    def test_registry_rejects_missing_and_conflicting_rows(self):
        with self.assertRaises(ValueError):
            validate_registry({'own': [{}], 'analogs': []})
        row = {'id': 1, 'url': URL, 'site': 'test', 'price': 100}
        with self.assertRaises(ValueError):
            validate_registry({'own': [row, dict(row, price=200)], 'analogs': []})

    def test_distinct_sku_not_collapsed(self):
        row = {'id': 1, 'site': 'test', 'url': URL, 'sku': 'A'}
        data = {'own': [row, dict(row, id=2, sku='B')], 'analogs': []}
        self.assertEqual(len(merge_release(data, data)['own']), 2)

    def test_archive_idempotent_and_city_isolated(self):
        row = {'site': 'test', 'url': URL, 'price': 100, 'checkedAt': NOW}
        data = {'own': [row], 'history': {'own': [row]}, 'cities': {'Санкт-Петербург': {'own': [row]}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'audit.sqlite3'
            archive(path, data)
            archive(path, data)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 2)
                self.assertGreaterEqual(len(db.execute('PRAGMA index_list(observations)').fetchall()), 3)

    def test_checkpoints_batch_render_but_persist_each_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            render = Mock(return_value='page')
            report = {'running': True, 'updated': 0}
            data = {'own': [], 'analogs': []}
            checkpoint = Checkpoint(state, state / 'public', render, data, report)
            for n in range(10):
                report['updated'] = n
                checkpoint()
            self.assertEqual(render.call_count, 1)
            self.assertEqual(json.loads((state / 'last-refresh.json').read_text())['updated'], 9)
            report['running'] = False
            checkpoint()
            self.assertEqual(render.call_count, 2)
            self.assertIn('np_refresh_updated 9.0', (state / 'metrics.prom').read_text())


class RetryTests(unittest.TestCase):
    @patch('request_pacing.time.sleep')
    def test_transient_retries_are_bounded(self, sleep):
        fetch = Mock(side_effect=URLError('temporary'))
        p = RequestPacing()
        with self.assertRaises(URLError):
            p.request(fetch, URL)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(p.metrics['retries'], 2)

    @patch('request_pacing.time.sleep')
    def test_503_recovers(self, sleep):
        fetch = Mock(side_effect=[HTTPError(URL, 503, 'busy', {}, None), 'ok'])
        self.assertEqual(RequestPacing().request(fetch, URL), 'ok')
        self.assertEqual(fetch.call_count, 2)

    @patch('request_pacing.time.sleep')
    def test_permanent_error_not_retried(self, sleep):
        fetch = Mock(side_effect=HTTPError(URL, 403, 'blocked', {}, None))
        with self.assertRaises(HTTPError):
            RequestPacing().request(fetch, URL)
        self.assertEqual(fetch.call_count, 1)

    @patch('request_pacing.time.sleep')
    def test_long_retry_after_persisted(self, sleep):
        p = RequestPacing()
        fetch = Mock(side_effect=HTTPError(URL, 503, 'busy', {'Retry-After': '7200'}, None))
        with self.assertRaises(RateLimited):
            p.request(fetch, URL)
        with self.assertRaises(RateLimited):
            p.request(fetch, URL)
        self.assertEqual(fetch.call_count, 1)

    def test_retry_respects_crawl_delay(self):
        p = RequestPacing()
        p.state['profsafe.ru'] = {'lastRequest': 100}
        with patch('request_pacing.time.time', return_value=110), patch('request_pacing.time.sleep') as sleep:
            p.request(lambda _: 'ok', URL, min_interval=300)
            sleep.assert_called_once_with(290)
