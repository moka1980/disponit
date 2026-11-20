#!/usr/bin/env bash
# Installerer rotasjonstimeren for modultokenene. Kjør som root, én gang:
#   sudo bash deploy/staging/modultoken-oppsett.sh
# Arbeiderunitene installeres av sine egne oppsettskript, ikke av opp.sh;
# denne timeren hører til dem og følger samme form. Idempotent.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "kjør med sudo"; exit 1; }
cd /opt/disponit/aktiv
install -m 644 deploy/staging/disponit-modultoken.service /etc/systemd/system/
install -m 644 deploy/staging/disponit-modultoken.timer /etc/systemd/system/
# Arbeidervakten hører til samme familie: rotasjonen fjerner én årsak til
# at arbeiderne dør, vakten gjør det synlig uansett årsak.
install -m 644 deploy/staging/disponit-arbeidervakt.service /etc/systemd/system/
install -m 644 deploy/staging/disponit-arbeidervakt.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now disponit-modultoken.timer
systemctl enable --now disponit-arbeidervakt.timer
systemctl list-timers disponit-arbeidervakt.timer --no-pager | sed -n 1,2p
systemctl list-timers disponit-modultoken.timer --no-pager | sed -n 1,2p
