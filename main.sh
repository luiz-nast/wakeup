#!/usr/bin/env bash
# Entrypoint do despertador. Chamado pelo wakeup.service.
# Caminho absoluto do uv: o systemd nao herda o PATH do Homebrew.
cd "$(dirname "$(readlink -f "$0")")"
exec /home/linuxbrew/.linuxbrew/bin/uv run --frozen python wakeup.py
