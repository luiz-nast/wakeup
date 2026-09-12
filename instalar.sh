#!/usr/bin/env bash
# Poe a agenda no ar: units do systemd, o sincronizador e o atalho do app.
# Idempotente - roda de novo depois de cada git pull.
#
#   sudo ./instalar.sh
set -e
[ "$(id -u)" = 0 ] || { echo "roda com sudo: sudo ./instalar.sh"; exit 1; }

U="${SUDO_USER:-$(logname)}"
H=$(getent passwd "$U" | cut -d: -f6)
UID_U=$(id -u "$U")
BASE=$(dirname "$(readlink -f "$0")")
JSON="$H/.config/wakeup/alarmes.json"

# O sincronizador roda como root, entao nao pode morar numa pasta que o teu
# usuario escreve - se morasse, quem editasse o arquivo mandaria no root.
install -m 755 -o root -g root "$BASE/wakeup-sync" /usr/local/sbin/wakeup-sync

# Primeiro alarme: herda a hora do alarm.timer antigo, se ele ainda estiver la
if [ ! -f "$JSON" ]; then
  HORA=$(sed -n 's/^OnCalendar=.* \([0-2][0-9]:[0-5][0-9]\):[0-9][0-9]$/\1/p' \
    /etc/systemd/system/alarm.timer 2>/dev/null | head -1)
  install -d -o "$U" -g "$U" "$H/.config/wakeup"
  printf '{\n  "alarmes": [\n    {"hora": "%s"}\n  ]\n}\n' "${HORA:-06:00}" > "$JSON"
  chown "$U:$U" "$JSON"
  echo "criei $JSON com o alarme das ${HORA:-06:00}"
fi

cat > /etc/systemd/system/wakeup@.service <<UNIT
[Unit]
Description=despertador das %i

[Service]
User=$U
Environment=XDG_RUNTIME_DIR=/run/user/$UID_U
Environment=ALARME=%i
ExecStart=$BASE/main.sh
UNIT

cat > /etc/systemd/system/wakeup-sync.service <<UNIT
[Unit]
Description=aplica o alarmes.json nos timers

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/wakeup-sync $JSON

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/wakeup-sync.path <<UNIT
[Unit]
Description=vigia o alarmes.json

[Path]
PathChanged=$JSON
PathModified=$H/.config/wakeup
Unit=wakeup-sync.service

[Install]
WantedBy=paths.target
UNIT

systemctl daemon-reload
systemctl enable --now wakeup-sync.path
systemctl enable wakeup-sync.service          # roda tambem no boot
systemctl start wakeup-sync.service           # aplica o que ja esta no json

# O alarme velho so sai depois que os novos estao no ar
if [ -f /etc/systemd/system/alarm.timer ]; then
  systemctl disable --now alarm.timer
  rm -f /etc/systemd/system/alarm.timer /etc/systemd/system/alarm.service
  systemctl daemon-reload
  echo "tirei o alarm.timer antigo"
fi

install -d -o "$U" -g "$U" "$H/.local/share/applications"
cat > "$H/.local/share/applications/dev.nast.wakeup.desktop" <<ATALHO
[Desktop Entry]
Type=Application
Name=Alarmes
Comment=Agenda do despertador
Exec=$BASE/agenda.py
Icon=alarm-symbolic
Terminal=false
Categories=Utility;Clock;
ATALHO
chown "$U:$U" "$H/.local/share/applications/dev.nast.wakeup.desktop"

echo
systemctl list-timers 'wakeup@*' --all --no-pager
