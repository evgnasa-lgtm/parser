"""Exercise production category selection and dealer controls without a browser."""
import pathlib
import shutil
import subprocess
import unittest

class CatalogFilterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_categories_search_dealers_and_navigation(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('src/app.js','utf8'),data=JSON.parse(fs.readFileSync('data/observations.json','utf8'));
const sites=['КОМУС','ВсеИнструменты','Железная мебель','profsafe.ru','dimaxmet.ru','evrosafe.ru'];
const c={D:data,sites,state:{city:'Санкт-Петербург',dealers:sites.slice()},defaults:{},offers:[],esc:s=>String(s??''),money:String,siteName:s=>s,manufacturerPrice:()=>'',title:()=>'',persist:()=>{},render:()=>{},bindPickers:()=>{}};
vm.createContext(c);
vm.runInContext(src.slice(src.indexOf('function modelCategory('),src.indexOf('\nfunction ',src.indexOf('function catalog()')+1)),c);
assert.equal(c.categoryModels(data.selected,'archive').length,3);
assert.equal(c.categoryModels(data.selected,'lockers').length,10);
assert.equal(c.categoryModels(data.selected,'filing').length,2);
assert.equal(c.categoryModels(data.selected,'all').length,15);
c.state.dealers=['КОМУС'];c.setCatalogCategory('filing');
assert.equal(c.state.city,'Санкт-Петербург');assert.deepEqual(c.state.dealers,['КОМУС']);
assert.equal(c.state.catalogModels.length,2);
assert.equal(c.catalogSelection(data.selected,'filing',null,' afc-04 ').length,1);
assert.equal(c.catalogSelection(data.selected,'filing',null,'S20299050102')[0].models,'AFC-05');
assert.equal(c.catalogSelection(data.selected,'filing',[],'').length,0);
assert.equal(c.catalogSelection(data.selected,'filing',null,'CB-12').length,0);
c.state.catalogModels=[];c.state.catalogSearch='missing';c.setCatalogCategory('archive');
assert.equal(c.state.catalogModels.length,3);assert.equal(c.state.catalogSearch,'');
c.selectDealer('КОМУС');assert.equal(c.state.dealers.length,0);
c.selectDealer('evrosafe.ru');c.selectDealer('ВсеИнструменты');assert.equal(c.state.dealers.length,2);
c.selectDealer('profsafe.ru',true);assert.equal(c.state.dealers.join(','),'profsafe.ru');
assert(c.dealerFilter().includes('aria-pressed="true"'));assert(c.dealerFilter().includes('Только эта'));
c.setCatalogCategory('filing');const sku=c.state.catalogModels[0];
c.offers=[{kind:'own',skus:[sku],site:'profsafe.ru',price:123,key:'shown'},{kind:'own',skus:[sku],site:'КОМУС',price:987,key:'hidden'}];
const html=c.catalog();assert(html.includes('data-offer="shown"'));assert(!html.includes('data-offer="hidden"'));assert(!html.includes('<h2>CB-12</h2>'));
c.offers=[{price:14155,key:'expensive'},{price:null,key:'missing'},{price:12650,key:'low1'},{price:12850,key:'middle'},{price:12650,key:'low2'}].map(o=>({...o,kind:'own',skus:[sku],site:'profsafe.ru'}));
const ordered=c.catalog();
const keys=[...ordered.matchAll(/data-offer="([^"]+)"/g)].map(m=>m[1]);
assert.deepEqual(keys,['low1','low2','middle','expensive','missing']);
assert.equal(c.offers[0].key,'expensive');
assert.equal(c.catalogPrice({price:0}),Infinity);
const compare={dataset:{compareModel:sku}},category={dataset:{catalogCategory:'lockers'}},city={};
c.$=selector=>selector==='#city'?city:null;
c.all=selector=>selector==='[data-compare-model]'?[compare]:selector==='[data-catalog-category]'?[category]:[];
vm.runInContext(src.slice(src.indexOf('function bindCatalogFilters('),src.indexOf('function openDetail(')),c);
c.bind();compare.onclick();
assert.equal(c.state.page,'compare');assert.equal(c.state.model,sku);assert.equal(c.state.city,'Санкт-Петербург');assert.equal(c.state.dealers.join(','),'profsafe.ru');assert.equal(c.state.catalogCategory,'filing');
category.onclick();assert.equal(c.state.catalogModels.length,10);assert.equal(c.state.dealers.join(','),'profsafe.ru');
'''
        subprocess.run(['node','-e',script],cwd=pathlib.Path(__file__).resolve().parents[1],check=True,capture_output=True,text=True)
