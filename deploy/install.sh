#!/bin/bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Запустите через sudo'; exit 1; }
cd /opt/promet-parser
test -f /etc/nginx/sites-available/pars.promet.pw
test -f data/observations.json
if ! id promet-parser >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/promet-parser --shell /usr/sbin/nologin promet-parser
fi
install -d -m 750 -o promet-parser -g promet-parser /var/lib/promet-parser
install -d -m 755 -o promet-parser -g promet-parser /var/www/pars.promet.pw/public
if [[ ! -f /var/lib/promet-parser/observations.json ]]; then
    install -m 640 -o promet-parser -g promet-parser data/observations.json /var/lib/promet-parser/observations.json
fi
install -m 755 deploy/promet-parser-update /usr/local/bin/promet-parser-update
install -m 755 deploy/set-password.sh /usr/local/bin/promet-parser-password
install -m 644 deploy/promet-parser-refresh.service /etc/systemd/system/
install -m 644 deploy/promet-parser-refresh.timer /etc/systemd/system/
# Do not enable the timer until password installation and config validation pass.
/usr/local/bin/promet-parser-password
runuser -u promet-parser -- python3 scripts/build.py --data /var/lib/promet-parser/observations.json --output /var/www/pars.promet.pw/public/index.html
systemctl daemon-reload
systemctl enable --now promet-parser-refresh.timer
echo 'Установлено. Обновить программу и цены: promet-parser-update'
echo 'Проверить расписание: systemctl list-timers promet-parser-refresh.timer'
