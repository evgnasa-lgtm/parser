"""Regional isolation tests; synthetic fixtures never enter observations.json."""
import argparse
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from refresh_regions import run, Collector, regional_adapter

class RegionsTests(unittest.TestCase):
    def execute(self, fetch, cities=None, limit=0):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            row={'id':1,'site':'КОМУС','url':'https://www.komus.ru/p/1/','price':100,'checkedAt':'2026-11-01T00:00:00Z'}
            region={'own':[copy.deepcopy(row)],'analogs':[], 'history':{},'selected':[], 'pairs':[]}
            seed=copy.deepcopy(region);seed['cities']={'Санкт-Петербург':copy.deepcopy(region)}
            (root/'data').mkdir();(root/'data/observations.json').write_text(json.dumps(seed),encoding='utf-8')
            args=argparse.Namespace(state=root/'runtime',publish=root/'public',limit=limit,allow_large_change=False,cities=cities)
            with patch('refresh_regions.ROOT',root),patch('refresh_regions.render',return_value='test'),redirect_stdout(io.StringIO()):
                code=run(args,fetch)
            return code,json.loads((args.state/'observations.json').read_text(encoding='utf-8'))

    def test_same_url_has_separate_city_requests_and_prices(self):
        calls=[]
        def fetch(url,city):
            calls.append((url,city));return dict(price=110 if city=='Москва' else 120,statusObserved='В наличии',city=city,cityVerified=True)
        code,data=self.execute(fetch)
        self.assertEqual(code,0);self.assertEqual(len(calls),2)
        self.assertEqual(data['own'][0]['price'],110)
        self.assertEqual(data['cities']['Санкт-Петербург']['own'][0]['price'],120)
        self.assertEqual(data['refreshSummary']['updated'],2)

    def test_wrong_city_does_not_overwrite_last_price_or_history(self):
        code,data=self.execute(lambda url,city:dict(price=110,statusObserved='В наличии',city='Москва',cityVerified=True))
        self.assertEqual(code,3)
        spb=data['cities']['Санкт-Петербург']
        self.assertEqual(spb['own'][0]['price'],100)
        self.assertEqual(spb['own'][0]['checkedAt'],'2026-11-01T00:00:00Z')
        self.assertEqual(spb['history']['own'],[])
        self.assertEqual(spb['refresh']['failed'],1)

    def test_selected_city_only(self):
        code,data=self.execute(lambda url,city:dict(price=120,statusObserved='В наличии',city=city,cityVerified=True),['Санкт-Петербург'])
        self.assertEqual(code,0);self.assertEqual(data['own'][0]['price'],100)
        self.assertEqual(list(data['refreshSummary']['cities']),['Санкт-Петербург'])

    def test_limit_not_claimed_complete(self):
        code,data=self.execute(lambda url,city:dict(price=110,statusObserved='В наличии',city=city,cityVerified=True),limit=1)
        self.assertEqual(code,3);self.assertEqual(data['refreshSummary']['skipped'],1)
        self.assertFalse(data['refreshSummary']['complete'])

    def test_unconfigured_adapter_does_not_guess_region(self):
        with self.assertRaisesRegex(ValueError,'Регион не настроен'):
            Collector().fetch('https://www.vseinstrumenti.ru/product/1/','Санкт-Петербург')

    def test_confirmed_region_adapters(self):
        import re
        for city in ('Москва','Санкт-Петербург'):
            komus=regional_adapter('www.komus.ru',city)
            self.assertTrue(komus['initUrl'].endswith('77' if city=='Москва' else '78'))
            markup='<div class="gtm-current-region"><div><span class="b-enterAccount__link">'+city+'</span></div>'
            self.assertEqual(re.search(komus['cityPattern'],markup).group('city'),city)
            prof=regional_adapter('profsafe.ru',city)
            self.assertEqual(prof['host'],'profsafe.ru' if city=='Москва' else 'spb.profsafe.ru')
            self.assertEqual(re.search(prof['cityPattern'],'<div class="js_city_chooser"><span>'+city+'</span>').group('city'),city)
            self.assertIsNone(re.search(prof['cityPattern'],'<a>'+city+'</a>'))

if __name__=='__main__':unittest.main()
