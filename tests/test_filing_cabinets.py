import copy
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from merge_release import merge_release
from data_quality import validate_registry

class FilingTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((ROOT/'data/observations.json').read_text(encoding='utf-8'))
        self.skus={'S20299040102','S20299050102'}

    def test_registry_and_pending_city_observations(self):
        validate_registry(self.data)
        models=[m for m in self.data['selected'] if m['sku'] in self.skus]
        self.assertEqual(len(models),2)
        self.assertEqual({m['specification']['drawers'] for m in models},{4,5})
        for city,r in [('Москва',self.data),*self.data['cities'].items()]:
            own=[o for o in r['own'] if o.get('sku') in self.skus]
            self.assertEqual(len(own),7)
            for o in own:
                self.assertEqual(o['city'],city)
                self.assertIsNone(o['price'])
                self.assertIsNone(o['checkedAt'])
                self.assertFalse(o['cityVerified'])
            analogs=[o for o in r['analogs'] if self.skus.intersection(o['skus'])]
            self.assertEqual(len(analogs),7)
            for o in analogs:
                self.assertTrue(any(p['model']==o['model'] and p['sku'] in o['skus'] for p in self.data['pairs']))

    def test_old_runtime_receives_new_models_without_history_changes(self):
        runtime=copy.deepcopy(self.data)
        runtime['selected']=[m for m in runtime['selected'] if m['sku'] not in self.skus]
        runtime['pairs']=[p for p in runtime['pairs'] if p['sku'] not in self.skus]
        for r in [runtime,*runtime['cities'].values()]:
            r['own']=[o for o in r['own'] if o.get('sku') not in self.skus]
            r['analogs']=[o for o in r['analogs'] if not self.skus.intersection(o['skus'])]
        merged=merge_release(self.data,runtime)
        self.assertEqual(merged['selected'],self.data['selected'])
        self.assertEqual(merged['history'],runtime['history'])
        self.assertEqual(merged['cities']['Санкт-Петербург']['history'],runtime['cities']['Санкт-Петербург']['history'])
        self.assertEqual(len(merged['own']),len(self.data['own']))

    def test_fs_is_not_inferred_from_a4(self):
        k=json.loads((ROOT/'data/filing-cabinets.json').read_text(encoding='utf-8'))
        p={x['brand']+' '+x['model']:x for x in k['products']}
        self.assertEqual(p['ПРОМЕТ AFC-04']['suspended_fs'],'confirmed')
        self.assertEqual(p['ROMMEL ШК-4']['suspended_fs'],'confirmed')
        self.assertEqual(p['Церера КО-41Т']['suspended_fs'],'unconfirmed')
