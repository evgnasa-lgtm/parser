"""Build the standalone dashboard. Python 3.10+, standard library only."""
from pathlib import Path
import html
import json
import re

ROOT = Path(__file__).resolve().parents[1]

def build():
    data = json.loads((ROOT / 'data/observations.json').read_text(encoding='utf-8'))
    assert all(key in data for key in ('selected', 'own', 'analogs', 'pairs'))
    skus = {r['sku'] for r in data['selected']}
    assert all(set(r['skus']) <= skus for r in data['analogs'])
    fragment = (ROOT / 'src/dashboard.html').read_text(encoding='utf-8')
    assert fragment.count('DATA_PLACEHOLDER') == 1
    fragment = fragment.replace('DATA_PLACEHOLDER', json.dumps(data, ensure_ascii=False).replace('</', '<\\/'))
    wrapper = (ROOT / 'src/wrapper.html').read_text(encoding='utf-8')
    assert wrapper.count('DASHBOARD_FRAGMENT_PLACEHOLDER') == 1
    result = wrapper.replace('DASHBOARD_FRAGMENT_PLACEHOLDER', html.escape(fragment, quote=True))
    assert 'sandbox="allow-scripts allow-downloads"' in result
    assert 'Content-Security-Policy' in result
    output = ROOT / 'public'
    output.mkdir(exist_ok=True)
    (output / 'index.html').write_text(result, encoding='utf-8')
    return result

if __name__ == '__main__':
    build()
    print('Built public/index.html')
