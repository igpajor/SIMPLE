"""Strict, bounded NDJSON client protocol validation."""

from __future__ import annotations

import json
import math
import time
import unicodedata
from collections import deque
from typing import Any, Deque, Dict, Iterable, Optional, Tuple

from ..engine import config

CLIENT_MESSAGE_SCHEMAS = {
    "dir": {"d"},
    "boost": {"on"},
    "resize": {"cols", "rows"},
    "ping": set(),
    "bye": set(),
}


def _reject_duplicate_keys(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chave duplicada no protocolo.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Constante numérica inválida no protocolo.")


def _valid_nickname(value: Any) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= config.MAX_NICKNAME_LENGTH:
        return False
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            return False
    return True


def _integer_in_range(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _validate_viewport(message: Dict[str, Any]) -> None:
    if not _integer_in_range(
        message.get("cols"), config.MIN_VIEW_COLS, config.MAX_VIEW_COLS
    ):
        raise ValueError("Largura da viewport inválida.")
    if not _integer_in_range(
        message.get("rows"), config.MIN_VIEW_ROWS, config.MAX_VIEW_ROWS
    ):
        raise ValueError("Altura da viewport inválida.")


def parse_client_message(raw: bytes) -> Dict[str, Any]:
    if not isinstance(raw, bytes):
        raise ValueError("A mensagem deve ser bytes.")
    if not raw or len(raw) > config.MAX_CLIENT_MESSAGE_BYTES:
        raise ValueError("Mensagem vazia ou maior que o limite permitido.")
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ValueError("A mensagem deve conter uma única linha completa.")
    try:
        text = raw[:-1].decode("utf-8")
        message = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("JSON inválido no protocolo.") from error
    if not isinstance(message, dict):
        raise ValueError("A mensagem deve ser um objeto JSON.")
    message_type = message.get("t")
    if not isinstance(message_type, str):
        raise ValueError("Tipo de mensagem ausente ou inválido.")

    if message_type == "hello":
        required = {"t", "v", "cols", "rows"}
        allowed = required | {"nick"}
        if set(message) - allowed or not required.issubset(message):
            raise ValueError("Campos inválidos para hello.")
        if type(message["v"]) is not int or message["v"] != config.PROTOCOL_VERSION:
            raise ValueError("Versão de protocolo não suportada.")
        _validate_viewport(message)
        if "nick" in message and not _valid_nickname(message["nick"]):
            raise ValueError("Apelido inválido.")
        return message

    expected_fields = CLIENT_MESSAGE_SCHEMAS.get(message_type)
    if expected_fields is None or set(message) != expected_fields | {"t"}:
        raise ValueError("Tipo ou campos de mensagem inválidos.")
    if message_type == "dir" and (
        not isinstance(message["d"], str)
        or message["d"] not in {"U", "D", "L", "R"}
    ):
        raise ValueError("Direção inválida.")
    if message_type == "boost" and type(message["on"]) is not bool:
        raise ValueError("O estado do boost deve ser booleano.")
    if message_type == "resize":
        _validate_viewport(message)
    return message


class MessageRateLimiter:
    def __init__(
        self,
        max_messages: int = config.MAX_CLIENT_MESSAGES_PER_SECOND,
        interval_seconds: float = 1.0,
    ) -> None:
        if type(max_messages) is not int or max_messages < 1:
            raise ValueError("O limite de mensagens deve ser positivo.")
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds)
            or interval_seconds <= 0
        ):
            raise ValueError("O intervalo do limite deve ser positivo e finito.")
        self.max_messages = max_messages
        self.interval_seconds = float(interval_seconds)
        self.timestamps: Deque[float] = deque()

    def allow(self, now: Optional[float] = None) -> bool:
        current = time.monotonic() if now is None else now
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise ValueError("Instante inválido para limite de mensagens.")
        if not math.isfinite(current):
            raise ValueError("Instante inválido para limite de mensagens.")
        current = float(current)
        if self.timestamps and current < self.timestamps[-1]:
            raise ValueError("O relógio do limite não pode retroceder.")
        cutoff = current - self.interval_seconds
        while self.timestamps and self.timestamps[0] <= cutoff:
            self.timestamps.popleft()
        if len(self.timestamps) >= self.max_messages:
            return False
        self.timestamps.append(current)
        return True
