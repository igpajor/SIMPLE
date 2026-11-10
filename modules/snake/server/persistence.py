"""Bounded persistence requests to the dedicated Broker Snake channel."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..engine import config
from .identity import SNAKE_INTERNAL_PROTOCOL

MAX_BROKER_REQUEST_BYTES = 1024
MAX_BROKER_RESPONSE_BYTES = 131072
LEADERBOARD_MAX_PAGE_SIZE = 100
LEADERBOARD_MAX_OFFSET = 10000


class BrokerSnakePersistence:
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
        self.socket_path = Path(socket_path)
        self.timeout_seconds = float(timeout_seconds)

    async def record_score(
        self, linux_uid: int, binding_id: int, length: int, kills: int
    ) -> None:
        if type(linux_uid) is not int or linux_uid < 1:
            raise ValueError("UID Linux inválido.")
        if type(binding_id) is not int or binding_id < 1:
            raise ValueError("Vínculo de identidade inválido.")
        max_score_length = config.ARENA_WIDTH * config.ARENA_HEIGHT
        if type(length) is not int or not 1 <= length <= max_score_length:
            raise ValueError("Comprimento de Snake inválido.")
        if type(kills) is not int or not 0 <= kills <= max_score_length:
            raise ValueError("Quantidade de eliminações inválida.")
        await self._request(
            {
                "protocol": SNAKE_INTERNAL_PROTOCOL,
                "method": "score.record",
                "linux_uid": linux_uid,
                "binding_id": binding_id,
                "length": length,
                "kills": kills,
            }
        )

    async def leaderboard(
        self, limit: int = config.LEADERBOARD_SIZE, offset: int = 0
    ) -> List[Dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= LEADERBOARD_MAX_PAGE_SIZE:
            raise ValueError("Limite do ranking inválido.")
        if type(offset) is not int or not 0 <= offset <= LEADERBOARD_MAX_OFFSET:
            raise ValueError("Deslocamento do ranking inválido.")
        response = await self._request(
            {
                "protocol": SNAKE_INTERNAL_PROTOCOL,
                "method": "leaderboard.get",
                "limit": limit,
                "offset": offset,
            }
        )
        scores = response.get("scores")
        if not isinstance(scores, list) or len(scores) > limit:
            raise ValueError("Ranking inválido retornado pelo Broker.")
        validated = []
        expected_fields = {
            "rank",
            "username",
            "best_length",
            "kills_total",
            "deaths_total",
            "games",
        }
        for index, score in enumerate(scores, start=1):
            if not isinstance(score, dict) or set(score) != expected_fields:
                raise ValueError("Registro inválido no ranking Snake.")
            if (
                type(score["rank"]) is not int
                or score["rank"] != offset + index
                or not isinstance(score["username"], str)
                or not score["username"]
                or type(score["best_length"]) is not int
                or not (
                    1
                    <= score["best_length"]
                    <= config.ARENA_WIDTH * config.ARENA_HEIGHT
                )
                or any(
                    type(score[field]) is not int or score[field] < 0
                    for field in ("kills_total", "deaths_total", "games")
                )
            ):
                raise ValueError("Registro inválido no ranking Snake.")
            validated.append(dict(score))
        return validated

    async def _request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(payload) > MAX_BROKER_REQUEST_BYTES:
            raise ValueError("Requisição ao Broker excede o limite permitido.")
        response = await asyncio.wait_for(
            self._exchange(payload), timeout=self.timeout_seconds
        )
        try:
            value = json.loads(
                response.decode("utf-8"),
                object_pairs_hook=self._reject_duplicate_keys,
                parse_constant=self._reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("Resposta inválida do Broker.") from error
        if not isinstance(value, dict) or type(value.get("ok")) is not bool:
            raise ValueError("Resposta inválida do Broker.")
        if not value["ok"]:
            error = value.get("error")
            if not isinstance(error, str):
                raise ValueError("Resposta inválida do Broker.")
            if error == "UID Linux sem vínculo ativo com uma conta SIMPLE.":
                raise PermissionError(error)
            raise RuntimeError("O Broker recusou a operação Snake.")
        return value

    @staticmethod
    def _reject_duplicate_keys(
        pairs: List[Tuple[str, Any]]
    ) -> Dict[str, Any]:
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Resposta do Broker contém uma chave duplicada.")
            value[key] = item
        return value

    @staticmethod
    def _reject_json_constant(value: str) -> None:
        raise ValueError("Resposta do Broker contém um número inválido.")

    async def _exchange(self, payload: bytes) -> bytes:
        reader, writer = await asyncio.open_unix_connection(
            str(self.socket_path), limit=MAX_BROKER_RESPONSE_BYTES + 1
        )
        try:
            writer.write(payload)
            await writer.drain()
            try:
                response = await reader.readline()
            except (ValueError, asyncio.LimitOverrunError) as error:
                raise ValueError(
                    "Resposta inválida ou muito grande do Broker."
                ) from error
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
