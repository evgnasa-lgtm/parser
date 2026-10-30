import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from refresh_prices import parse_product, apply_result, validate_url, run
from build import ROOT, render

URL = 'https://profsafe.ru/product/test/'

def product(price='12030', status='InStock', currency='RUB'):
    return f'''<div itemscope itemtype="https://schema.org/Product">
    <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
    <meta itemprop="price" content="{price}"><meta itemprop="priceCurrency" content="{currency}">
    <link itemprop="availability" href="https://schema.org/{status}"></div></div>'''

class RefreshTests(unittest.TestCase):
    def test_meta_offer(self):
        result = parse_product(product('12 030,50'), URL)
        self.assertEqual(result['price'], 12030.5)

    def test_unavailable_price_is_not_in_stock(self):
        result = parse_product(product(status='OutOfStock'), URL)
        self.assertIsNone(result['price'])
        self.assertEqual(result['unavailablePrice'], 12030)

    def test_preorder(self):
        self.assertEqual(parse_product(product(status='PreOrder'), URL)['statusObserved'], 'Под заказ')

    def test_related_products_are_not_a_price(self):
        with self.assertRaises(ValueError):
            parse_product(product('100') + product('200'), URL)

    def test_catalog_and_challenge_are_not_observations(self):
        for source in ('<h1>Captcha</h1>Цена 100 рублей', '<meta itemprop="price" content="100">'):
            with self.assertRaises(ValueError):
                parse_product(source, URL)

    def test_bad_price_or_currency(self):
        for source in (product('0'), product('-100'), product('nan'), product(currency='USD')):
            with self.assertRaises(ValueError):
                parse_product(source, URL)

    def test_jsonld(self):
        source = '<script type="application/ld+json">' + json.dumps({'@type': 'Product', 'url': URL,
            'offers': {'@type': 'Offer', 'price': 12030, 'priceCurrency': 'RUB',
                       'availability': 'https://schema.org/InStock'}}) + '</script>'
        self.assertEqual(parse_product(source, URL)['price'], 12030)

    def test_ssrf_not_allowed(self):
        for url in ('http://profsafe.ru/', 'https://127.0.0.1/', 'https://profsafe.ru:8443/',
                    'https://profsafe.ru.evil.test/', 'https://user@profsafe.ru/'):
            with self.assertRaises(ValueError):
                validate_url(url)

    def test_large_change_does_not_mutate_row(self):
        row = {'price': 100, 'checkedAt': '2026-10-28T00:00:00Z'}
        before = copy.deepcopy(row)
        with self.assertRaises(ValueError):
            apply_result(row, parse_product(product('1000'), URL), '2026-10-30T00:00:00Z')
        self.assertEqual(row, before)

    def test_render_escapes_script_ending(self):
        data = json.loads((ROOT / 'data/observations.json').read_text())
        data['own'][0]['model'] = '</script><script>alert(1)</script>'
        page = render(data)
        self.assertNotIn('&lt;/script&gt;&lt;script&gt;alert(1)', page)
        self.assertIn('\\u003c/script', page)

    def test_failed_refresh_keeps_price_and_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(state=Path(tmp) / 'state', publish=Path(tmp) / 'public', limit=1, allow_large_change=False)
            baseline = json.loads((ROOT / 'data/observations.json').read_text())
            def fail(url): raise ValueError('HTTP 403')
            self.assertEqual(run(args, fail), 2)
            data = json.loads((args.state / 'observations.json').read_text())
            self.assertEqual(data['own'][0]['price'], baseline['own'][0]['price'])
            self.assertEqual(data['own'][0]['checkedAt'], baseline['own'][0]['checkedAt'])
            self.assertEqual(data['history']['own'], baseline['history']['own'])
            self.assertIn('HTTP 403', data['own'][0]['refreshError'])
            self.assertTrue((args.publish / 'index.html').exists())

    def test_success_archives_before_updating_and_reuses_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(state=Path(tmp) / 'state', publish=Path(tmp) / 'public', limit=1, allow_large_change=False)
            result = parse_product(product('13000'), URL)
            self.assertEqual(run(args, lambda _: result), 0)
            first = json.loads((args.state / 'observations.json').read_text())
            self.assertEqual(first['own'][0]['price'], 13000)
            self.assertTrue(any(x['price'] == json.loads((ROOT / 'data/observations.json').read_text())['own'][0]['price'] for x in first['history']['own']))
            self.assertEqual(run(args, lambda _: dict(result, price=14000)), 0)
            second = json.loads((args.state / 'observations.json').read_text())
            self.assertEqual(second['own'][0]['price'], 14000)
            self.assertTrue(any(x['price'] == 13000 for x in second['history']['own']))

if __name__ == '__main__': unittest.main()
