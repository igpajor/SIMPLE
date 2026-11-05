#!/usr/bin/env python3
"""One-request client for the SIMPLE Unix-socket broker."""

from __future__ import annotations

import json
import socket
import sys


def _request_bytes(socket_path: str, payload: dict[str, str]) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(25)
        connection.connect(socket_path)
        connection.sendall(
            json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        )
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionError("O broker encerrou a conexão sem resposta.")
            response.extend(chunk)
            if len(response) > 131072:
                raise ValueError("Resposta do broker excede o limite permitido.")
    return bytes(response)


def request(socket_path: str, method: str, fields: dict[str, str]) -> dict:
    response = _request_bytes(socket_path, {"method": method, **fields})
    value = json.loads(response.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("O broker retornou uma resposta inválida.")
    return value


def main() -> int:
    if len(sys.argv) < 3:
        print("Uso: broker_client.py SOCKET OPERAÇÃO [CAMPOS...]", file=sys.stderr)
        return 2
    socket_path, method, *field_names = sys.argv[1:]
    values = sys.stdin.buffer.read().split(b"\0")
    if values and values[-1] == b"":
        values.pop()
    if len(values) != len(field_names):
        print("Quantidade inválida de campos para o broker.", file=sys.stderr)
        return 2
    try:
        request_fields = {}
        for field, raw_value in zip(field_names, values):
            request_fields[field] = raw_value.decode("utf-8")
        response = _request_bytes(
            socket_path, {"method": method, **request_fields}
        )
        print(response.decode("utf-8").strip())
        return 0
    except (OSError, UnicodeDecodeError, ValueError, ConnectionError) as error:
        print(f"Não foi possível acessar o broker SIMPLE: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
