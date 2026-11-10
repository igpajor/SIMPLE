"""Authoritative asyncio game server over a restricted Unix stream socket."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import secrets
import signal
import socket
import stat
import struct
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from ..engine import config
from ..engine.bots import step_bots
from ..engine.world import Snake, World
from .identity import BrokerIdentityResolver
from .protocol import (
    MessageRateLimiter,
    UnsupportedVersionError,
    parse_client_message,
)

IdentityResolver = Callable[[int], Awaitable[Optional[Dict[str, Any]]]]
QueuedMessage = Tuple[bytes, str]
logger = logging.getLogger(__name__)


@dataclass
class ClientSession:
    linux_uid: int
    binding_id: int
    user_id: int
    session_id: str
    player_id: str
    nick: str
    cols: int
    rows: int
    writer: asyncio.StreamWriter
    identity_checked_at: float
    outgoing: asyncio.Queue[QueuedMessage] = field(
        default_factory=lambda: asyncio.Queue(maxsize=config.MAX_OUTBOUND_QUEUE)
    )
    writer_task: Optional[asyncio.Task] = None
    was_alive: bool = True


class SnakeServer:
    def __init__(
        self,
        identity_resolver: IdentityResolver,
        world: Optional[World] = None,
        bot_count: Optional[int] = None,
        max_human_players: int = config.MAX_HUMAN_PLAYERS,
        tick_interval: Optional[float] = None,
        idle_timeout: float = config.CLIENT_IDLE_TIMEOUT_SECONDS,
        identity_revalidation_interval: float = config.IDENTITY_REVALIDATION_SECONDS,
    ) -> None:
        if not callable(identity_resolver):
            raise ValueError("O resolvedor de identidade é obrigatório.")
        if (
            type(max_human_players) is not int
            or not 1 <= max_human_players <= config.MAX_HUMAN_PLAYERS
        ):
            raise ValueError("O limite de jogadores humanos é inválido.")
        if bot_count is None:
            bot_count = config.BOT_COUNT if config.BOTS_ENABLED else 0
        if type(bot_count) is not int or not 0 <= bot_count <= config.BOT_COUNT:
            raise ValueError("A quantidade de bots é inválida.")
        if tick_interval is None:
            tick_interval = 1.0 / config.TICK_HZ
        if (
            isinstance(tick_interval, bool)
            or not isinstance(tick_interval, (int, float))
            or not math.isfinite(tick_interval)
            or tick_interval <= 0
        ):
            raise ValueError("O intervalo de tick deve ser positivo e finito.")
        if (
            isinstance(idle_timeout, bool)
            or not isinstance(idle_timeout, (int, float))
            or not math.isfinite(idle_timeout)
            or idle_timeout <= 0
        ):
            raise ValueError("O limite de inatividade deve ser positivo e finito.")
        if (
            isinstance(identity_revalidation_interval, bool)
            or not isinstance(identity_revalidation_interval, (int, float))
            or not math.isfinite(identity_revalidation_interval)
            or identity_revalidation_interval <= 0
        ):
            raise ValueError("O intervalo de revalidação deve ser positivo e finito.")
        if max_human_players + bot_count > 40:
            raise ValueError("A quantidade total de cobras excede a arena.")
        self.identity_resolver = identity_resolver
        self.world = world or World()
        self.max_human_players = max_human_players
        self.tick_interval = float(tick_interval)
        self.idle_timeout = float(idle_timeout)
        self.identity_revalidation_interval = float(identity_revalidation_interval)
        self.bot_ids: List[str] = []
        self.sessions: Dict[int, ClientSession] = {}
        self.player_nicks: Dict[str, str] = {}
        self.player_slots: Dict[str, int] = {}
        self._respawn_waiting = set()
        self._client_writers: set = set()
        self._client_tasks: set = set()
        self._identity_tasks: Dict[int, asyncio.Task] = {}
        self._server: Optional[asyncio.AbstractServer] = None
        self._tick_task: Optional[asyncio.Task] = None
        self._socket_path: Optional[Path] = None
        self._socket_identity: Optional[Tuple[int, int]] = None
        self._active_connections = 0
        self._max_connections = max_human_players * 2
        self._bot_count = bot_count
        self._initialize_bots()

    async def start_unix(
        self, socket_path: Path, socket_gid: Optional[int] = None
    ) -> None:
        path = Path(socket_path)
        if not path.is_absolute():
            raise ValueError("O caminho do socket do Snake deve ser absoluto.")
        path = Path(os.path.abspath(path))
        current = Path("/")
        parent_stat = current.lstat()
        for component in path.parent.parts[1:]:
            current = current / component
            try:
                parent_stat = current.lstat()
            except OSError as error:
                raise RuntimeError(
                    "O diretório do socket do Snake não existe."
                ) from error
            if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
                parent_stat.st_mode
            ):
                raise RuntimeError("O caminho do socket do Snake não é seguro.")
            if parent_stat.st_mode & 0o022:
                sticky_root_directory = (
                    parent_stat.st_uid == 0
                    and parent_stat.st_mode & stat.S_ISVTX
                )
                if not sticky_root_directory:
                    raise RuntimeError(
                        "Um diretório ancestral do socket Snake permite escrita."
                    )
        if (
            parent_stat.st_uid != os.geteuid()
            or parent_stat.st_mode & 0o022
        ):
            raise RuntimeError("O diretório do socket do Snake não é seguro.")
        if self._server is not None:
            raise RuntimeError("O servidor do Snake já está iniciado.")
        if socket_gid is not None and (type(socket_gid) is not int or socket_gid < 0):
            raise ValueError("GID do socket do Snake inválido.")
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("O caminho do socket do Snake já existe.")

        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(path))
            socket_stat = path.lstat()
            if not stat.S_ISSOCK(socket_stat.st_mode) or socket_stat.st_uid != os.geteuid():
                raise RuntimeError("O socket criado não pertence ao serviço Snake.")
            self._socket_path = path
            self._socket_identity = (socket_stat.st_dev, socket_stat.st_ino)
            if socket_gid is not None:
                os.chown(path, -1, socket_gid)
            os.chmod(path, 0o660)
            listener.listen()
            listener.setblocking(False)
            path_stat = path.lstat()
            if (
                not stat.S_ISSOCK(path_stat.st_mode)
                or (path_stat.st_dev, path_stat.st_ino) != self._socket_identity
            ):
                raise RuntimeError("O socket do Snake foi substituído durante a inicialização.")
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                sock=listener,
                limit=config.MAX_CLIENT_MESSAGE_BYTES + 1,
            )
            self._socket_path = path
            self._tick_task = asyncio.create_task(self._tick_loop())
        except BaseException:
            listener.close()
            self._unlink_owned_socket()
            raise

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None
        identity_tasks = list(self._identity_tasks.values())
        self._identity_tasks.clear()
        for task in identity_tasks:
            task.cancel()
        if identity_tasks:
            await asyncio.gather(*identity_tasks, return_exceptions=True)
        for writer in list(self._client_writers):
            writer.close()
        for session in list(self.sessions.values()):
            await self._close_session(session)
        current_task = asyncio.current_task()
        pending_tasks = [
            task for task in self._client_tasks if task is not current_task
        ]
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        self._unlink_owned_socket()

    def _unlink_owned_socket(self) -> None:
        if self._socket_path is None or self._socket_identity is None:
            return
        path = self._socket_path
        identity = self._socket_identity
        self._socket_path = None
        self._socket_identity = None
        try:
            current = path.lstat()
        except FileNotFoundError:
            return
        if (
            stat.S_ISSOCK(current.st_mode)
            and (current.st_dev, current.st_ino) == identity
        ):
            path.unlink()

    def _initialize_bots(self) -> None:
        for index in range(self._bot_count):
            player_id = "bot-{0:02d}".format(index + 1)
            self._add_snake(player_id, index, "Bot {0:02d}".format(index + 1))
            self.bot_ids.append(player_id)
        self.world.replenish_food()

    def _add_snake(self, player_id: str, slot: int, nick: str) -> Snake:
        x = 5 + (slot % 10) * 11
        y = 5 + (slot // 10) * 12
        body = [(x - offset, y) for offset in range(config.INITIAL_SNAKE_LENGTH)]
        snake = self.world.add_snake(player_id, body=body, direction="R")
        self.player_nicks[player_id] = nick
        self.player_slots[player_id] = slot
        return snake

    @staticmethod
    def _peer_uid(writer: asyncio.StreamWriter) -> int:
        peer_socket = writer.get_extra_info("socket")
        if peer_socket is None or not hasattr(socket, "SO_PEERCRED"):
            raise PermissionError("O servidor exige credenciais Unix do kernel.")
        _pid, peer_uid, _gid = struct.unpack(
            "3i",
            peer_socket.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            ),
        )
        if peer_uid < 1:
            raise PermissionError("UID Linux não elegível para jogar.")
        return peer_uid

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._active_connections += 1
        self._client_writers.add(writer)
        current_task = asyncio.current_task()
        if current_task is not None:
            self._client_tasks.add(current_task)
        session: Optional[ClientSession] = None
        invalid_count = 0
        limiter = MessageRateLimiter()
        try:
            if self._active_connections > self._max_connections:
                await self._send_direct(
                    writer, self._error("capacity", "O servidor está ocupado.")
                )
                return
            try:
                linux_uid = self._peer_uid(writer)
            except (OSError, PermissionError, struct.error):
                await self._send_direct(
                    writer, self._error("unauthorized", "Identidade Linux inválida.")
                )
                return
            while True:
                try:
                    raw = await asyncio.wait_for(
                        reader.readline(), timeout=self.idle_timeout
                    )
                except asyncio.TimeoutError:
                    break
                except (ValueError, asyncio.LimitOverrunError):
                    await self._send_direct(
                        writer,
                        self._error("invalid_request", "Mensagem inválida ou muito grande."),
                    )
                    break
                if not raw:
                    break
                if not raw.endswith(b"\n") or len(raw) > config.MAX_CLIENT_MESSAGE_BYTES:
                    await self._send_direct(
                        writer,
                        self._error("invalid_request", "Mensagem inválida ou muito grande."),
                    )
                    break
                try:
                    if not limiter.allow():
                        await self._send_direct(
                            writer,
                            self._error("rate_limited", "Limite de mensagens excedido."),
                        )
                        break
                    message = parse_client_message(raw)
                except UnsupportedVersionError:
                    await self._send_direct(
                        writer,
                        self._error(
                            "version_unsupported", "Versão de protocolo não suportada."
                        ),
                    )
                    break
                except ValueError:
                    invalid_count += 1
                    await self._send_direct(
                        writer,
                        self._error("invalid_request", "Mensagem inválida.")
                    )
                    if invalid_count >= config.MAX_PROTOCOL_FAILURES:
                        break
                    continue

                if session is None:
                    if message["t"] != "hello":
                        invalid_count += 1
                        await self._send_direct(
                            writer,
                            self._error("invalid_request", "Envie hello antes das ações."),
                        )
                        if invalid_count >= config.MAX_PROTOCOL_FAILURES:
                            break
                        continue
                    session = await self._create_session(
                        linux_uid, message, writer
                    )
                    if session is None:
                        break
                    continue

                if message["t"] == "hello":
                    invalid_count += 1
                    await self._send_direct(
                        writer,
                        self._error("invalid_request", "A sessão já foi iniciada."),
                    )
                    if invalid_count >= config.MAX_PROTOCOL_FAILURES:
                        break
                elif message["t"] == "dir":
                    self.world.set_direction(session.player_id, message["d"])
                elif message["t"] == "boost":
                    self.world.set_boost(session.player_id, message["on"])
                elif message["t"] == "resize":
                    session.cols = message["cols"]
                    session.rows = message["rows"]
                elif message["t"] == "bye":
                    break
        except (ConnectionError, OSError):
            logger.debug("Conexão de cliente Snake encerrada durante I/O")
        finally:
            if session is not None:
                await self._close_session(session)
            else:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            self._client_writers.discard(writer)
            if current_task is not None:
                self._client_tasks.discard(current_task)
            self._active_connections -= 1

    async def _create_session(
        self,
        linux_uid: int,
        hello: Dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> Optional[ClientSession]:
        try:
            identity = await asyncio.wait_for(
                self.identity_resolver(linux_uid), timeout=2.0
            )
        except PermissionError:
            await self._send_direct(
                writer,
                self._error("unauthorized", "UID Linux sem vínculo SIMPLE ativo."),
            )
            return None
        except (OSError, asyncio.TimeoutError, ValueError):
            logger.exception("Falha ao resolver identidade no Broker")
            await self._send_direct(
                writer, self._error("server_error", "Não foi possível iniciar a sessão.")
            )
            return None
        if not self._valid_identity(identity, linux_uid):
            await self._send_direct(
                writer, self._error("unauthorized", "Identidade SIMPLE inválida.")
            )
            return None

        user_id = identity["user_id"]
        existing = self.sessions.get(user_id)
        if existing is not None:
            await self._close_session(existing)
        if len(self.sessions) >= self.max_human_players:
            await self._send_direct(
                writer, self._error("capacity", "A sala atingiu o limite de jogadores.")
            )
            return None

        player_id = secrets.token_urlsafe(12)
        nick = hello.get("nick") or self._sanitize_nickname(identity["username"])
        occupied_slots = set(self.player_slots.values())
        occupied_cells = {
            position
            for snake in self.world.snakes.values()
            if snake.alive
            for position in snake.body
        }
        slot = None
        for candidate in range(40):
            if candidate in occupied_slots:
                continue
            x = 5 + (candidate % 10) * 11
            y = 5 + (candidate // 10) * 12
            body = [
                (x - offset, y)
                for offset in range(config.INITIAL_SNAKE_LENGTH)
            ]
            food_collision = bool(self.world.food.keys() & set(body))
            if not food_collision and not occupied_cells.intersection(body):
                slot = candidate
                break
        if slot is None:
            await self._send_direct(
                writer, self._error("capacity", "Não há posição livre na arena.")
            )
            return None
        try:
            self._add_snake(player_id, slot, nick)
        except ValueError:
            await self._send_direct(
                writer, self._error("capacity", "Não há posição livre na arena.")
            )
            return None

        session = ClientSession(
            linux_uid=linux_uid,
            binding_id=identity["binding_id"],
            user_id=user_id,
            session_id=secrets.token_urlsafe(18),
            player_id=player_id,
            nick=nick,
            cols=hello["cols"],
            rows=hello["rows"],
            writer=writer,
            identity_checked_at=time.monotonic(),
        )
        self.sessions[user_id] = session
        self.player_nicks[player_id] = nick
        session.writer_task = asyncio.create_task(self._writer_loop(session))
        welcome = {
            "t": "welcome",
            "v": config.PROTOCOL_VERSION,
            "session_id": session.session_id,
            "player_id": player_id,
            "arena": {"cols": self.world.width, "rows": self.world.height},
            "tick_hz": config.TICK_HZ,
        }
        if not await self._send_direct(writer, welcome):
            await self._close_session(session)
            return None
        return session

    @staticmethod
    def _valid_identity(identity: Any, linux_uid: int) -> bool:
        return (
            isinstance(identity, dict)
            and type(identity.get("linux_uid")) is int
            and identity["linux_uid"] == linux_uid
            and type(identity.get("binding_id")) is int
            and identity["binding_id"] > 0
            and type(identity.get("user_id")) is int
            and identity["user_id"] > 0
            and isinstance(identity.get("username"), str)
            and bool(identity["username"])
        )

    @staticmethod
    def _sanitize_nickname(value: str) -> str:
        visible = "".join(
            character
            for character in value
            if unicodedata.category(character) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        )
        visible = visible[: config.MAX_NICKNAME_LENGTH].strip()
        return visible or "Tripulante"

    async def _close_session(self, session: ClientSession) -> None:
        if self.sessions.get(session.user_id) is session:
            self.sessions.pop(session.user_id, None)
            self.world.remove_snake(session.player_id)
            self.player_nicks.pop(session.player_id, None)
            self.player_slots.pop(session.player_id, None)
        writer_task = session.writer_task
        if writer_task is not None and writer_task is not asyncio.current_task():
            writer_task.cancel()
            try:
                await writer_task
            except asyncio.CancelledError:
                pass
        session.writer.close()
        try:
            await session.writer.wait_closed()
        except OSError:
            pass

    async def _writer_loop(self, session: ClientSession) -> None:
        try:
            while True:
                payload, _message_type = await session.outgoing.get()
                session.writer.write(payload)
                await asyncio.wait_for(session.writer.drain(), timeout=2.0)
        except (asyncio.CancelledError, ConnectionError, OSError, asyncio.TimeoutError):
            session.writer.close()

    async def _send_direct(
        self, writer: asyncio.StreamWriter, message: Dict[str, Any]
    ) -> bool:
        payload = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        if len(payload) > config.MAX_SERVER_MESSAGE_BYTES:
            raise ValueError("Mensagem do servidor excede o limite permitido.")
        try:
            writer.write(payload)
            await asyncio.wait_for(writer.drain(), timeout=2.0)
            return True
        except (ConnectionError, OSError, asyncio.TimeoutError):
            return False

    @staticmethod
    def _error(code: str, message: str) -> Dict[str, str]:
        return {"t": "error", "code": code, "message": message}

    def _enqueue(self, session: ClientSession, message: Dict[str, Any]) -> None:
        payload = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        if len(payload) > config.MAX_SERVER_MESSAGE_BYTES:
            logger.error("Snapshot do Snake excede o limite de saída")
            session.writer.close()
            return
        if session.outgoing.full():
            queued: List[QueuedMessage] = []
            while not session.outgoing.empty():
                queued.append(session.outgoing.get_nowait())
            removed_state = False
            for queued_payload, message_type in queued:
                if not removed_state and message_type == "state":
                    removed_state = True
                    continue
                session.outgoing.put_nowait((queued_payload, message_type))
            if not removed_state:
                if message["t"] == "state":
                    return
                session.writer.close()
                return
        session.outgoing.put_nowait((payload, message["t"]))

    async def _tick_loop(self) -> None:
        while True:
            started = time.monotonic()
            self._schedule_identity_checks(started)
            for player_id, snake in self.world.snakes.items():
                if not snake.alive:
                    try:
                        self.world.respawn(player_id)
                    except ValueError as error:
                        if player_id not in self._respawn_waiting:
                            logger.warning(
                                "Respawn do Snake %s aguardando espaço livre: %s",
                                player_id,
                                error,
                            )
                            self._respawn_waiting.add(player_id)
                    else:
                        self._respawn_waiting.discard(player_id)
            step_bots(self.world, self.bot_ids)
            self.world.tick()
            self.world.replenish_food()
            self._broadcast_state_changes()
            delay = max(0.0, self.tick_interval - (time.monotonic() - started))
            await asyncio.sleep(delay)

    def _schedule_identity_checks(self, now: float) -> None:
        for session in list(self.sessions.values()):
            if (
                now - session.identity_checked_at
                < self.identity_revalidation_interval
                or session.user_id in self._identity_tasks
            ):
                continue
            task = asyncio.create_task(self._revalidate_identity(session))
            self._identity_tasks[session.user_id] = task

    async def _revalidate_identity(self, session: ClientSession) -> None:
        current_task = asyncio.current_task()
        try:
            try:
                identity = await asyncio.wait_for(
                    self.identity_resolver(session.linux_uid), timeout=2.0
                )
            except PermissionError:
                code = "unauthorized"
                message = "O vínculo ou a conta SIMPLE não está mais ativo."
            except (OSError, asyncio.TimeoutError, ValueError):
                logger.exception("Falha ao revalidar identidade no Broker")
                code = "server_error"
                message = "Não foi possível validar a sessão."
            else:
                if (
                    self._valid_identity(identity, session.linux_uid)
                    and identity["user_id"] == session.user_id
                    and identity["binding_id"] == session.binding_id
                ):
                    session.identity_checked_at = time.monotonic()
                    return
                code = "unauthorized"
                message = "O vínculo ou a conta SIMPLE foi alterado."

            if self.sessions.get(session.user_id) is session:
                await self._send_direct(
                    session.writer, self._error(code, message)
                )
                await self._close_session(session)
        finally:
            if (
                current_task is not None
                and self._identity_tasks.get(session.user_id) is current_task
            ):
                self._identity_tasks.pop(session.user_id, None)

    def _broadcast_state_changes(self) -> None:
        for session in list(self.sessions.values()):
            snake = self.world.snakes.get(session.player_id)
            if snake is None:
                continue
            if session.was_alive and not snake.alive:
                self._enqueue(
                    session,
                    {
                        "t": "dead",
                        "score": len(snake.body),
                        "length": len(snake.body),
                        "killer": snake.last_killer,
                        "tick": self.world.tick_count,
                    },
                )
                session.was_alive = False
            elif snake.alive:
                session.was_alive = True
            self._enqueue(session, self._snapshot(session, snake))

    def _snapshot(self, session: ClientSession, player: Snake) -> Dict[str, Any]:
        head_x, head_y = player.body[0]
        left = max(0, min(self.world.width - session.cols, head_x - session.cols // 2))
        top = max(0, min(self.world.height - session.rows, head_y - session.rows // 2))
        right = left + session.cols
        bottom = top + session.rows
        margin = 2
        snakes = []
        for player_id, snake in self.world.snakes.items():
            if not snake.alive:
                continue
            body = [
                [x, y]
                for x, y in snake.body
                if left - margin <= x < right + margin
                and top - margin <= y < bottom + margin
            ]
            if not body:
                continue
            snakes.append(
                {
                    "id": player_id,
                    "nick": self.player_nicks.get(player_id, "Tripulante"),
                    "body": body,
                    "length": len(snake.body),
                    "alive": snake.alive,
                    "color": 0,
                    "glyph": "@",
                }
            )
        food = [
            {"x": x, "y": y, "value": value}
            for (x, y), value in self.world.food.items()
            if left - margin <= x < right + margin
            and top - margin <= y < bottom + margin
        ]
        leaderboard = []
        for rank, entry in enumerate(
            self.world.leaderboard(config.LEADERBOARD_SIZE), start=1
        ):
            leaderboard.append(
                {
                    "rank": rank,
                    "nick": self.player_nicks.get(entry["player_id"], "Tripulante"),
                    "length": entry["length"],
                }
            )
        return {
            "t": "state",
            "tick": self.world.tick_count,
            "player": {
                "id": session.player_id,
                "length": len(player.body),
                "alive": player.alive,
                "kills": player.kills,
                "deaths": player.deaths,
                "boosting": player.boosting,
            },
            "snakes": snakes,
            "food": food,
            "leaderboard": leaderboard,
        }

async def _run_service(
    socket_path: Path, broker_socket: Path, socket_gid: Optional[int] = None
) -> None:
    resolver = BrokerIdentityResolver(broker_socket)
    server = SnakeServer(identity_resolver=resolver)
    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_requested.set)
    await server.start_unix(socket_path, socket_gid=socket_gid)
    try:
        await stop_requested.wait()
    finally:
        await server.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Servidor autoritativo do Snake SIMPLE")
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--broker-socket", required=True, type=Path)
    parser.add_argument("--socket-gid", type=int)
    arguments = parser.parse_args()
    try:
        if arguments.socket_gid is not None and arguments.socket_gid < 0:
            parser.error("--socket-gid deve ser um GID não negativo")
        asyncio.run(
            _run_service(
                arguments.socket, arguments.broker_socket, arguments.socket_gid
            )
        )
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, "Falha ao iniciar o servidor Snake: {0}\n".format(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
