#!/usr/bin/env bash
# Entrypoint do despertador. Chamado pelo wakeup@.service.
# Caminho absoluto do uv: o systemd nao herda o PATH do Homebrew.
# flock: com mais de um alarme, o que pega a hora primeiro toca sozinho -
# o segundo sai na hora em vez de subir outro mpv por cima.
cd "$(dirname "$(readlink -f "$0")")"
exec flock -n "${XDG_RUNTIME_DIR:-/tmp}/wakeup.lock" \
  /home/linuxbrew/.linuxbrew/bin/uv run --frozen python wakeup.py
