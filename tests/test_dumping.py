"""Check historical dealer ranking with JavaScript's production implementation."""
import pathlib
import shutil
import subprocess
import unittest


class DumpingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required for UI calculation')
    def test_historical_minima_and_exclusions(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('src/app.js','utf8');
const code=source.slice(source.indexOf('function dumpingRanking('),source.indexOf('function dumpingPage('));
const c={};vm.createContext(c);vm.runInContext(code,c);
const row=(sku,site,price,extra={})=>({sku,site,price,statusObserved:'В наличии',checkedAt:'2026-11-05T10:00:00Z',...extra});
const data={selected:['a','b','c','d'].map(sku=>({sku,models:sku})),own:[row('a','X',130),row('a','Y',110),row('b','X',80),row('b','Y',80),row('c','X',20),row('a','Z',1,{refreshError:'failed'}),row('a','Z',2,{statusObserved:'Нет в наличии'}),row('a','Z',null,{conditionalPrice:1}),row('a','Z',3,{checkedAt:'2099-01-01'}),row('outside','Z',1)],history:{own:[row('a','X',100),row('a','X',100)]}};
const result=c.dumpingRanking(data,Date.parse('2026-11-09'));
assert.equal(result.total,4);assert.equal(result.comparable,2);
const x=result.dealers.find(d=>d.site==='X'),y=result.dealers.find(d=>d.site==='Y');
assert.equal(x.count,2);assert.equal(x.percent,50);assert.equal(x.covered,3);
assert.equal(y.count,1);assert.equal(y.percent,25);
assert.equal(x.wins.find(w=>w.sku==='a').price,100);
assert(x.wins.find(w=>w.sku==='b').tied);
assert.equal(result.dealers.find(d=>d.site==='Z').count,0);
const other=c.dumpingRanking({selected:data.selected,own:[row('a','Y',50),row('a','X',60)],history:{}},Date.parse('2026-11-09'));
assert.equal(other.dealers[0].site,'Y');
assert.equal(c.dumpingRanking({selected:[],own:[],history:{}}).total,0);
'''
        subprocess.run(['node', '-e', script], cwd=root, check=True, capture_output=True, text=True)
