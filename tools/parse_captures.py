import json,re,sys
if len(sys.argv)!=3: raise SystemExit("Usage: python tools/parse_captures.py input.json output.json")
from pathlib import Path
rows=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
def norm(s): return s.replace('\xa0',' ').replace('\u202f',' ')
for r in rows:
 t=norm(r.get('text',''))
 if r['site']=='ВсеИнструменты':
  ends=[t.find(x) for x in ['ОПИСАНИЕ И ХАРАКТЕРИСТИКИ','Аналоги в наличии','\nАналоги\n','Сопутствующие товары'] if t.find(x)>0]; b=t[:min(ends)] if ends else t
 elif r['site']=='Железная мебель':
  start=t.find('Код товара:'); end=t.find('Характеристики и описание');b=t[max(0,start-130):end] if end>0 else t
 elif r['site']=='profsafe.ru':b=t[:t.find('Поделиться')] if 'Поделиться' in t else t
 else:b=t[:t.find('Похожие товары')] if 'Похожие товары' in t else t
 r['evidence']=b
 r['money']=re.findall(r'\d[\d .]*(?:₽|руб\.)',b)
 r['availabilityLines']=[l.strip() for l in b.splitlines() if re.search(r'наличии|складе|поставщика|магазинах сегодня|шт\.|Самовывоз|Курьером|Завтра',l)]
 r['dimensions']=re.findall(r'\d{3,4}\s*[хx×]\s*\d{3,4}\s*[хx×]\s*\d{3,4}',t[:6000])[:3]
Path(sys.argv[2]).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
for r in rows:print(json.dumps({k:r.get(k) for k in ['id','model','money','availabilityLines','dimensions']},ensure_ascii=False))
