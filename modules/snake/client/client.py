"""Interactive curses client and local solo game."""

from __future__ import annotations

import argparse
import curses
import json
import queue
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..engine import config
from ..engine.simulation import HeadlessSimulation
from ..engine.world import Snake, World
from .render import (
    FOOD_COLOR,
    SNAKE_COLOR_PALETTE,
    render_world,
    render_world_styled,
    safe_text,
)

CLIENT_SCHEMAS = {
    "dir": {"d"},
    "boost": {"on"},
    "resize": {"cols", "rows"},
    "ping": set(),
    "bye": set(),
}
SERVER_TYPES = {"welcome", "state", "dead", "error"}


def encode_message(message: Dict[str, Any]) -> bytes:
    if not isinstance(message, dict) or not isinstance(message.get("t"), str):
        raise ValueError("Mensagem do cliente inválida.")
    message_type = message["t"]
    if message_type == "hello":
        required = {"t", "v", "cols", "rows"}
        allowed = required | {"nick"}
        if set(message) - allowed or not required.issubset(message):
            raise ValueError("Campos inválidos para hello.")
        if type(message["v"]) is not int or message["v"] != config.PROTOCOL_VERSION:
            raise ValueError("Versão de protocolo inválida.")
        if type(message["cols"]) is not int or type(message["rows"]) is not int:
            raise ValueError("Dimensões de terminal inválidas.")
    else:
        fields = CLIENT_SCHEMAS.get(message_type)
        if fields is None or set(message) != fields | {"t"}:
            raise ValueError("Tipo ou campos de mensagem inválidos.")
        if message_type == "dir" and message["d"] not in {"U", "D", "L", "R"}:
            raise ValueError("Direção inválida.")
        if message_type == "boost" and type(message["on"]) is not bool:
            raise ValueError("Estado do boost inválido.")
        if message_type == "resize":
            if type(message["cols"]) is not int or type(message["rows"]) is not int:
                raise ValueError("Dimensões de terminal inválidas.")
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    ) + b"\n"
    if len(payload) > config.MAX_CLIENT_MESSAGE_BYTES:
        raise ValueError("Mensagem do cliente excede o limite permitido.")
    return payload


def decode_server_message(raw: bytes) -> Dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > config.MAX_SERVER_MESSAGE_BYTES:
        raise ValueError("Mensagem do servidor vazia ou muito grande.")
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Resposta inválida do servidor Snake.") from error
    if (
        not isinstance(message, dict)
        or not isinstance(message.get("t"), str)
        or message["t"] not in SERVER_TYPES
    ):
        raise ValueError("Tipo de resposta inválido do servidor Snake.")
    if message["t"] == "error" and not isinstance(message.get("message"), str):
        raise ValueError("Erro inválido retornado pelo servidor Snake.")
    if message["t"] == "welcome" and (
        message.get("v") != config.PROTOCOL_VERSION
        or not isinstance(message.get("session_id"), str)
        or not isinstance(message.get("player_id"), str)
        or not isinstance(message.get("arena"), dict)
    ):
        raise ValueError("Boas-vindas inválidas do servidor Snake.")
    if message["t"] == "state" and (
        type(message.get("tick")) is not int
        or not isinstance(message.get("player"), dict)
        or not isinstance(message.get("snakes"), list)
        or not isinstance(message.get("food"), list)
        or not isinstance(message.get("leaderboard"), list)
        or any(
            not isinstance(snake, dict)
            or (
                "color" in snake
                and (
                    type(snake["color"]) is not int
                    or not 0 <= snake["color"] < config.SNAKE_COLOR_COUNT
                )
            )
            for snake in message.get("snakes", [])
        )
        or (
            "records" in message
            and not isinstance(message.get("records"), list)
        )
        or (
            "minimap" in message
            and (
                not isinstance(message["minimap"], list)
                or len(message["minimap"]) != config.MINIMAP_HEIGHT
                or any(
                    not isinstance(row, str)
                    or len(row) != config.MINIMAP_WIDTH
                    or any(character not in " .#o@" for character in row)
                    for row in message["minimap"]
                )
            )
        )
        or (
            "kill_feed" in message
            and (
                not isinstance(message["kill_feed"], list)
                or len(message["kill_feed"]) > config.KILL_FEED_SIZE
                or any(
                    not isinstance(event, dict)
                    or set(event) != {"killer", "victim"}
                    or (
                        event["killer"] is not None
                        and (
                            not isinstance(event["killer"], str)
                            or len(event["killer"]) > config.MAX_NICKNAME_LENGTH
                        )
                    )
                    or not isinstance(event["victim"], str)
                    or not event["victim"]
                    or len(event["victim"]) > config.MAX_NICKNAME_LENGTH
                    for event in message["kill_feed"]
                )
            )
        )
    ):
        raise ValueError("Estado inválido do servidor Snake.")
    return message


class NetworkSession:
    def __init__(
        self,
        socket_path: Path,
        cols: int = 80,
        rows: int = 24,
        timeout: float = 5.0,
    ) -> None:
        if not socket_path.is_absolute():
            raise ValueError("O caminho do servidor deve ser absoluto.")
        self.socket_path = socket_path
        self.cols = max(config.MIN_VIEW_COLS, min(config.MAX_VIEW_COLS, cols))
        self.rows = max(config.MIN_VIEW_ROWS, min(config.MAX_VIEW_ROWS, rows))
        self.timeout = timeout
        self.outgoing: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=32)
        self.incoming: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=2)
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.connected = threading.Event()

    def start(self) -> None:
        if self.thread is not None:
            raise RuntimeError("A sessão de rede já foi iniciada.")
        self.thread = threading.Thread(target=self._run, name="snake-client-io")
        self.thread.start()

    def send(self, message: Dict[str, Any]) -> None:
        payload = encode_message(message)
        if self.outgoing.full():
            try:
                self.outgoing.get_nowait()
            except queue.Empty:
                pass
        self.outgoing.put_nowait(json.loads(payload.decode("utf-8")))

    def latest(self) -> Optional[Dict[str, Any]]:
        newest = None
        while True:
            try:
                newest = self.incoming.get_nowait()
            except queue.Empty:
                return newest

    def close(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=self.timeout + 1)

    def _publish(self, message: Dict[str, Any]) -> None:
        if self.incoming.full():
            try:
                self.incoming.get_nowait()
            except queue.Empty:
                pass
        self.incoming.put_nowait(message)

    def _run(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        buffer = bytearray()
        try:
            connection.connect(str(self.socket_path))
            connection.settimeout(0.2)
            connection.sendall(
                encode_message(
                    {
                        "t": "hello",
                        "v": config.PROTOCOL_VERSION,
                        "cols": self.cols,
                        "rows": self.rows,
                    }
                )
            )
            self.connected.set()
            while not self.stop_event.is_set():
                self._flush_outgoing(connection)
                try:
                    chunk = connection.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("O servidor encerrou a conexão.")
                buffer.extend(chunk)
                if len(buffer) > config.MAX_SERVER_MESSAGE_BYTES:
                    raise ValueError("Resposta do servidor excedeu o limite.")
                while b"\n" in buffer:
                    line, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    self._publish(decode_server_message(bytes(line)))
        except (OSError, ValueError, ConnectionError) as error:
            self._publish(
                {
                    "t": "error",
                    "code": "connection_error",
                    "message": safe_text(str(error)),
                }
            )
        finally:
            connection.close()

    def _flush_outgoing(self, connection: socket.socket) -> None:
        for _ in range(8):
            try:
                message = self.outgoing.get_nowait()
            except queue.Empty:
                return
            try:
                connection.sendall(encode_message(message))
            except OSError as error:
                raise ConnectionError("Não foi possível enviar a ação ao servidor.") from error


class SoloGame:
    def __init__(self, seed: Optional[int] = None, bot_count: int = config.BOT_COUNT):
        if type(bot_count) is not int or not 0 <= bot_count <= config.BOT_COUNT:
            raise ValueError("A quantidade de bots é inválida.")
        self.simulation = HeadlessSimulation(snake_count=max(1, bot_count), seed=seed)
        self.world = self.simulation.world
        self.bot_ids = self.simulation.bot_ids
        if bot_count == 0:
            self.world.remove_snake(self.bot_ids.pop())
        self.player_id = "local"
        player_body = [
            (60 - offset, 30) for offset in range(config.INITIAL_SNAKE_LENGTH)
        ]
        for position in player_body:
            self.world.food.pop(position, None)
        self.world.add_snake(
            self.player_id,
            body=player_body,
            direction="R",
        )
        self.world.replenish_food()
        self.boosting = False
        self.kill_feed: List[Dict[str, Optional[str]]] = []

    def turn(self, direction: str) -> bool:
        return self.world.set_direction(self.player_id, direction)

    def set_boost(self, enabled: bool) -> bool:
        self.boosting = enabled
        return self.world.set_boost(self.player_id, enabled)

    def step(self) -> None:
        for player_id in self.bot_ids:
            snake = self.world.snakes[player_id]
            if not snake.alive:
                try:
                    self.world.respawn(player_id)
                except ValueError:
                    pass
        self.simulation.step()
        for victim_id, killer_id in self.world.kill_events:
            victim = self._nickname(victim_id)
            killer = self._nickname(killer_id) if killer_id is not None else None
            self.kill_feed.append({"killer": killer, "victim": victim})
        if len(self.kill_feed) > config.KILL_FEED_SIZE:
            del self.kill_feed[:-config.KILL_FEED_SIZE]
        player = self.world.snakes[self.player_id]
        if not player.alive:
            try:
                self.world.respawn(self.player_id)
            except ValueError:
                pass

    def snapshot(self) -> Dict[str, Any]:
        snakes = []
        for index, (player_id, snake) in enumerate(self.world.snakes.items()):
            if snake.alive:
                snakes.append(
                    {
                        "id": player_id,
                        "nick": "Você" if player_id == self.player_id else player_id,
                        "body": [list(position) for position in snake.body],
                        "length": len(snake.body),
                        "alive": True,
                        "color": index % config.SNAKE_COLOR_COUNT,
                        "glyph": "@",
                    }
                )
        player = self.world.snakes[self.player_id]
        return {
            "t": "state",
            "tick": self.world.tick_count,
            "player": {
                "id": self.player_id,
                "length": len(player.body),
                "alive": player.alive,
                "kills": player.kills,
                "deaths": player.deaths,
                "boosting": player.boosting,
            },
            "snakes": snakes,
            "food": [
                {"x": x, "y": y, "value": value}
                for (x, y), value in self.world.food.items()
            ],
            "leaderboard": [],
            "records": [],
            "minimap": self.world.minimap(self.player_id),
            "kill_feed": [dict(event) for event in self.kill_feed],
        }

    def _nickname(self, player_id: str) -> str:
        if player_id == self.player_id:
            return "Você"
        if player_id.startswith("bot-"):
            return "Bot {0}".format(player_id[4:])
        return "Tripulante"


class SnakeClient:
    def __init__(
        self, solo: bool = False, socket_path: Optional[Path] = None
    ) -> None:
        self.solo = solo
        self.game = SoloGame() if solo else None
        self.socket_path = socket_path
        self.network: Optional[NetworkSession] = None
        self.snapshot: Dict[str, Any] = (
            self.game.snapshot() if self.game is not None else {}
        )
        self.arena_cols = config.ARENA_WIDTH
        self.arena_rows = config.ARENA_HEIGHT
        self.status = "Modo solo" if solo else "Conectando ao servidor..."
        self.boost_enabled = False
        self.color_pairs: Dict[str, int] = {}

    def run(self, screen: Any) -> int:
        curses.curs_set(0)
        self._initialize_colors()
        screen.keypad(True)
        screen.timeout(50)
        if not self.solo:
            if self.socket_path is None:
                self.status = "Socket do servidor Snake não configurado."
            else:
                self.network = NetworkSession(
                    self.socket_path,
                    cols=max(config.MIN_VIEW_COLS, screen.getmaxyx()[1] // 2),
                    rows=max(config.MIN_VIEW_ROWS, screen.getmaxyx()[0] - 5),
                )
                self.network.start()
        previous_tick = time.monotonic()
        try:
            while True:
                key = screen.getch()
                if key in (ord("q"), ord("Q"), 27):
                    if self.network is not None:
                        try:
                            self.network.send({"t": "bye"})
                        except ValueError:
                            pass
                    return 0
                direction = self._direction_for_key(key)
                if direction is not None:
                    self._send_direction(direction)
                elif key == ord(" "):
                    self.boost_enabled = not self.boost_enabled
                    if self.game is not None:
                        self.game.set_boost(self.boost_enabled)
                    elif self.network is not None:
                        self.network.send({"t": "boost", "on": self.boost_enabled})

                if self.game is not None and time.monotonic() - previous_tick >= 0.1:
                    self.game.step()
                    self.snapshot = self.game.snapshot()
                    previous_tick = time.monotonic()
                if self.network is not None:
                    message = self.network.latest()
                    if message is not None:
                        self._handle_server_message(message)
                self._draw(screen)
        finally:
            if self.network is not None:
                self.network.close()

    @staticmethod
    def _direction_for_key(key: int) -> Optional[str]:
        return {
            curses.KEY_UP: "U",
            ord("w"): "U",
            ord("k"): "U",
            curses.KEY_DOWN: "D",
            ord("s"): "D",
            ord("j"): "D",
            curses.KEY_LEFT: "L",
            ord("a"): "L",
            ord("h"): "L",
            curses.KEY_RIGHT: "R",
            ord("d"): "R",
            ord("l"): "R",
        }.get(key)

    def _send_direction(self, direction: str) -> None:
        if self.game is not None:
            self.game.turn(direction)
        elif self.network is not None:
            self.network.send({"t": "dir", "d": direction})

    def _handle_server_message(self, message: Dict[str, Any]) -> None:
        if message["t"] == "welcome":
            self.status = "Conectado · aguardando arena..."
            arena = message.get("arena", {})
            if type(arena.get("cols")) is int:
                self.arena_cols = arena["cols"]
            if type(arena.get("rows")) is int:
                self.arena_rows = arena["rows"]
        elif message["t"] == "state":
            self.snapshot = message
            self.status = "Conectado · Q sair"
        elif message["t"] == "dead":
            self.status = "Eliminado · aguardando respawn"
        elif message["t"] == "error":
            self.status = safe_text(message.get("message", "Falha na conexão."))

    def _draw(self, screen: Any) -> None:
        height, width = screen.getmaxyx()
        screen.erase()
        if self.snapshot.get("t") != "state":
            message = (
                "Aguardando estado inicial da arena..."
                if self.status.startswith("Conectando")
                or self.status.startswith("Conectado")
                else safe_text(self.status)
            )
            if height > 0 and width > 1:
                row = max(0, (height - 1) // 2)
                column = max(0, (width - len(message)) // 2)
                try:
                    screen.addnstr(row, column, message, max(0, width - column - 1))
                except curses.error:
                    pass
            if height > 0:
                try:
                    screen.addnstr(height - 1, 0, self.status, max(0, width - 1))
                except curses.error:
                    pass
            screen.refresh()
            return

        lines, styles = render_world_styled(
            self.snapshot,
            width,
            max(1, height - 1),
            arena_cols=self.arena_cols,
            arena_rows=self.arena_rows,
        )
        for y, line in enumerate(lines):
            try:
                screen.addnstr(y, 0, line, max(0, width - 1))
            except curses.error:
                pass
        for y, column, text, color in styles:
            pair = self.color_pairs.get(color)
            if pair is None or y >= height - 1 or column >= width - 1:
                continue
            try:
                attributes = curses.color_pair(pair)
                if color == FOOD_COLOR or text == "@@":
                    attributes |= curses.A_BOLD
                screen.addnstr(
                    y,
                    column,
                    text,
                    min(len(text), width - column - 1),
                    attributes,
                )
            except curses.error:
                pass
        if height > 0:
            try:
                screen.addnstr(height - 1, 0, self.status, max(0, width - 1))
            except curses.error:
                pass
        screen.refresh()

    def _initialize_colors(self) -> None:
        self.color_pairs = {}
        if not curses.has_colors():
            return
        try:
            curses.start_color()
        except curses.error:
            return
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        for pair, name in enumerate(SNAKE_COLOR_PALETTE + (FOOD_COLOR,), start=1):
            color = getattr(curses, "COLOR_" + name.upper())
            try:
                curses.init_pair(pair, color, background)
            except curses.error:
                continue
            self.color_pairs[name] = pair


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Jogue Snake no terminal")
    parser.add_argument("--solo", action="store_true", help="jogar com bots sem servidor")
    parser.add_argument("--socket", type=Path, help="socket Unix do servidor multiplayer")
    arguments = parser.parse_args(argv)
    if arguments.solo == (arguments.socket is not None):
        parser.error("use --solo ou --socket SOCKET")
    try:
        return curses.wrapper(
            SnakeClient(solo=arguments.solo, socket_path=arguments.socket).run
        )
    except (OSError, ValueError) as error:
        print(
            "Não foi possível iniciar Snake: {0}".format(error),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
