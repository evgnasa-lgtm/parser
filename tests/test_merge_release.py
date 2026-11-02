from datetime import datetime, timedelta
import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from merge_release import merge_release
from build import ROOT, render

class MergeTests(unittest.TestCase):
    def setUp(self):
        self.seed = json.loads((ROOT/'data/observations.json').read_text(encoding='utf-8'))

    def test_idempotent_and_no_mutation(self):
        original = copy.deepcopy(self.seed)
        merged = merge_release(self.seed, self.seed)
        self.assertEqual(merged, merge_release(self.seed, merged))
        self.assertEqual(self.seed, original)
        self.assertEqual(len(merged['own']) + len(merged['analogs']), 170)

    def test_newer_runtime_wins_and_release_additions_survive(self):
        runtime = copy.deepcopy(self.seed)
        runtime.pop('manufacturerPrices')
        runtime['analogs'] = runtime['analogs'][:-2]
        newer = (datetime.fromisoformat(self.seed['own'][0]['checkedAt'].replace('Z', '+00:00')) + timedelta(days=1)).isoformat()
        runtime['own'][0].update(price=15000, checkedAt=newer)
        merged = merge_release(self.seed, runtime)
        self.assertEqual(merged['own'][0]['price'], 15000)
        self.assertEqual(len(merged['manufacturerPrices']), 13)
        self.assertEqual(len(merged['analogs']), 122)
        self.assertTrue(any(x.get('price') == self.seed['own'][0]['price'] for x in merged['history']['own']))

    def test_newer_failure_keeps_successful_price(self):
        runtime = copy.deepcopy(self.seed)
        runtime['own'][0].update(checkedAt='2026-10-20T00:00:00Z',price=1,lastAttemptAt='2099-11-01T00:00:00Z',refreshError='HTTP 403')
        merged=merge_release(self.seed,runtime)
        self.assertEqual(merged['own'][0]['price'],self.seed['own'][0]['price'])
        self.assertEqual(merged['own'][0]['refreshError'],'HTTP 403')

    def test_city_release_added_to_legacy_runtime(self):
        runtime = copy.deepcopy(self.seed)
        runtime.pop('cities')
        merged = merge_release(self.seed, runtime)
        self.assertEqual(merged['cities']['Санкт-Петербург'], self.seed['cities']['Санкт-Петербург'])

    def test_newer_city_runtime_survives_without_changing_moscow(self):
        runtime = copy.deepcopy(self.seed)
        runtime['cities']['Санкт-Петербург']['own'][0].update(price=12345, checkedAt='2099-01-01T00:00:00Z')
        merged = merge_release(self.seed, runtime)
        self.assertEqual(merged['cities']['Санкт-Петербург']['own'][0]['price'], 12345)
        self.assertEqual(merged['own'][0]['price'], self.seed['own'][0]['price'])

    def test_secure_build_and_latest_ui(self):
        self.seed['own'][0]['model']='</script><script>alert(1)</script>'
        page=render(self.seed)
        self.assertIn('\\u003c/script',page)
        self.assertNotIn('&lt;/script&gt;&lt;script&gt;alert(1)',page)
        self.assertIn('sandbox="allow-scripts allow-downloads"',page)
        self.assertIn('manufacturer-price',page)

if __name__=='__main__': unittest.main()
