import pathlib
import shutil
import subprocess
import unittest


class PriceEventsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_history_and_seven_day_filter(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const s=fs.readFileSync('src/app.js','utf8');
const now=Date.parse('2026-11-09T09:00:00Z'),day=86400000;
const rows=[10,8,6,3,1].map((age,i)=>({price:[100,110,90,95,95][i],checkedAt:new Date(now-age*day).toISOString(),statusObserved:'В наличии'}));
const c={state:{city:'Москва',eventType:'Изменения цен',eventDays:'7'},offers:[{key:'own-1',...rows.at(-1)}],samples:()=>rows,previous:()=>rows.at(-2),numeric:Number.isFinite,money:String,availability:()=> 'В наличии',Date:class extends Date{static now(){return now;}}};
vm.createContext(c);
vm.runInContext(s.slice(s.indexOf('let events=[];'),s.indexOf('function notifications('))+s.slice(s.indexOf('function isPriceEvent('),s.indexOf('function eventPage(')),c);
vm.runInContext('refreshEvents()',c);
assert.equal(c.priceEventsLastWeek().length,2); // Both historical changes, not just the unchanged latest pair.
assert.equal(c.filteredEvents().length,2);
assert.equal(c.filteredEvents()[0].type,'Рост цены');
assert.equal(c.inLastWeek({offer:{checkedAt:new Date(now-7*day).toISOString()}}),true);
assert.equal(c.inLastWeek({offer:{checkedAt:new Date(now-7*day-1).toISOString()}}),false);
assert.equal(c.inLastWeek({offer:{checkedAt:new Date(now+1).toISOString()}}),false);
c.state.eventDays='all';assert.equal(c.filteredEvents().length,3);
c.state.eventType='Снижение цены';assert.equal(c.filteredEvents().length,1);
'''
        subprocess.run(['node', '-e', script], cwd=root, check=True, capture_output=True, text=True)
