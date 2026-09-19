#!/usr/bin/env bash
# Tira o despertador do sistema e deixa a maquina como estava antes.
#
#   ./desinstalar.sh --listar   # so mostra o que existe hoje (sem root)
#   sudo ./desinstalar.sh       # tira tudo
#
# Leva tudo mesmo: units, sincronizador, atalho, alarmes, musicas copiadas e
# estado. So o repo fica - e codigo, e e por ele que eu volto (./instalar.sh).
set -e
# o pkexec (botao do app) entra com PATH enxuto: fixo aqui pra achar runuser
PATH=/usr/sbin:/usr/bin:/sbin:/bin
BASE=$(dirname "$(readlink -f "$0")")

if [ "$1" = "--listar" ]; then
  echo "o que sairia:"
  for f in /etc/systemd/system/wakeup@*.timer /etc/systemd/system/wakeup@.service \
           /etc/systemd/system/wakeup-sync.service /etc/systemd/system/wakeup-sync.path \
           /usr/local/sbin/wakeup-sync "$HOME/.local/share/applications/dev.nast.wakeup.desktop"; do
    [ -e "$f" ] && echo "  $f"
  done
  for d in "$HOME/.config/wakeup" "$HOME/.local/share/wakeup" \
           "$HOME/.local/state/wakeup" "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/wakeup" \
           "$BASE/__pycache__"; do
    [ -e "$d" ] && echo "  $d/"
  done
  echo "fica: $BASE (codigo, modelos, .venv, sons/alarm.mp3)"
  exit 0
fi

[ "$(id -u)" = 0 ] || { echo "roda com sudo: sudo ./desinstalar.sh"; exit 1; }

# quem chamou: sudo diz num lugar, pkexec (o botao do app) diz em outro
U="${SUDO_USER:-}"
[ -z "$U" ] && [ -n "$PKEXEC_UID" ] && U=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
[ -z "$U" ] && { echo "nao sei de quem e a sessao"; exit 1; }
H=$(getent passwd "$U" | cut -d: -f6)
UID_U=$(id -u "$U")

# 1. para o que estiver tocando agora - o SIGTERM e que devolve o audio
systemctl stop 'wakeup@*.service' 2>/dev/null || true

# 2. se algum alarme morreu no soco e deixou anotado, devolve o audio
if [ -f "$H/.local/state/wakeup/audio-antes.json" ]; then
  runuser -u "$U" -- env XDG_RUNTIME_DIR=/run/user/$UID_U \
    python3 "$BASE/audio_estado.py" || true
fi

# 3. units
systemctl disable --now 'wakeup@*.timer' 2>/dev/null || true
systemctl disable --now wakeup-sync.path wakeup-sync.service 2>/dev/null || true
rm -f /etc/systemd/system/wakeup@*.timer \
      /etc/systemd/system/wakeup@.service \
      /etc/systemd/system/wakeup-sync.service \
      /etc/systemd/system/wakeup-sync.path \
      /etc/systemd/system/timers.target.wants/wakeup@*.timer \
      /etc/systemd/system/paths.target.wants/wakeup-sync.path \
      /etc/systemd/system/multi-user.target.wants/wakeup-sync.service
systemctl daemon-reload
systemctl reset-failed 'wakeup*' 2>/dev/null || true

# 4. sincronizador e atalho do menu
rm -f /usr/local/sbin/wakeup-sync
rm -f "$H/.local/share/applications/dev.nast.wakeup.desktop"

# 5. o que o app deixou na home: alarmes, musicas copiadas, estado e o
#    diretorio de runtime do painel
rm -rf "$H/.config/wakeup" "$H/.local/share/wakeup" "$H/.local/state/wakeup" \
       "/run/user/$UID_U/wakeup" "$BASE/__pycache__"

echo "desinstalado - alarmes, musicas copiadas e estado foram junto."
echo "fica: $BASE (codigo, modelos, .venv e sons/alarm.mp3)"
echo "volta com: sudo ./instalar.sh"
echo "a tampa do note (/etc/systemd/logind.conf.d/wakeup.conf) eu deixo: e config de"
echo "sistema, nao do despertador. Pra tirar: sudo rm esse arquivo e reiniciar."
