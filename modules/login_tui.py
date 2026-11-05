#!/usr/bin/env python3
"""Responsive curses login and registration screen."""

from __future__ import annotations

import curses
import json
import os
import sys
import unicodedata
from typing import Any


class LoginTui:
    def __init__(self, mode: str, error: str, username: str) -> None:
        self.mode = mode
        self.error = self.safe_text(error)
        self.username = username[:32]
        self.password = ""
        self.confirmation = ""
        self.action = "login"
        self.selection = 1
        self.focus = 0
        self.cursor = len(self.username)
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
            ("label", curses.COLOR_WHITE, curses.A_NORMAL),
            ("hint", curses.COLOR_CYAN, curses.A_DIM),
            ("error", curses.COLOR_YELLOW, curses.A_BOLD),
            ("placeholder", curses.COLOR_BLUE, curses.A_DIM),
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
        color: str = "label",
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

    def draw_frame(
        self, screen: Any, title: str, body: list[str]
    ) -> tuple[int, int, int, int]:
        self.height, self.width = screen.getmaxyx()
        screen.erase()
        if self.height < 8 or self.width < 32:
            self.draw_text(screen, 0, 0, "Aumente o terminal para continuar.", "error")
            self.draw_text(screen, self.height - 1, 0, "Esc sai", "hint")
            screen.refresh()
            return 0, 0, 0, 0

        panel_width = min(72, self.width - 4)
        panel_height = min(max(14, len(body) + 8), self.height - 2)
        top = max(0, (self.height - panel_height) // 2)
        left = max(0, (self.width - panel_width) // 2)
        screen.attron(curses.A_BOLD)
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
        screen.attroff(curses.A_BOLD)

        heading = title[: panel_width - 4]
        self.draw_text(
            screen,
            top + 1,
            left + max(2, (panel_width - len(heading)) // 2),
            heading,
            "title",
            panel_width - 4,
        )
        content_top = top + 3
        content_width = panel_width - 4
        for index, line in enumerate(body):
            if content_top + index >= top + panel_height - 2:
                break
            self.draw_text(
                screen,
                content_top + index,
                left + 2,
                line,
                width=content_width,
            )
        return top, left, panel_width, panel_height

    def draw_menu(self, screen: Any, refresh: bool = True) -> None:
        self.height, self.width = screen.getmaxyx()
        screen.erase()
        if self.height < 8 or self.width < 32:
            self.draw_text(screen, 0, 0, "Aumente o terminal para continuar.", "error")
            self.draw_text(screen, self.height - 1, 0, "Esc sai", "hint")
            if refresh:
                screen.refresh()
            return

        y = max(0, (self.height - 25) // 2) + 1
        options = ["1 | Criar Conta", "2 | Entrar", "0 | Sair"]
        banner_width = 70 if self.width >= 72 else 30
        banner_left = max(0, (self.width - banner_width) // 2)

        if self.width >= 72:
            art = [
                "",
                "███████╗██╗███╗   ███╗██████╗ ██╗     ███████╗",
                "██╔════╝██║████╗ ████║██╔══██╗██║     ██╔════╝",
                "███████╗██║██╔████╔██║██████╔╝██║     █████╗",
                "╚════██║██║██║╚██╔╝██║██╔═══╝ ██║     ██╔══╝",
                "███████║██║██║ ╚═╝ ██║██║     ███████╗███████╗",
                "╚══════╝╚═╝╚═╝     ╚═╝╚═╝     ╚══════╝╚══════╝",
                "",
                "AMBIENTE DE APRENDIZAGEM",
                "",
            ]
            self.draw_text(
                screen,
                y,
                banner_left,
                f"┌{'─' * 68}┐",
                "title",
                banner_width,
            )
            for index, line in enumerate(art, start=1):
                if line == "AMBIENTE DE APRENDIZAGEM":
                    padding = (68 - len(line)) // 2
                else:
                    padding = (68 - 46) // 2
                self.draw_text(
                    screen,
                    y + index,
                    banner_left,
                    f"│{' ' * padding}{line}{' ' * (68 - len(line) - padding)}│",
                    "title",
                    banner_width,
                )
            self.draw_text(
                screen,
                y + len(art) + 1,
                banner_left,
                f"└{'─' * 68}┘",
                "title",
                banner_width,
            )
            y += len(art) + 2
        else:
            self.draw_text(screen, y, banner_left, "+----------------------------+", "title", 30)
            for index, line in enumerate(("SIMPLE", "AMBIENTE DE APRENDIZAGEM"), start=1):
                padding = (28 - len(line)) // 2
                self.draw_text(
                    screen,
                    y + index,
                    banner_left,
                    f"|{' ' * padding}{line}{' ' * (28 - len(line) - padding)}|",
                    "title",
                    30,
                )
            self.draw_text(screen, y + 3, banner_left, "+----------------------------+", "title", 30)
            y += 5

        y += 1
        button_width = min(26, self.width)
        button_left = max(0, (self.width - button_width) // 2)
        for index, option in enumerate(options):
            border_color = "selected" if index == self.selection else "label"
            border_attr = self.colors.get(border_color, curses.A_NORMAL)
            border_top = f"┌{'─' * (button_width - 2)}┐"
            border_bottom = f"└{'─' * (button_width - 2)}┘"
            content_attr = curses.A_BOLD if index == self.selection else curses.A_NORMAL
            self.draw_text(
                screen,
                y,
                button_left,
                border_top,
                border_color,
                width=button_width,
            )
            content = f" {option:<{button_width - 4}} "
            try:
                screen.addnstr(y + 1, button_left, "│", 1, border_attr)
                screen.addnstr(
                    y + 1,
                    button_left + 1,
                    content,
                    button_width - 2,
                    content_attr,
                )
                screen.addnstr(
                    y + 1,
                    button_left + button_width - 1,
                    "│",
                    1,
                    border_attr,
                )
            except curses.error:
                pass
            self.draw_text(
                screen,
                y + 2,
                button_left,
                border_bottom,
                border_color,
                width=button_width,
            )
            y += 3

        if self.error:
            self.draw_text(
                screen,
                min(self.height - 1, y + 1),
                max(0, (self.width - len(self.error)) // 2),
                self.error,
                "error",
                self.width,
            )
        if refresh:
            screen.refresh()

    def form_fields(self) -> list[tuple[str, str]]:
        fields = [("Login", self.username), ("Senha", self.password)]
        if self.action == "register":
            fields.append(("Confirmar senha", self.confirmation))
        return fields

    def draw_form(self, screen: Any) -> None:
        self.draw_menu(screen, refresh=False)
        fields = self.form_fields()
        if self.height < 8 or self.width < 32:
            screen.refresh()
            return

        modal_width = min(48, self.width - 4)
        modal_height = 12 + (len(fields) - 2) * 4
        top = max(0, (self.height - modal_height) // 2)
        left = max(0, (self.width - modal_width) // 2)
        modal_attr = self.colors.get("label", curses.A_NORMAL)
        for row in range(modal_height):
            try:
                screen.addnstr(
                    top + row,
                    left,
                    " " * modal_width,
                    modal_width,
                    modal_attr,
                )
            except curses.error:
                pass

        try:
            screen.addch(top, left, curses.ACS_ULCORNER, modal_attr)
            screen.addch(top, left + modal_width - 1, curses.ACS_URCORNER, modal_attr)
            screen.addch(top + modal_height - 1, left, curses.ACS_LLCORNER, modal_attr)
            screen.addch(
                top + modal_height - 1,
                left + modal_width - 1,
                curses.ACS_LRCORNER,
                modal_attr,
            )
            screen.hline(top, left + 1, curses.ACS_HLINE, modal_width - 2, modal_attr)
            screen.hline(
                top + modal_height - 1,
                left + 1,
                curses.ACS_HLINE,
                modal_width - 2,
                modal_attr,
            )
            screen.vline(
                top + 1, left, curses.ACS_VLINE, modal_height - 2, modal_attr
            )
            screen.vline(
                top + 1,
                left + modal_width - 1,
                curses.ACS_VLINE,
                modal_height - 2,
                modal_attr,
            )
        except curses.error:
            pass

        title = "CRIAR CONTA" if self.action == "register" else "ENTRAR"
        self.draw_text(
            screen,
            top + 1,
            left + max(2, (modal_width - len(title)) // 2),
            title,
            "title",
            modal_width - 4,
        )
        field_width = modal_width - 8
        field_left = left + 4
        field_inner_width = field_width - 2
        for index, (placeholder, value) in enumerate(fields):
            field_top = top + 3 + index * 4
            focused = index == self.focus
            field_color = "selected" if focused else "label"
            field_attr = self.colors.get(field_color, curses.A_NORMAL)
            self.draw_text(
                screen,
                field_top,
                field_left,
                f"┌{'─' * (field_width - 2)}┐",
                field_color,
                field_width,
            )
            try:
                screen.addch(field_top + 1, field_left, "│", field_attr)
                screen.addch(
                    field_top + 1,
                    field_left + field_width - 1,
                    "│",
                    field_attr,
                )
            except curses.error:
                pass
            if value:
                shown = value if index == 0 else "*" * min(len(value), field_inner_width)
                text_color = "label"
            else:
                shown = placeholder
                text_color = "placeholder"
            shown = shown[-field_inner_width:]
            self.draw_text(
                screen,
                field_top + 1,
                field_left + 1,
                f" {shown:<{field_inner_width - 2}} ",
                text_color,
                field_inner_width,
            )
            self.draw_text(
                screen,
                field_top + 2,
                field_left,
                f"└{'─' * (field_width - 2)}┘",
                field_color,
                field_width,
            )
        if self.error:
            self.draw_text(
                screen,
                top + modal_height - 2,
                left + 2,
                self.error,
                "error",
                modal_width - 4,
            )

        field_top = top + 3 + self.focus * 4
        cursor_x = field_left + 2 + self.cursor
        if self.current_value():
            max_cursor_x = field_left + field_width - 2
            cursor_x = min(cursor_x, max_cursor_x)
        try:
            screen.move(field_top + 1, cursor_x)
        except curses.error:
            pass
        screen.refresh()

    def current_value(self) -> str:
        if self.focus == 0:
            return self.username
        if self.focus == 1:
            return self.password
        return self.confirmation

    def set_current_value(self, value: str) -> None:
        if self.focus == 0:
            self.username = value[:32]
            self.cursor = min(self.cursor, len(self.username))
        elif self.focus == 1:
            self.password = value[:256]
            self.cursor = min(self.cursor, len(self.password))
        else:
            self.confirmation = value[:256]
            self.cursor = min(self.cursor, len(self.confirmation))

    def run(self, screen: Any) -> dict[str, str]:
        curses.cbreak()
        curses.noecho()
        screen.keypad(True)
        screen.timeout(-1)
        self.initialize_colors()
        menu = True
        while True:
            if menu:
                self.draw_menu(screen)
            else:
                self.draw_form(screen)
            key = screen.get_wch()

            if menu:
                if key in (curses.KEY_UP, curses.KEY_LEFT):
                    self.selection = (self.selection - 1) % 3
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                    self.selection = (self.selection + 1) % 3
                elif key in ("\x1b", 27):
                    return {"action": "quit"}
                elif key in ("\n", "\r", curses.KEY_ENTER):
                    if self.selection == 0:
                        if self.mode == "proxy":
                            self.error = (
                                "Solicite ao administrador a criação e vinculação "
                                "da sua conta SIMPLE."
                            )
                        else:
                            self.action = "register"
                            self.focus = 0
                            menu = False
                    elif self.selection == 1:
                        self.action = "login"
                        self.focus = 0
                        menu = False
                    else:
                        return {"action": "quit"}
                elif isinstance(key, str) and key in {"1", "2", "0"}:
                    self.selection = {"1": 0, "2": 1, "0": 2}[key]
            else:
                if key in ("\x1b", 27):
                    menu = True
                    self.error = ""
                    continue
                if key in (curses.KEY_UP, curses.KEY_BTAB):
                    self.focus = max(0, self.focus - 1)
                    self.cursor = len(self.current_value())
                    continue
                if key in (curses.KEY_DOWN, "\t"):
                    self.focus = min(len(self.form_fields()) - 1, self.focus + 1)
                    self.cursor = len(self.current_value())
                    continue
                if key in ("\n", "\r", curses.KEY_ENTER):
                    if self.focus < len(self.form_fields()) - 1:
                        self.focus += 1
                        self.cursor = len(self.current_value())
                    else:
                        return {
                            "action": self.action,
                            "username": self.username,
                            "password": self.password,
                            "confirmation": self.confirmation,
                        }
                    continue
                if key in (curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_HOME, curses.KEY_END):
                    if key == curses.KEY_LEFT:
                        self.cursor = max(0, self.cursor - 1)
                    elif key == curses.KEY_RIGHT:
                        self.cursor = min(len(self.current_value()), self.cursor + 1)
                    else:
                        self.cursor = 0 if key == curses.KEY_HOME else len(self.current_value())
                    continue
                value = self.current_value()
                if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    if self.cursor:
                        self.set_current_value(
                            value[: self.cursor - 1] + value[self.cursor :]
                        )
                        self.cursor -= 1
                    continue
                if isinstance(key, str) and key.isprintable():
                    self.error = ""
                    self.set_current_value(
                        value[: self.cursor] + key + value[self.cursor :]
                    )
                    self.cursor += len(key)


def main() -> int:
    try:
        tui = LoginTui(
            mode=os.environ.get("SIMPLE_MODE", "local"),
            error=os.environ.get("LOGIN_TUI_ERROR", ""),
            username=os.environ.get("LOGIN_TUI_USERNAME", ""),
        )
        result = curses.wrapper(tui.run)
        result_fd = int(os.environ.get("LOGIN_TUI_RESULT_FD", "1"))
        with os.fdopen(result_fd, "w", encoding="utf-8", closefd=False) as stream:
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        return 0
    except (OSError, curses.error) as error:
        print(f"Não foi possível abrir a tela de acesso: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
