#!/bin/bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Запустите через sudo'; exit 1; }
python3 - <<'PY'
import getpass, grp, os, re, subprocess, tempfile
from datetime import datetime
from pathlib import Path

user = 'MEBELPROMET'
config = Path('/etc/nginx/sites-available/pars.promet.pw')
if not config.is_file():
    raise SystemExit('Не найдена конфигурация pars.promet.pw')
original = config.read_text()
pattern = r'(^[ \t]*server_name[ \t]+pars\.promet\.pw[ \t]*;[^\n]*$)'
if not re.search(pattern, original, re.M):
    raise SystemExit('Остановлено: проверьте server_name в конфигурации')
password = getpass.getpass('Введите пароль для MEBELPROMET: ')
if not password or password != getpass.getpass('Повторите пароль: '):
    raise SystemExit('Пароли пусты или не совпадают; изменения не внесены')
hashed = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=password+'\n',
                        text=True, capture_output=True, check=True).stdout.strip()
password = None
credential = Path('/etc/nginx/pars.promet.pw.htpasswd')
previous = credential.read_bytes() if credential.exists() else None
fd, tmp = tempfile.mkstemp(dir='/etc/nginx', prefix='.pars-pass-')
try:
    os.fchmod(fd, 0o640)
    os.fchown(fd, 0, grp.getgrnam('www-data').gr_gid)
    with os.fdopen(fd, 'w') as stream:
        stream.write(user+':'+hashed+'\n')
    os.replace(tmp, credential)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
snippet = Path('/etc/nginx/snippets/pars-promet-auth.conf')
snippet.parent.mkdir(exist_ok=True)
snippet.write_text('auth_basic "PROMET";\nauth_basic_user_file /etc/nginx/pars.promet.pw.htpasswd;\n')
snippet.chmod(0o644)
include = 'include /etc/nginx/snippets/pars-promet-auth.conf;'
updated = original if include in original else re.sub(pattern, lambda m:m[0]+'\n    '+include, original, flags=re.M)
backup = Path('/var/backups/promet-parser')
backup.mkdir(parents=True, exist_ok=True)
(backup / ('nginx-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.conf')).write_text(original)
config.write_text(updated)
try:
    subprocess.run(['nginx', '-t'], check=True)
    subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
except Exception:
    config.write_text(original)
    if previous is not None: credential.write_bytes(previous)
    else: credential.unlink(missing_ok=True)
    raise
print('Вход для MEBELPROMET включён. Пароль и его хеш не добавлены в GitHub.')
PY
