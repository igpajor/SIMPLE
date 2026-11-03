#!/usr/bin/env bash
set -euo pipefail

if [[ -v SSH_ORIGINAL_COMMAND ]]; then
  printf 'Comandos SSH remotos não são permitidos.\n' >&2
  exit 126
fi

if [[ -z "${SSH_TTY:-}" || ! -t 0 || ! -t 1 ]]; then
  printf 'O SIMPLE requer uma sessão SSH interativa com terminal.\n' >&2
  exit 126
fi

terminal_type="${TERM:-xterm}"
if [[ ! "$terminal_type" =~ ^[A-Za-z0-9._+-]{1,64}$ ]]; then
  terminal_type="xterm"
fi

exec /usr/bin/env -i \
  HOME="$HOME" \
  USER="$USER" \
  LOGNAME="$USER" \
  SHELL=/bin/bash \
  TERM="$terminal_type" \
  PATH=/usr/bin:/bin \
  SIMPLE_MODE=proxy \
  PROXY_SOCKET=/run/simple-public/proxy.sock \
  /usr/bin/bash /opt/simple/simple.sh
