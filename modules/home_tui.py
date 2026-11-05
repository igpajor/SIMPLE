#!/usr/bin/env python3
"""Responsive curses home screen for SIMPLE."""

from __future__ import annotations

import curses
import json
import os
import re
import sys
import unicodedata
from typing import Any


class HomeTui:
    def __init__(self, config: dict[str, Any]) -> None:
        self.username = self.safe_text(config.get("username", ""))
        self.stats = [
            (self.safe_text(item["label"]), self.safe_text(item["value"]))
            for item in config.get("stats", [])
        ]
        self.notification = self.safe_text(config.get("notification", ""))
        self.options = [
            (str(item["key"]), self.safe_text(item["label"]))
            for item in config.get("options", [])
        ]
        self.selection = 0
        self.width = 80
        self.height = 24
        self.colors: dict[str, int] = {}

    @staticmethod
    def safe_text(value: Any) -> str:
        return "".join(
            char
            for char in str(value)
            if unicodedata.category(char) not in {"Cc", "Cs"}
        )

    def initialize_colors(self) -> None:
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        palette = [
            ("title", curses.COLOR_CYAN, curses.A_BOLD),
            ("selected", curses.COLOR_CYAN, curses.A_BOLD),
            ("text", curses.COLOR_WHITE, curses.A_NORMAL),
            ("muted", curses.COLOR_WHITE, curses.A_DIM),
            ("notification", curses.COLOR_YELLOW, curses.A_BOLD),
        ]
        for index, (name, foreground, attributes) in enumerate(palette, start=1):
            try:
                curses.init_pair(index, foreground, -1)
                self.colors[name] = curses.color_pair(index) | attributes
            except curses.error:
                self.colors[name] = attributes

    def draw_text(
        self,
        screen: Any,
        y: int,
        x: int,
        text: str,
        color: str = "text",
        width: int | None = None,
    ) -> None:
        if y < 0 or y >= self.height or x < 0 or x >= self.width:
            return
        text = self.safe_text(text)
        limit = self.width - x - 1 if width is None else min(width, self.width - x - 1)
        try:
            screen.addnstr(y, x, text, max(0, limit), self.colors.get(color, curses.A_NORMAL))
        except curses.error:
            pass

    def stats_columns(self, inner_width: int) -> int:
        if inner_width >= 64 and len(self.stats) > 2:
            return len(self.stats)
        if inner_width >= 42 and len(self.stats) > 1:
            return 2
        return 1

    def draw_frame(
        self, screen: Any, top: int, left: int, width: int, height: int
    ) -> None:
        right = left + width - 1
        bottom = top + height - 1
        attr = self.colors.get("muted", curses.A_NORMAL)
        try:
            screen.addch(top, left, curses.ACS_ULCORNER, attr)
            screen.addch(top, right, curses.ACS_URCORNER, attr)
            screen.addch(bottom, left, curses.ACS_LLCORNER, attr)
            screen.addch(bottom, right, curses.ACS_LRCORNER, attr)
            screen.hline(top, left + 1, curses.ACS_HLINE, width - 2, attr)
            screen.hline(bottom, left + 1, curses.ACS_HLINE, width - 2, attr)
            screen.vline(top + 1, left, curses.ACS_VLINE, height - 2, attr)
            screen.vline(top + 1, right, curses.ACS_VLINE, height - 2, attr)
        except curses.error:
            pass

    def visible_options(self, max_visible: int) -> tuple[int, int]:
        count = len(self.options)
        visible = min(count, max_visible)
        start = max(0, self.selection - visible // 2)
        start = min(start, count - visible)
        return start, start + visible

    def draw(self, screen: Any) -> None:
        self.height, self.width = screen.getmaxyx()
        screen.erase()
        if self.width < 32:
            self.draw_text(screen, 0, 0, "Aumente o terminal para continuar.", "notification")
            self.draw_text(screen, self.height - 1, 0, "Esc sair", "muted")
            screen.refresh()
            return

        panel_width = min(110, self.width - 2)
        if panel_width < 38:
            panel_width = self.width - 2
        left = max(0, (self.width - panel_width) // 2)
        inner_width = panel_width - 6
        columns = self.stats_columns(inner_width)
        stats_rows = (len(self.stats) + columns - 1) // columns
        summary_top = 5
        summary_height = stats_rows + 2
        menu_title_offset = summary_top + summary_height + 1
        option_start_offset = menu_title_offset + 1
        max_panel_height = self.height - 2
        max_visible = max_panel_height - option_start_offset - 6
        if max_visible < 1 or self.height < 15 + stats_rows:
            self.draw_text(
                screen,
                0,
                0,
                "Aumente a altura do terminal para continuar.",
                "notification",
            )
            self.draw_text(screen, self.height - 1, 0, "Esc sair", "muted")
            screen.refresh()
            return
        start, end = self.visible_options(max_visible)
        visible_count = end - start
        option_rows = visible_count + 2
        panel_height = option_start_offset + option_rows + 4
        panel_height = min(panel_height, max_panel_height)
        top = max(0, (self.height - panel_height) // 2)
        self.draw_frame(screen, top, left, panel_width, panel_height)

        self.draw_text(
            screen,
            top + 1,
            left + 3,
            "SIMPLE · INÍCIO",
            "title",
            inner_width,
        )
        self.draw_text(
            screen,
            top + 2,
            left + 3,
            f"{'─' * inner_width}",
            "muted",
            inner_width,
        )
        self.draw_text(
            screen,
            top + 3,
            left + 3,
            f"Olá, {self.username}. Que bom ter você por aqui.",
            "text",
            inner_width,
        )
        if self.notification:
            self.draw_text(
                screen,
                top + 4,
                left + 3,
                self.notification,
                "notification",
                inner_width,
            )

        summary_left = left + 3
        summary_width = inner_width
        summary_top_y = top + summary_top
        self.draw_frame(
            screen,
            summary_top_y,
            summary_left,
            summary_width,
            summary_height,
        )
        self.draw_text(
            screen,
            summary_top_y,
            summary_left + 2,
            " SEU RESUMO ",
            "title",
            14,
        )
        stat_width = inner_width // columns
        for index, (label, value) in enumerate(self.stats):
            row = index // columns
            column = index % columns
            stat_text = f"{label} {value}"
            self.draw_text(
                screen,
                summary_top_y + 1 + row,
                summary_left + 2 + column * stat_width,
                stat_text,
                "text",
                stat_width - 1,
            )

        menu_title_y = top + menu_title_offset
        self.draw_text(screen, menu_title_y, left + 3, "MENU PRINCIPAL", "title", inner_width)

        option_y = top + option_start_offset
        option_width = min(inner_width, 58)
        option_left = left + (panel_width - option_width) // 2
        for index in range(start, end):
            key, label = self.options[index]
            option_text = f"[{key}] {label}"
            if index == self.selection:
                self.draw_text(
                    screen,
                    option_y,
                    option_left,
                    f"┌{'─' * (option_width - 2)}┐",
                    "selected",
                    option_width,
                )
                self.draw_text(
                    screen,
                    option_y + 1,
                    option_left + 2,
                    option_text,
                    "selected",
                    option_width - 4,
                )
                self.draw_text(
                    screen,
                    option_y + 2,
                    option_left,
                    f"└{'─' * (option_width - 2)}┘",
                    "selected",
                    option_width,
                )
                option_y += 3
            else:
                self.draw_text(
                    screen,
                    option_y,
                    option_left + 2,
                    option_text,
                    "text",
                    option_width - 4,
                )
                option_y += 1

        footer_y = top + panel_height - 2
        self.draw_text(
            screen,
            footer_y,
            left + 3,
            "↑/↓ navegar · Enter abrir · número escolher · 0 sair",
            "muted",
            inner_width,
        )
        screen.refresh()

    def run(self, screen: Any) -> str:
        curses.cbreak()
        curses.noecho()
        screen.keypad(True)
        screen.timeout(-1)
        self.initialize_colors()
        while True:
            self.draw(screen)
            key = screen.get_wch()
            if key in (curses.KEY_UP, curses.KEY_LEFT, curses.KEY_BTAB):
                self.selection = (self.selection - 1) % len(self.options)
            elif key in (curses.KEY_DOWN, curses.KEY_RIGHT, "\t"):
                self.selection = (self.selection + 1) % len(self.options)
            elif key in ("\n", "\r", curses.KEY_ENTER):
                return self.options[self.selection][0]
            elif key in ("\x1b", 27):
                return "0"
            elif key == curses.KEY_RESIZE:
                continue
            elif isinstance(key, str) and key.isdigit():
                for option_key, _ in self.options:
                    if key == option_key:
                        return option_key


def load_config() -> dict[str, Any]:
    config = json.loads(os.environ["HOME_TUI_CONFIG"])
    if not isinstance(config, dict):
        raise ValueError("Configuração da home inválida")
    options = config.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("A home precisa ter ao menos uma opção")
    for option in options:
        if (
            not isinstance(option, dict)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", str(option.get("key", "")))
            or not isinstance(option.get("label"), str)
        ):
            raise ValueError("Opção inválida na home")
    stats = config.get("stats", [])
    if not isinstance(stats, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("label"), str)
        or not isinstance(item.get("value"), str)
        for item in stats
    ):
        raise ValueError("Resumo inválido na home")
    if not isinstance(config.get("username", ""), str):
        raise ValueError("Usuário inválido na home")
    if not isinstance(config.get("notification", ""), str):
        raise ValueError("Notificação inválida na home")
    return config


def main() -> int:
    try:
        result = curses.wrapper(HomeTui(load_config()).run)
        result_fd = int(os.environ.get("HOME_TUI_RESULT_FD", "1"))
        with os.fdopen(result_fd, "w", encoding="utf-8", closefd=False) as stream:
            stream.write(result + "\n")
        return 0
    except (OSError, ValueError, curses.error, KeyError, json.JSONDecodeError) as error:
        print(f"Não foi possível abrir a tela inicial: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
