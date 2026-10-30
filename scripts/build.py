"""Build the standalone dashboard. Python 3.10+, standard library only."""
from pathlib import Path
import html
import json
import os
import tempfile
import argparse

ROOT = Path(__file__).resolve().parents[1]

def render(data):
    assert all(key in data for key in ('selected', 'own', 'analogs', 'pairs'))
    skus = {r['sku'] for r in data['selected']}
    assert all(set(r['skus']) <= skus for r in data['analogs'])
    import re
    def encoded(value):
        return json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')
    legacy = (ROOT / 'src/legacy.html').read_text(encoding='utf-8')
    legacy = re.sub(r'(<script type="application/json" id="pm-data">).*?(</script>)',
                    lambda m: m[1] + encoded(data) + m[2], legacy, flags=re.S)
    fragment = (ROOT / 'src/template.html').read_text(encoding='utf-8')
    fragment = fragment.split('<body>', 1)[1].rsplit('</body>', 1)[0]
    fragment = fragment.replace('@@STYLE@@', (ROOT / 'src/style.css').read_text(encoding='utf-8'))
    fragment = fragment.replace('@@APP@@', (ROOT / 'src/app.js').read_text(encoding='utf-8'))
    fragment = fragment.replace('@@DATA@@', encoded(data)).replace('@@LEGACY@@', encoded(legacy))
    wrapper = (ROOT / 'src/wrapper.html').read_text(encoding='utf-8')
    assert wrapper.count('DASHBOARD_FRAGMENT_PLACEHOLDER') == 1
    result = wrapper.replace('DASHBOARD_FRAGMENT_PLACEHOLDER', html.escape(fragment, quote=True))
    assert 'sandbox="allow-scripts allow-downloads"' in result
    assert 'Content-Security-Policy' in result
    return result

def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def build(data_path=ROOT / 'data/observations.json', output=ROOT / 'public/index.html'):
    from merge_release import merge_release
    data = json.loads(Path(data_path).read_text(encoding='utf-8'))
    seed = json.loads((ROOT / 'data/observations.json').read_text(encoding='utf-8'))
    result = render(merge_release(seed, data))
    atomic_write(output, result)
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=ROOT / 'data/observations.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'public/index.html')
    args = parser.parse_args()
    build(args.data, args.output)
    print('Built', args.output)
