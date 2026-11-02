#!/usr/bin/env python3
"""SO_PEERCRED-authenticated Unix proxy for the SIMPLE Broker."""

from __future__ import annotations

import argparse
import errno
import grp
import json
import logging
import os
import secrets
import socket
import socketserver
import stat
import struct
import threading
import time
from pathlib import Path
from typing import Any

from simple_broker import (
    BROKER_MAX_CONNECTIONS,
    MAX_REQUEST_BYTES,
    METHOD_FIELDS,
    PROXY_INTERNAL_PROTOCOL,
    SESSION_TTL_SECONDS,
    _serve_until_stopped,
)


MAX_RESPONSE_BYTES = 131072
CLIENT_TIMEOUT_SECONDS = 10
BROKER_TIMEOUT_SECONDS = 10
MAX_CONNECTIONS = BROKER_MAX_CONNECTIONS
ALLOWED_METHODS = frozenset(METHOD_FIELDS) - {"auth.register"}


def _peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("O proxy exige SO_PEERCRED, disponível em Linux.")
    peer_pid, peer_uid, peer_gid = struct.unpack(
        "3i",
        connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        ),
    )
    return peer_uid, peer_gid, peer_pid


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chave duplicada no protocolo.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Constante JSON inválida: {value}.")


def _validate_private_broker_socket(socket_path: Path) -> None:
    if not socket_path.is_absolute():
        raise RuntimeError("O socket privado do Broker deve ser absoluto.")
    normalized = Path(os.path.abspath(socket_path))
    current = Path("/")
    for component in normalized.parts[1:-1]:
        current = current / component
        try:
            component_stat = current.lstat()
        except OSError as error:
            raise RuntimeError("Não foi possível validar o socket privado do Broker.") from error
        if stat.S_ISLNK(component_stat.st_mode) or not stat.S_ISDIR(
            component_stat.st_mode
        ):
            raise RuntimeError("O caminho do socket privado contém componente inseguro.")
        if component_stat.st_mode & 0o022:
            sticky_root_directory = (
                component_stat.st_uid == 0
                and component_stat.st_mode & stat.S_ISVTX
            )
            if not sticky_root_directory:
                raise RuntimeError(
                    "Um ancestral do socket privado permite escrita não confiável."
                )
    try:
        parent_stat = normalized.parent.lstat()
        socket_stat = normalized.lstat()
    except OSError as error:
        raise RuntimeError("Não foi possível validar o socket privado do Broker.") from error
    if (
        stat.S_ISLNK(parent_stat.st_mode)
        or not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.geteuid()
        or parent_stat.st_mode & 0o077
    ):
        raise RuntimeError(
            "O diretório do socket privado deve pertencer ao proxy e ser privado."
        )
    if (
        stat.S_ISLNK(socket_stat.st_mode)
        or not stat.S_ISSOCK(socket_stat.st_mode)
        or socket_stat.st_uid != os.geteuid()
        or stat.S_IMODE(socket_stat.st_mode) != 0o600
    ):
        raise RuntimeError(
            "O socket privado do Broker deve pertencer ao proxy e ter modo 0600."
        )


def _validate_socket_directory(path: Path, group_id: int) -> None:
    if not path.is_absolute():
        raise RuntimeError("O diretório do socket público deve ser absoluto.")
    normalized = Path(os.path.abspath(path))
    current = Path("/")
    for component in normalized.parts[1:]:
        current = current / component
        try:
            current_stat = current.lstat()
        except OSError as error:
            raise RuntimeError(
                "O diretório do socket público deve existir previamente."
            ) from error
        if stat.S_ISLNK(current_stat.st_mode) or not stat.S_ISDIR(
            current_stat.st_mode
        ):
            raise RuntimeError("O caminho do socket contém link ou componente inválido.")
        if current == normalized:
            if current_stat.st_uid != os.geteuid() or current_stat.st_gid != group_id:
                raise RuntimeError(
                    "O diretório do socket deve pertencer ao proxy e ao grupo configurado."
                )
            mode = stat.S_IMODE(current_stat.st_mode)
            if (
                mode & 0o700 != 0o700
                or mode & 0o077 != 0o010
            ):
                raise RuntimeError(
                    "O diretório do socket exige modo 0710: privado e sem escrita pelo grupo."
                )
        elif current_stat.st_mode & 0o022:
            sticky_root_directory = (
                current_stat.st_uid == 0
                and current_stat.st_mode & stat.S_ISVTX
            )
            if not sticky_root_directory:
                raise RuntimeError(
                    "Um ancestral do socket permite escrita não confiável."
                )


def _prepare_socket_path(socket_path: Path, group_id: int) -> None:
    if not socket_path.is_absolute():
        raise RuntimeError("O caminho do socket público deve ser absoluto.")
    socket_path = Path(os.path.abspath(socket_path))
    _validate_socket_directory(socket_path.parent, group_id)
    try:
        socket_stat = socket_path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(socket_stat.st_mode):
        raise RuntimeError("O caminho do proxy já existe e não é um socket.")
    if socket_stat.st_uid != os.geteuid():
        raise RuntimeError("O socket obsoleto não pertence ao processo do proxy.")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1)
        probe.connect(str(socket_path))
    except OSError as error:
        if error.errno == errno.ENOENT:
            return
        if error.errno != errno.ECONNREFUSED:
            raise RuntimeError(
                "Não foi possível verificar o socket público existente."
            ) from error
        socket_path.unlink()
    else:
        raise RuntimeError("Já existe um proxy ativo nesse socket.")
    finally:
        probe.close()


class ProxyService:
    def __init__(self, broker_socket: Path):
        if not broker_socket.is_absolute():
            raise ValueError("O socket privado do Broker deve ser absoluto.")
        self.broker_socket = Path(os.path.abspath(broker_socket))
        _validate_private_broker_socket(self.broker_socket)
        self.sessions: dict[str, tuple[int, int, int, float]] = {}
        self.sessions_lock = threading.Lock()

    def _broker_call(self, request: dict[str, Any]) -> dict[str, Any]:
        _validate_private_broker_socket(self.broker_socket)
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Requisição muito grande.")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(BROKER_TIMEOUT_SECONDS)
            connection.connect(str(self.broker_socket))
            connection.sendall(payload)
            response = bytearray()
            while not response.endswith(b"\n"):
                chunk = connection.recv(4096)
                if not chunk:
                    raise ConnectionError("Broker encerrou sem retornar resposta.")
                response.extend(chunk)
                if len(response) > MAX_RESPONSE_BYTES:
                    raise ValueError("Resposta do Broker excede o limite permitido.")
        parsed = json.loads(
            response.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(parsed, dict) or not isinstance(parsed.get("ok"), bool):
            raise ValueError("Resposta inválida do Broker.")
        return parsed

    def _resolve_identity(self, linux_uid: int) -> dict[str, Any]:
        response = self._broker_call(
            {
                "protocol": PROXY_INTERNAL_PROTOCOL,
                "method": "identity.resolve",
                "linux_uid": linux_uid,
            }
        )
        if not response["ok"]:
            raise PermissionError(response.get("error", "Identidade não autorizada."))
        identity = {
            key: response.get(key)
            for key in ("linux_uid", "binding_id", "user_id", "username")
        }
        if (
            identity["linux_uid"] != linux_uid
            or isinstance(identity["binding_id"], bool)
            or not isinstance(identity["binding_id"], int)
            or isinstance(identity["user_id"], bool)
            or not isinstance(identity["user_id"], int)
            or not isinstance(identity["username"], str)
        ):
            raise ValueError("Resposta de identidade inválida do Broker.")
        return identity

    def dispatch_for_uid(
        self, linux_uid: int, request: Any
    ) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("Requisição inválida.")
        method = request.get("method")
        if not isinstance(method, str) or method not in ALLOWED_METHODS:
            raise ValueError("Operação não suportada pelo proxy.")
        fields = METHOD_FIELDS[method]
        if set(request) != {"method", *fields}:
            raise ValueError("Campos inválidos para a operação.")

        identity = self._resolve_identity(linux_uid)
        if method == "auth.login":
            username = request["username"]
            if (
                not isinstance(username, str)
                or not secrets.compare_digest(
                    username.lower(), identity["username"].lower()
                )
            ):
                raise PermissionError("Usuário ou senha inválidos.")
            response = self._broker_call(request)
            if response["ok"]:
                token = response.get("token")
                if (
                    not isinstance(token, str)
                    or response.get("user_id") != identity["user_id"]
                ):
                    raise ValueError("Resposta de autenticação inválida do Broker.")
                with self.sessions_lock:
                    now = time.monotonic()
                    self.sessions = {
                        active_token: active_identity
                        for active_token, active_identity in self.sessions.items()
                        if active_identity[3] > now
                    }
                    self.sessions[token] = (
                        linux_uid,
                        identity["user_id"],
                        identity["binding_id"],
                        now + SESSION_TTL_SECONDS,
                    )
            return response

        token = request.get("token")
        if not isinstance(token, str) or not token:
            raise PermissionError("Sessão inválida ou expirada.")
        with self.sessions_lock:
            token_identity = self.sessions.get(token)
            if token_identity is None:
                raise PermissionError("Sessão inválida para esta identidade Linux.")
            if token_identity[3] <= time.monotonic():
                self.sessions.pop(token, None)
                raise PermissionError("Sessão inválida para esta identidade Linux.")
            if token_identity[:3] != (
                linux_uid,
                identity["user_id"],
                identity["binding_id"],
            ):
                raise PermissionError("Sessão inválida para esta identidade Linux.")

        response = self._broker_call(request)
        if method == "auth.change_password" and response["ok"]:
            replacement_token = response.get("token")
            if not isinstance(replacement_token, str):
                raise ValueError("Resposta de troca de senha inválida do Broker.")
            with self.sessions_lock:
                self.sessions = {
                    active_token: active_identity
                    for active_token, active_identity in self.sessions.items()
                    if active_identity[1] != identity["user_id"]
                }
                self.sessions[replacement_token] = (
                    linux_uid,
                    identity["user_id"],
                    identity["binding_id"],
                    time.monotonic() + SESSION_TTL_SECONDS,
                )
        elif method == "auth.logout" and response["ok"]:
            with self.sessions_lock:
                self.sessions.pop(token, None)
        return response


class ProxyRequestHandler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.request.settimeout(CLIENT_TIMEOUT_SECONDS)

    def handle(self) -> None:
        try:
            linux_uid, _linux_gid, _pid = _peer_credentials(self.request)
        except (OSError, RuntimeError, struct.error):
            logging.warning("Rejected connection without kernel peer credentials")
            return
        method = "invalid"
        try:
            raw_request = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if (
                not raw_request
                or len(raw_request) > MAX_REQUEST_BYTES
                or not raw_request.endswith(b"\n")
            ):
                raise ValueError("Requisição inválida ou muito grande.")
            request = json.loads(
                raw_request.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
            if (
                isinstance(request, dict)
                and isinstance(request.get("method"), str)
                and request["method"] in ALLOWED_METHODS
            ):
                method = request["method"]
            response = self.server.service.dispatch_for_uid(  # type: ignore[attr-defined]
                linux_uid, request
            )
            logging.info(
                "Proxy request uid=%d method=%s result=%s",
                linux_uid,
                method,
                "ok" if response.get("ok") else "denied",
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, PermissionError) as error:
            response = {"ok": False, "error": str(error)}
            logging.warning(
                "Proxy request uid=%d method=%s rejected: %s",
                linux_uid,
                method,
                str(error),
            )
        except RuntimeError:
            logging.exception(
                "Proxy security validation failed uid=%d method=%s",
                linux_uid,
                method,
            )
            response = {"ok": False, "error": "Falha ao processar a operação."}
        except (OSError, TimeoutError, ConnectionError):
            logging.exception(
                "Proxy request uid=%d method=%s failed contacting Broker",
                linux_uid,
                method,
            )
            response = {"ok": False, "error": "Falha ao processar a operação."}
        try:
            encoded = json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
            if len(encoded) > MAX_RESPONSE_BYTES:
                encoded = b'{"ok":false,"error":"Resposta muito grande."}\n'
            self.wfile.write(encoded)
        except (OSError, TimeoutError):
            logging.warning("Proxy response delivery failed uid=%d", linux_uid)


class ProxyServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = MAX_CONNECTIONS

    def __init__(self, socket_path: Path, service: ProxyService):
        self.service = service
        self.connection_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.serve_ready = threading.Event()
        super().__init__(str(socket_path), ProxyRequestHandler)

    def service_actions(self) -> None:
        self.serve_ready.set()

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connection_slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket, client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="Proxy Unix multiusuário do SIMPLE")
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--broker-socket", required=True, type=Path)
    parser.add_argument("--socket-group", required=True)
    arguments = parser.parse_args()
    if not hasattr(socket, "SO_PEERCRED"):
        parser.error("SO_PEERCRED é obrigatório; execute o proxy em Linux.")
    arguments.socket = Path(os.path.abspath(arguments.socket))
    arguments.broker_socket = Path(os.path.abspath(arguments.broker_socket))
    try:
        group_id = int(arguments.socket_group)
    except ValueError:
        try:
            group_id = grp.getgrnam(arguments.socket_group).gr_gid
        except KeyError as error:
            parser.error(f"grupo Linux não encontrado: {arguments.socket_group}")
            raise AssertionError from error
    if group_id < 0:
        parser.error("GID inválido.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    _prepare_socket_path(arguments.socket, group_id)
    previous_umask = os.umask(0o117)
    try:
        server = ProxyServer(arguments.socket, ProxyService(arguments.broker_socket))
    finally:
        os.umask(previous_umask)
    os.chown(arguments.socket, os.geteuid(), group_id)
    os.chmod(arguments.socket, 0o660)
    logging.info("Proxy SIMPLE iniciado em %s", arguments.socket)
    _serve_until_stopped(server, arguments.socket, "Proxy SIMPLE")
    logging.info("Proxy SIMPLE encerrado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
