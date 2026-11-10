"""Narrow asynchronous identity lookup through the authorized Broker socket."""

from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
from typing import Any, Dict

SNAKE_INTERNAL_PROTOCOL = "simple-snake-v1"
MAX_BROKER_RESPONSE_BYTES = 131072
INACTIVE_IDENTITY_ERRORS = {
    "UID Linux sem vínculo ativo com uma conta SIMPLE.",
    "A conta SIMPLE vinculada está desativada.",
}


class BrokerIdentityResolver:
    def __init__(
        self, socket_path: Path, timeout_seconds: float = 2.0
    ) -> None:
        if not socket_path.is_absolute():
            raise ValueError("O caminho do Broker deve ser absoluto.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("O tempo limite do Broker deve ser positivo e finito.")
        self.socket_path = Path(os.path.abspath(socket_path))
        self.timeout_seconds = float(timeout_seconds)

    async def __call__(self, linux_uid: int) -> Dict[str, Any]:
        if type(linux_uid) is not int or linux_uid < 1:
            raise ValueError("UID Linux inválido.")
        request = {
            "protocol": SNAKE_INTERNAL_PROTOCOL,
            "method": "identity.resolve",
            "linux_uid": linux_uid,
        }
        payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        response = await asyncio.wait_for(
            self._request(payload), timeout=self.timeout_seconds
        )
        try:
            value = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Resposta inválida do Broker.") from error
        if not isinstance(value, dict) or type(value.get("ok")) is not bool:
            raise ValueError("Resposta inválida do Broker.")
        if not value["ok"]:
            error = value.get("error")
            if isinstance(error, str) and error in INACTIVE_IDENTITY_ERRORS:
                raise PermissionError(error)
            raise ConnectionError("O Broker não conseguiu resolver a identidade.")
        identity = {
            key: value.get(key)
            for key in ("linux_uid", "binding_id", "user_id", "username")
        }
        if (
            identity["linux_uid"] != linux_uid
            or type(identity["binding_id"]) is not int
            or identity["binding_id"] < 1
            or type(identity["user_id"]) is not int
            or identity["user_id"] < 1
            or not isinstance(identity["username"], str)
        ):
            raise ValueError("Resposta de identidade inválida do Broker.")
        return identity

    async def _request(self, payload: bytes) -> bytes:
        reader, writer = await asyncio.open_unix_connection(
            str(self.socket_path), limit=MAX_BROKER_RESPONSE_BYTES + 1
        )
        try:
            writer.write(payload)
            await writer.drain()
            response = await reader.readline()
            if (
                not response
                or len(response) > MAX_BROKER_RESPONSE_BYTES
                or not response.endswith(b"\n")
            ):
                raise ValueError("Resposta inválida ou muito grande do Broker.")
            return response
        finally:
            writer.close()
            await writer.wait_closed()
