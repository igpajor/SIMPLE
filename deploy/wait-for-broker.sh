#!/usr/bin/env bash
set -euo pipefail

readonly SOCKET_PATH="${1:-/run/simple-private/broker.sock}"
readonly ATTEMPTS=100

for ((attempt = 0; attempt < ATTEMPTS; attempt++)); do
    if /usr/bin/python3 -c '
import socket
import sys

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(0.25)
    connection.connect(sys.argv[1])
    connection.sendall(b"{}\n")
    response = connection.recv(4096)
    if not response.endswith(b"\n"):
        raise ConnectionError("Broker did not return a complete response")
' "$SOCKET_PATH" 2>/dev/null; then
        exit 0
    fi
    sleep 0.1
done

printf 'Broker não ficou disponível em %s dentro do limite.\n' "$SOCKET_PATH" >&2
exit 1
