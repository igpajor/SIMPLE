#!/usr/bin/env python3
"""Curses interface for the SIMPLE Broker chat."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import termios
import time
import unicodedata
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import curses
except ImportError as error:
    print(
        "O módulo curses do Python não está disponível; instale python3-curses.",
        file=sys.stderr,
    )
    raise SystemExit(1) from error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from broker_client import request as broker_request


class ChatError(Exception):
    pass


def safe_text(value: Any) -> str:
    text = re.sub(
        r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))",
        "",
        str(value),
    )
    return "".join(
        char
        for char in text
        if unicodedata.category(char) not in {"Cc", "Cs"}
        or char in "\n\t"
    )


def char_width(char: str) -> int:
    if unicodedata.combining(char) or unicodedata.category(char) in {"Cf", "Mn"}:
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def display_width(text: str) -> int:
    return sum(char_width(char) for char in text)


def wrap_text(text: str, width: int) -> list[str]:
    width = max(width, 1)
    lines: list[str] = []
    for source_line in safe_text(text).splitlines() or [""]:
        if not source_line:
            lines.append("")
            continue
        line = ""
        used = 0
        for char in source_line:
            char_size = char_width(char)
            if line and used + char_size > width:
                lines.append(line)
                line = ""
                used = 0
            line += char
            used += char_size
        lines.append(line)
    return lines


def local_datetime(created_at: str, timezone: ZoneInfo) -> dt.datetime:
    parsed = dt.datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=dt.timezone.utc).astimezone(timezone)


class ChatTui:
    def __init__(
        self,
        token: str,
        user_id: int,
        username: str,
        is_admin: bool,
        socket_path: str,
        channel: str,
        room_name: str,
        auto_refresh: bool,
        refresh_seconds: int,
        history_limit: int,
    ) -> None:
        self.token = token
        self.user_id = user_id
        self.username = username
        self.is_admin = is_admin
        self.socket_path = socket_path
        self.channel = channel
        self.room_name = room_name
        self.auto_refresh = auto_refresh
        self.refresh_seconds = refresh_seconds
        self.history_limit = history_limit
        self.messages: list[dict[str, Any]] = []
        self.online_users: list[str] = []
        self.typing_users: list[str] = []
        self.pinned: list[dict[str, Any]] = []
        self.unread_rooms: list[dict[str, Any]] = []
        self.last_id = 0
        self.history_before: int | None = None
        self.history_has_more = False
        self.history_loaded = False
        self.scroll_offset = 0
        self.input_text = ""
        self.cursor = 0
        self.input_history: list[str] = []
        self.history_cursor: int | None = None
        self.status = ""
        self.status_until = 0.0
        self.typing_active = False
        self.timezone = ZoneInfo(os.environ.get("CHAT_TIMEZONE", "America/Sao_Paulo"))
        self.width = 80
        self.height = 24
        self.colors: dict[str, int] = {}
        self.last_typing_update = 0.0
        self.dm_parent_state: dict[str, Any] | None = None
        self.dm_states: dict[str, dict[str, Any]] = {}

    ROOM_STATE_FIELDS = (
        "channel",
        "room_name",
        "messages",
        "online_users",
        "typing_users",
        "pinned",
        "last_id",
        "history_before",
        "history_has_more",
        "history_loaded",
        "scroll_offset",
        "input_text",
        "cursor",
        "typing_active",
        "last_typing_update",
    )

    def request(self, method: str, **fields: str) -> dict[str, Any]:
        try:
            response = broker_request(
                self.socket_path,
                method,
                {"token": self.token, **fields},
            )
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise ChatError(f"Falha ao consultar o Broker: {error}") from error
        if response.get("ok") is not True:
            raise ChatError(str(response.get("error", "Operação recusada pelo Broker.")))
        return response

    def set_status(self, message: str, seconds: float = 5.0) -> None:
        self.status = safe_text(message)
        self.status_until = time.monotonic() + seconds

    def sync(self) -> int:
        previous_last_id = self.last_id
        response = self.request(
            "chat.sync", channel=self.channel, after_id=str(self.last_id)
        )
        new_messages = response.get("messages", [])
        if not isinstance(new_messages, list):
            raise ChatError("O Broker retornou uma lista de mensagens inválida.")
        if self.last_id == 0:
            initial_window = response.get("window", [])
            if not isinstance(initial_window, list):
                raise ChatError("O Broker retornou uma janela de mensagens inválida.")
            self.messages = initial_window[-self.history_limit :]
            self.history_loaded = False
            self.history_has_more = (
                response.get("has_more") is True
                or len(initial_window) > len(self.messages)
            )
            self.history_before = (
                int(self.messages[0]["id"]) if self.messages else None
            )
        elif new_messages:
            self.messages.extend(new_messages)
            if self.history_loaded:
                if len(self.messages) > 500:
                    self.messages = self.messages[-500:]
                    self.history_before = int(self.messages[0]["id"])
                    self.history_has_more = True
            else:
                self.messages = self.messages[-self.history_limit :]
                if self.messages:
                    self.history_before = int(self.messages[0]["id"])
                    self.history_has_more = True
        last_id = response.get("last_id", self.last_id)
        if (
            not isinstance(last_id, int)
            or isinstance(last_id, bool)
            or last_id < self.last_id
        ):
            raise ChatError("O Broker retornou um cursor de mensagens inválido.")
        self.last_id = last_id
        self.online_users = response.get("online_users", [])
        self.typing_users = response.get("typing_users", [])
        self.pinned = response.get("pinned", [])
        unread_rooms = response.get("unread_rooms", [])
        if not isinstance(unread_rooms, list):
            raise ChatError("O Broker retornou notificações inválidas.")
        self.unread_rooms = unread_rooms
        if self.channel.startswith("dm:") and previous_last_id > 0:
            incoming = next(
                (
                    message
                    for message in reversed(new_messages)
                    if int(message.get("user_id", self.user_id)) != self.user_id
                ),
                None,
            )
            if incoming is not None:
                self.set_status(
                    f"Nova mensagem de {safe_text(incoming.get('username', 'usuário'))}: "
                    f"{safe_text(incoming.get('body', ''))}"
                )
        return len(new_messages)

    def capture_room_state(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self.ROOM_STATE_FIELDS}

    def restore_room_state(self, state: dict[str, Any]) -> None:
        for field in self.ROOM_STATE_FIELDS:
            setattr(self, field, state[field])

    def open_dm(self, channel: str, room_name: str) -> None:
        if self.typing_active:
            self.mark_typing(False)
        if self.channel.startswith("dm:"):
            self.dm_states[self.channel] = self.capture_room_state()
        elif self.dm_parent_state is None:
            self.dm_parent_state = self.capture_room_state()

        cached = self.dm_states.get(channel)
        if cached is not None:
            self.restore_room_state(cached)
            self.room_name = room_name
            self.sync()
        else:
            self.channel = channel
            self.room_name = room_name
            self.messages = []
            self.online_users = []
            self.typing_users = []
            self.pinned = []
            self.last_id = 0
            self.history_before = None
            self.history_has_more = False
            self.history_loaded = False
            self.scroll_offset = 0
            self.input_text = ""
            self.cursor = 0
            self.typing_active = False
            self.sync()

    def return_to_parent(self) -> None:
        if self.dm_parent_state is None:
            self.set_status("Você já está na sala principal.")
            return
        if self.typing_active:
            self.mark_typing(False)
        if self.channel.startswith("dm:"):
            self.dm_states[self.channel] = self.capture_room_state()
        parent_state = self.dm_parent_state
        self.dm_parent_state = None
        self.restore_room_state(parent_state)
        self.sync()

    def reset_channel(self, channel: str, room_name: str) -> None:
        if self.typing_active:
            self.mark_typing(False)
        self.channel = channel
        self.room_name = room_name
        self.messages = []
        self.last_id = 0
        self.history_before = None
        self.history_has_more = False
        self.history_loaded = False
        self.scroll_offset = 0
        self.sync()

    def mark_typing(self, active: bool) -> None:
        value = "1" if active else "0"
        self.request("chat.typing", channel=self.channel, active=value)
        self.typing_active = active
        self.last_typing_update = time.monotonic()

    def load_history(self) -> None:
        if not self.messages:
            self.set_status("Não há mensagens carregadas para buscar histórico.")
            return
        if self.history_before is None:
            self.set_status("Não há mensagens anteriores.")
            return
        response = self.request(
            "chat.history",
            channel=self.channel,
            before_id=str(self.history_before),
        )
        older = response.get("messages", [])
        if not isinstance(older, list):
            raise ChatError("O Broker retornou uma lista de histórico inválida.")
        if not older:
            self.history_has_more = False
            self.set_status("Não há mensagens anteriores.")
            return
        previous_row_count = len(self.all_display_rows())
        combined = older + self.messages
        self.messages = combined[-500:]
        self.history_loaded = True
        self.history_before = int(self.messages[0]["id"])
        self.history_has_more = (
            response.get("has_more") is True or len(combined) > len(self.messages)
        )
        self.scroll_offset += max(0, len(self.all_display_rows()) - previous_row_count)
        self.set_status(f"{len(older)} mensagem(ns) anterior(es) carregada(s).")

    def message_display_rows(
        self, message: dict[str, Any], width: int | None = None
    ) -> list[tuple[str, str]]:
        created = local_datetime(str(message["created_at"]), self.timezone)
        username = str(message["username"])
        user_id = int(message["user_id"])
        body = safe_text(message["body"])
        message_id = int(message["id"])
        if int(message.get("mentioned", 0)):
            author = f"@ você · {username}"
            color = "warning"
        elif user_id == self.user_id:
            author = "você" if username == self.username else username
            color = "success"
        else:
            author = username
            color = f"user{sum(map(ord, username)) % 6}"
        prefix = f"[#{message_id} {created:%H:%M}] {author}: "
        continuation = " " * min(len(prefix), 16)
        available = max((width if width is not None else self.width) - 4, 12)
        wrapped = wrap_text(body, max(available - len(prefix), 10))
        lines = [(prefix + wrapped[0], color)]
        lines.extend((continuation + line, color) for line in wrapped[1:])
        if message.get("pinned_at"):
            lines.append((f"  📌 mensagem fixada · #{message_id}", "warning"))
        reactions = message.get("reactions")
        if reactions:
            lines.append((f"  {reactions}", "content"))
        return lines

    def all_display_rows(self, width: int | None = None) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        last_date: str | None = None
        for message in self.messages:
            created = local_datetime(str(message["created_at"]), self.timezone)
            if created.date().isoformat() != last_date:
                last_date = created.date().isoformat()
                rows.append((last_date, "dim"))
            rows.extend(self.message_display_rows(message, width))
        if self.history_has_more:
            rows.insert(0, ("Há mensagens anteriores · /historico", "dim"))
        return rows

    def draw_text(
        self,
        window: Any,
        y: int,
        x: int,
        text: str,
        color: str = "normal",
        width: int | None = None,
    ) -> None:
        if y < 0 or y >= self.height or x >= self.width:
            return
        text = safe_text(text).replace("\n", " ").replace("\t", " ")
        limit = max(0, (self.width - x - 1) if width is None else width)
        clipped: list[str] = []
        used = 0
        for char in text:
            size = char_width(char)
            if used + size > limit:
                break
            clipped.append(char)
            used += size
        value = "".join(clipped)
        if not value:
            return
        try:
            window.addstr(y, x, value, self.colors.get(color, curses.A_NORMAL))
        except curses.error:
            pass

    def notification_hint(self) -> str:
        if not self.unread_rooms:
            return ""
        if self.channel == "global":
            room = next(
                (
                    unread
                    for unread in self.unread_rooms
                    if unread.get("kind") == "direct"
                ),
                None,
            )
            if room is not None and room.get("peer_username"):
                username = safe_text(room["peer_username"])
                count = room.get("unread_count", 1)
                return f"+{count} DM [{username}] · F2 ou /dm {username}"
        room = next(
            (
                unread
                for unread in self.unread_rooms
                if unread.get("kind") != "direct"
            ),
            None,
        )
        if room is None:
            return ""
        channel = safe_text(room.get("channel", "global"))
        count = room.get("unread_count", 0)
        return f"{count} nova(s) em {room.get('name', channel)} · /sala {channel}"

    def _draw_input(
        self, window: Any, y: int, x: int, available_width: int
    ) -> None:
        prompt = "MENSAGEM > "
        input_width = max(1, available_width - len(prompt))
        cursor_cell = display_width(self.input_text[: self.cursor])
        start_cell = max(0, cursor_cell - input_width + 1)
        visible: list[str] = []
        used = 0
        skipped = 0
        for char in self.input_text:
            char_cells = char_width(char)
            if skipped + char_cells <= start_cell:
                skipped += char_cells
                continue
            if used + char_cells > input_width:
                break
            visible.append(char)
            used += char_cells
        self.draw_text(
            window, y, x, prompt + "".join(visible), "input", available_width
        )
        try:
            window.move(
                y,
                min(
                    x + available_width - 1,
                    x + len(prompt) + max(0, cursor_cell - start_cell),
                ),
            )
        except curses.error:
            pass

    def _draw_room(self, screen: Any) -> None:
        if self.height < 6 or self.width < 30:
            self.draw_text(screen, 0, 0, "Aumente o terminal para usar o chat.", "warning")
            return
        self.draw_text(screen, 0, 1, f"SIMPLE · {self.room_name}", "title")
        online = ", ".join(
            "você" if name == self.username else safe_text(name)
            for name in self.online_users
        )
        online_line = (
            f"Online ({len(self.online_users)}): {online}"
            if online
            else "Ninguém online."
        )
        self.draw_text(screen, 1, 1, online_line, "success")
        notification = self.notification_hint()
        if notification:
            self.draw_text(screen, 2, 1, notification, "warning")
        elif self.typing_users:
            typing = ", ".join(safe_text(name) for name in self.typing_users)
            self.draw_text(screen, 2, 1, f"Digitando: {typing}", "success")
        elif self.pinned:
            pinned = "Fixada: " + safe_text(self.pinned[0].get("body", ""))
            self.draw_text(screen, 2, 1, pinned, "warning")
        try:
            screen.hline(3, 0, curses.ACS_HLINE, self.width)
        except curses.error:
            pass

        message_rows = self.all_display_rows()
        pane_top = 4
        pane_bottom = self.height - 3
        pane_height = max(0, pane_bottom - pane_top)
        max_offset = max(0, len(message_rows) - pane_height)
        self.scroll_offset = min(self.scroll_offset, max_offset)
        end = len(message_rows) - self.scroll_offset
        start = max(0, end - pane_height)
        visible = message_rows[start:end]
        for index, (text, color) in enumerate(visible):
            self.draw_text(screen, pane_top + index, 2, text, color)
        if self.scroll_offset:
            self.draw_text(
                screen,
                pane_top,
                max(1, self.width - 17),
                "↑ histórico",
                "dim",
                16,
            )

        input_y = self.height - 1
        status_y = self.height - 2
        self._draw_input(screen, input_y, 1, self.width - 3)
        if self.status and time.monotonic() < self.status_until:
            self.draw_text(screen, status_y, 1, self.status, "warning")
        else:
            hint = (
                f"Autoatualização {self.refresh_seconds}s · /ajuda · Esc sai"
                if self.auto_refresh
                else "Enter vazio atualiza · /ajuda · Esc sai"
            )
            self.draw_text(screen, status_y, 1, hint, "dim")

    def _draw_dm_window(self, screen: Any) -> None:
        if self.height < 12 or self.width < 48:
            self._draw_room(screen)
            self.draw_text(
                screen,
                self.height - 2,
                1,
                "DM aberta · Esc ou /voltar retorna à sala",
                "warning",
            )
            return
        panel_height = max(10, int(self.height * 0.78))
        panel_width = max(40, int(self.width * 0.76))
        panel_height = min(panel_height, self.height - 2)
        panel_width = min(panel_width, self.width - 2)
        top = (self.height - panel_height) // 2
        left = (self.width - panel_width) // 2
        for row in range(top + 1, top + panel_height - 1):
            try:
                screen.addstr(row, left + 1, " " * (panel_width - 2))
            except curses.error:
                pass
        try:
            screen.addch(top, left, curses.ACS_ULCORNER)
            screen.addch(top, left + panel_width - 1, curses.ACS_URCORNER)
            screen.addch(top + panel_height - 1, left, curses.ACS_LLCORNER)
            screen.addch(
                top + panel_height - 1,
                left + panel_width - 1,
                curses.ACS_LRCORNER,
            )
            screen.hline(top, left + 1, curses.ACS_HLINE, panel_width - 2)
            screen.hline(
                top + panel_height - 1,
                left + 1,
                curses.ACS_HLINE,
                panel_width - 2,
            )
            screen.vline(top + 1, left, curses.ACS_VLINE, panel_height - 2)
            screen.vline(
                top + 1,
                left + panel_width - 1,
                curses.ACS_VLINE,
                panel_height - 2,
            )
        except curses.error:
            pass
        self.draw_text(
            screen,
            top,
            left + 2,
            f" DM · {self.room_name.removeprefix('DM · ')} ",
            "title",
            panel_width - 4,
        )
        typing = ", ".join(safe_text(name) for name in self.typing_users)
        if typing:
            self.draw_text(
                screen,
                top + 1,
                left + 2,
                f"Digitando: {typing}",
                "success",
                panel_width - 4,
            )

        message_rows = self.all_display_rows(panel_width - 4)
        pane_top = 2
        pane_height = panel_height - 6
        max_offset = max(0, len(message_rows) - pane_height)
        self.scroll_offset = min(self.scroll_offset, max_offset)
        end = len(message_rows) - self.scroll_offset
        start = max(0, end - pane_height)
        for index, (text, color) in enumerate(message_rows[start:end]):
            self.draw_text(
                screen,
                top + pane_top + index,
                left + 2,
                text,
                color,
                panel_width - 4,
            )
        if self.status and time.monotonic() < self.status_until:
            footer = self.status
        else:
            footer = "Esc ou /voltar: sala anterior · /sair: sair"
        self.draw_text(
            screen,
            top + panel_height - 3,
            left + 2,
            footer,
            "dim",
            panel_width - 4,
        )
        self._draw_input(
            screen,
            top + panel_height - 2,
            left + 2,
            panel_width - 4,
        )

    def draw(self, screen: Any) -> None:
        self.height, self.width = screen.getmaxyx()
        screen.erase()
        if self.dm_parent_state is None:
            self._draw_room(screen)
        else:
            dm_state = self.capture_room_state()
            self.restore_room_state(self.dm_parent_state)
            self._draw_room(screen)
            self.restore_room_state(dm_state)
            self._draw_dm_window(screen)
        screen.refresh()

    def initialize_colors(self) -> None:
        if os.environ.get("NO_COLOR") or not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        palette = [
            ("title", curses.COLOR_CYAN, curses.A_BOLD),
            ("success", curses.COLOR_GREEN, curses.A_NORMAL),
            ("warning", curses.COLOR_YELLOW, curses.A_NORMAL),
            ("dim", curses.COLOR_CYAN, curses.A_DIM),
            ("content", curses.COLOR_MAGENTA, curses.A_NORMAL),
            ("input", curses.COLOR_WHITE, curses.A_BOLD),
            ("user0", curses.COLOR_BLUE, curses.A_NORMAL),
            ("user1", curses.COLOR_CYAN, curses.A_NORMAL),
            ("user2", curses.COLOR_GREEN, curses.A_NORMAL),
            ("user3", curses.COLOR_MAGENTA, curses.A_NORMAL),
            ("user4", curses.COLOR_YELLOW, curses.A_NORMAL),
            ("user5", curses.COLOR_RED, curses.A_NORMAL),
        ]
        for pair, (name, foreground, attributes) in enumerate(palette, start=1):
            try:
                curses.init_pair(pair, foreground, -1)
                self.colors[name] = curses.color_pair(pair) | attributes
            except curses.error:
                self.colors[name] = attributes

    def popup(self, screen: Any, title: str, lines: list[str]) -> None:
        height, width = screen.getmaxyx()
        box_height = min(max(6, len(lines) + 4), max(6, height - 2))
        box_width = min(max(40, min(width - 4, 76)), max(10, width - 2))
        top = max(0, (height - box_height) // 2)
        left = max(0, (width - box_width) // 2)
        popup = curses.newwin(box_height, box_width, top, left)
        popup.keypad(True)
        content = [
            wrapped
            for line in lines
            for wrapped in wrap_text(line, max(1, box_width - 4))
        ]
        visible_height = max(1, box_height - 3)
        offset = 0
        popup.timeout(-1)
        while True:
            popup.erase()
            popup.box()
            try:
                popup.addstr(0, 2, safe_text(title)[: box_width - 4], curses.A_BOLD)
            except curses.error:
                pass
            for index, line in enumerate(
                content[offset : offset + visible_height], start=1
            ):
                try:
                    popup.addstr(index, 2, line[: box_width - 4])
                except curses.error:
                    pass
            footer = (
                "↑/↓ navegar · Enter voltar"
                if len(content) > visible_height
                else "Pressione uma tecla para voltar"
            )
            try:
                popup.addstr(box_height - 2, 2, footer[: box_width - 4])
            except curses.error:
                pass
            popup.refresh()
            key = popup.getch()
            if key in (curses.KEY_UP, curses.KEY_PPAGE):
                offset = max(0, offset - (visible_height if key == curses.KEY_PPAGE else 1))
            elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                step = visible_height if key == curses.KEY_NPAGE else 1
                offset = min(max(0, len(content) - visible_height), offset + step)
            elif key in (10, 13, 27, ord("q")):
                break
            elif len(content) <= visible_height:
                break
        screen.timeout(100)

    def confirm(self, screen: Any, message: str) -> bool:
        height, width = screen.getmaxyx()
        box_height = 6
        box_width = min(max(40, min(width - 4, 76)), max(10, width - 2))
        top = max(0, (height - box_height) // 2)
        left = max(0, (width - box_width) // 2)
        popup = curses.newwin(box_height, box_width, top, left)
        popup.keypad(True)
        popup.box()
        try:
            popup.addstr(1, 2, safe_text(message)[: box_width - 4])
            popup.addstr(3, 2, "Confirmar? [s/N]"[: box_width - 4])
        except curses.error:
            pass
        popup.refresh()
        popup.timeout(-1)
        answer = popup.get_wch()
        screen.timeout(100)
        return isinstance(answer, str) and answer.casefold() == "s"

    def show_rows(self, screen: Any, title: str, messages: list[dict[str, Any]]) -> None:
        previous = self.messages
        previous_more = self.history_has_more
        self.messages = messages
        self.history_has_more = False
        rows = [text for text, _ in self.all_display_rows()]
        self.popup(screen, title, rows or ["Nenhum resultado."])
        self.messages = previous
        self.history_has_more = previous_more

    def handle_command(self, screen: Any, text: str) -> bool:
        parts = text.strip().split(maxsplit=2)
        command = parts[0].casefold()
        argument = parts[1] if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""
        if command in {"/sair", "/quit", "/exit"}:
            return False
        if command == "/voltar":
            self.return_to_parent()
            return True
        if command == "/ajuda":
            lines = [
                "Esc / /voltar: voltar da DM · /sair: sair",
                "F2 abre a DM não lida mais recente",
                "/quem · /salas · /sala global|help|exercise:CODIGO",
                "/dm usuario · /historico · /buscar termo · /limpar",
                "/reagir ID 👍|❤️|😂|+1 · /denunciar ID motivo",
            ]
            if self.is_admin:
                lines.append(
                    "/fixar ID · /desafixar ID · /ocultar ID · /restaurar ID"
                )
                lines.append("/relatorios · /apagar-historico")
            self.popup(screen, "AJUDA DO CHAT", lines)
            return True
        if command == "/quem":
            response = self.request("chat.dashboard")
            names = response.get("online_users", [])
            self.popup(screen, "PESSOAS ONLINE", names or ["Ninguém online."])
            return True
        if command == "/salas":
            response = self.request("chat.rooms")
            rooms = response.get("rooms", [])
            self.popup(
                screen,
                "SALAS DO CHAT",
                [f"{room['channel']} · {room['name']}" for room in rooms],
            )
            return True
        if command == "/sala":
            room = argument.casefold()
            if room in {"global", "help", "ajuda"}:
                channel = "help" if room in {"help", "ajuda"} else "global"
                name = "Ajuda" if channel == "help" else "Chat global"
            elif room.startswith("exercise:"):
                channel, name = argument, argument
            else:
                self.set_status("Uso: /sala global|ajuda|exercise:CODIGO")
                return True
            try:
                if self.dm_parent_state is not None:
                    self.return_to_parent()
                self.reset_channel(channel, name)
            except ChatError as error:
                self.set_status(str(error))
            return True
        if command == "/dm":
            if not argument:
                self.set_status("Uso: /dm usuario")
                return True
            response = self.request("chat.dm.open", username=argument)
            channel = str(response["channel"])
            self.open_dm(
                channel, f"DM · {response.get('username', argument)}"
            )
            return True
        if command == "/historico":
            self.load_history()
            return True
        if command == "/buscar":
            query = " ".join(part for part in (argument, rest) if part)
            if not query:
                self.set_status("Uso: /buscar termo")
                return True
            response = self.request("chat.search", channel=self.channel, query=query)
            self.show_rows(screen, "BUSCA", response.get("messages", []))
            return True
        if command == "/limpar":
            self.messages = []
            self.last_id = 0
            self.history_before = None
            self.history_has_more = False
            self.history_loaded = False
            self.sync()
            return True
        if command in {"/reagir", "/denunciar"}:
            if not argument or not rest:
                item = "reação" if command == "/reagir" else "motivo"
                self.set_status(f"Uso: {command} ID {item}")
                return True
            if command == "/reagir":
                self.request(
                    "chat.react", message_id=argument, reaction=rest
                )
                self.set_status("Reação atualizada.")
            else:
                self.request(
                    "chat.report", message_id=argument, reason=rest
                )
                self.set_status("Denúncia enviada à moderação.")
            self.reload_latest()
            return True
        moderation = {
            "/fixar": "pin",
            "/desafixar": "unpin",
            "/ocultar": "hide",
            "/restaurar": "restore",
        }
        if command in moderation:
            if not self.is_admin:
                self.set_status("Acesso administrativo não autorizado.")
                return True
            if not argument.isdigit():
                self.set_status(f"Uso: {command} ID")
                return True
            self.request(
                "chat.admin.moderate",
                message_id=argument,
                action=moderation[command],
            )
            self.set_status("Moderação aplicada.")
            self.reload_latest()
            return True
        if command == "/relatorios":
            if not self.is_admin:
                self.set_status("Acesso administrativo não autorizado.")
                return True
            response = self.request("chat.admin.reports")
            reports = response.get("reports", [])
            lines = [
                f"#{item['id']} · mensagem #{item['message_id']} · {item['author']}: "
                f"{item['body']} · {item['status']} · por {item['reporter']}"
                for item in reports
            ]
            self.popup(screen, "DENÚNCIAS ABERTAS", lines or ["Nenhuma denúncia aberta."])
            return True
        if command == "/apagar-historico":
            if not self.is_admin:
                self.set_status("Acesso administrativo não autorizado.")
                return True
            if self.confirm(screen, f"Apagar todo o histórico de '{self.room_name}'?"):
                response = self.request("chat.admin.clear", channel=self.channel)
                deleted = response.get("deleted_count", 0)
                self.messages = []
                self.last_id = 0
                self.history_before = None
                self.history_has_more = False
                self.history_loaded = False
                self.sync()
                self.set_status(f"{deleted} mensagem(ns) apagada(s).")
            return True
        self.set_status("Comando desconhecido. Digite /ajuda.")
        return True

    def reload_latest(self) -> None:
        self.last_id = 0
        self.messages = []
        self.history_before = None
        self.history_has_more = False
        self.history_loaded = False
        self.sync()

    def send_message(self, screen: Any, message: str) -> bool:
        if message.startswith("/"):
            if self.typing_active:
                try:
                    self.mark_typing(False)
                except ChatError as error:
                    self.set_status(str(error))
            try:
                return self.handle_command(screen, message)
            except ChatError as error:
                self.set_status(str(error))
                return True
        try:
            self.request("chat.post", channel=self.channel, message=message)
        except ChatError as error:
            self.input_text = message
            self.cursor = len(message)
            self.set_status(str(error))
            return True
        if self.typing_active:
            try:
                self.mark_typing(False)
            except ChatError:
                self.set_status("Mensagem enviada; não foi possível atualizar o indicador.")
        else:
            self.set_status("")
        try:
            self.sync()
        except ChatError as error:
            self.set_status(f"Mensagem enviada; falha ao atualizar: {error}")
        return True

    def handle_key(self, screen: Any, key: Any) -> bool:
        if key == curses.KEY_RESIZE:
            return True
        if key in ("\x1b", 27):
            if self.dm_parent_state is not None:
                self.return_to_parent()
                return True
            return False
        if key == curses.KEY_F2:
            direct = next(
                (
                    room
                    for room in self.unread_rooms
                    if room.get("kind") == "direct"
                ),
                None,
            )
            if direct is None:
                self.set_status("Não há DMs não lidas.")
            else:
                self.open_dm(
                    str(direct["channel"]), str(direct.get("name", "DM"))
                )
            return True
        if key in (curses.KEY_PPAGE,):
            self.scroll_offset = min(
                len(self.all_display_rows()), self.scroll_offset + max(3, self.height // 2)
            )
            return True
        if key in (curses.KEY_NPAGE,):
            self.scroll_offset = max(0, self.scroll_offset - max(3, self.height // 2))
            return True
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_HOME, curses.KEY_END):
            if key == curses.KEY_LEFT:
                self.cursor = max(0, self.cursor - 1)
            elif key == curses.KEY_RIGHT:
                self.cursor = min(len(self.input_text), self.cursor + 1)
            else:
                self.cursor = 0 if key == curses.KEY_HOME else len(self.input_text)
            return True
        if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            if self.cursor:
                self.input_text = (
                    self.input_text[: self.cursor - 1] + self.input_text[self.cursor :]
                )
                self.cursor -= 1
                if not self.input_text and self.typing_active:
                    self.mark_typing(False)
            return True
        if key == curses.KEY_DC:
            if self.cursor < len(self.input_text):
                self.input_text = (
                    self.input_text[: self.cursor]
                    + self.input_text[self.cursor + 1 :]
                )
            return True
        if key in ("\n", "\r", curses.KEY_ENTER):
            if not self.input_text:
                try:
                    self.sync()
                except ChatError as error:
                    self.set_status(str(error))
                return True
            message = self.input_text
            self.input_history.append(message)
            self.history_cursor = None
            self.input_text = ""
            self.cursor = 0
            keep_open = self.send_message(screen, message)
            self.scroll_offset = 0
            return keep_open
        if key == "\x04":
            if not self.input_text:
                return False
            return True
        if key in ("\x03",):
            self.input_text = ""
            self.cursor = 0
            if self.typing_active:
                self.mark_typing(False)
            return True
        if key in (curses.KEY_UP, curses.KEY_DOWN):
            if self.input_history:
                if key == curses.KEY_UP:
                    if self.history_cursor is None:
                        self.history_cursor = len(self.input_history)
                    self.history_cursor = max(0, self.history_cursor - 1)
                elif self.history_cursor is not None:
                    self.history_cursor += 1
                    if self.history_cursor >= len(self.input_history):
                        self.history_cursor = None
                self.input_text = (
                    self.input_history[self.history_cursor]
                    if self.history_cursor is not None
                    else ""
                )
                self.cursor = len(self.input_text)
            return True
        if isinstance(key, str) and key.isprintable():
            if len(self.input_text) >= 500:
                self.set_status("O limite da mensagem é 500 caracteres.")
                return True
            self.input_text = (
                self.input_text[: self.cursor] + key + self.input_text[self.cursor :]
            )
            self.cursor += len(key)
            if self.input_text and not self.typing_active:
                try:
                    self.mark_typing(True)
                except ChatError:
                    pass
        return True

    def run(self, screen: Any) -> int:
        curses.cbreak()
        screen.keypad(True)
        screen.timeout(100)
        try:
            curses.set_escdelay(25)
        except (AttributeError, curses.error):
            pass
        self.initialize_colors()
        try:
            self.sync()
        except ChatError as error:
            self.set_status(str(error), 30)
        last_poll = time.monotonic()
        running = True
        try:
            while running:
                now = time.monotonic()
                if self.auto_refresh and now - last_poll >= self.refresh_seconds:
                    try:
                        self.sync()
                    except ChatError as error:
                        self.set_status(str(error))
                    last_poll = now
                if self.typing_active and now - self.last_typing_update >= 4:
                    try:
                        self.mark_typing(True)
                    except ChatError:
                        pass
                self.draw(screen)
                try:
                    key = screen.get_wch()
                except curses.error:
                    continue
                try:
                    running = self.handle_key(screen, key)
                except ChatError as error:
                    self.set_status(str(error))
        finally:
            if self.typing_active:
                try:
                    self.mark_typing(False)
                except ChatError:
                    pass
        return 0


def main() -> int:
    try:
        token = os.environ["CHAT_TUI_TOKEN"]
        user_id = int(os.environ["CHAT_TUI_USER_ID"])
        username = os.environ["CHAT_TUI_USERNAME"]
        is_admin = os.environ.get("CHAT_TUI_IS_ADMIN") == "True"
        socket_path = os.environ["CHAT_TUI_SOCKET"]
        auto_refresh_value = os.environ.get("CHAT_AUTO_REFRESH", "1")
        if auto_refresh_value not in {"0", "1"}:
            raise ValueError("CHAT_AUTO_REFRESH deve ser 0 (manual) ou 1 (automático).")
        refresh_seconds = int(os.environ.get("CHAT_REFRESH_SECONDS", "2"))
        if not 1 <= refresh_seconds <= 60:
            raise ValueError("CHAT_REFRESH_SECONDS deve estar entre 1 e 60.")
        history_limit = int(os.environ.get("CHAT_HISTORY_WINDOW", "10"))
        if not 1 <= history_limit <= 100:
            raise ValueError("CHAT_HISTORY_WINDOW deve estar entre 1 e 100.")
        tui = ChatTui(
            token=token,
            user_id=user_id,
            username=username,
            is_admin=is_admin,
            socket_path=socket_path,
            channel=os.environ.get("CHAT_TUI_CHANNEL", "global"),
            room_name=os.environ.get("CHAT_TUI_ROOM_NAME", "Chat global"),
            auto_refresh=auto_refresh_value == "1",
            refresh_seconds=refresh_seconds,
            history_limit=history_limit,
        )
        terminal_fd = sys.stdin.fileno()
        terminal_state = termios.tcgetattr(terminal_fd)
        try:
            try:
                return curses.wrapper(tui.run)
            except KeyboardInterrupt:
                return 0
        finally:
            termios.tcsetattr(terminal_fd, termios.TCSADRAIN, terminal_state)
    except (KeyError, ValueError, OSError, curses.error) as error:
        print(f"Não foi possível iniciar o chat: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
