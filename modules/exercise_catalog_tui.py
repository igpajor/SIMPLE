#!/usr/bin/env python3
"""Responsive curses catalog and exercise detail screens."""

from __future__ import annotations

import curses
import json
import os
import sys
import unicodedata
from typing import Any


FILTERS = {
    "all": "A Todos",
    "available": "D Disponíveis",
    "pending": "E Em avaliação",
    "approved": "C Aprovados",
}
STATUS_LABELS = {
    "available": "Disponível",
    "pending": "Em avaliação",
    "approved": "Aprovado",
    "no_points": "Sem pontos",
}
DETAIL_FIELDS = (
    ("DESCRIÇÃO", "description"),
    ("MISSÃO", "statement"),
    ("DIRETÓRIO DE ENTREGA", "delivery_directory"),
    ("ARQUIVOS A ENTREGAR", "delivery_files"),
    ("FUNÇÕES PERMITIDAS", "allowed_functions"),
    ("EXEMPLO DE EXECUÇÃO", "example"),
    ("MODELO DO TERMINAL", "terminal_model"),
    ("DICAS", "hints"),
)


def safe_text(value: Any) -> str:
    return "".join(
        char
        for char in str(value)
        if unicodedata.category(char) not in {"Cc", "Cs"}
    )


def char_width(char: str) -> int:
    if unicodedata.combining(char) or unicodedata.category(char) in {"Cf", "Mn"}:
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def wrap_text(text: str, width: int) -> list[str]:
    width = max(1, width)
    lines: list[str] = []
    for source in safe_text(text).splitlines() or [""]:
        if not source:
            lines.append("")
            continue
        line = ""
        used = 0
        for char in source:
            size = char_width(char)
            if line and used + size > width:
                lines.append(line)
                line = ""
                used = 0
            line += char
            used += size
        lines.append(line)
    return lines


class ExerciseCatalogTui:
    def __init__(self, config: dict[str, Any]) -> None:
        self.username = safe_text(config["username"])
        self.points = config["points"]
        self.items = config["items"]
        self.filter = config.get("filter", "all")
        if self.filter not in FILTERS:
            self.filter = "all"
        self.query = safe_text(config.get("query", ""))
        self.selected_code = safe_text(config.get("selected_code", ""))
        self.filtered_items: list[dict[str, Any]] = []
        self.selection = 0
        self.detail: dict[str, Any] | None = None
        self.detail_lines: list[tuple[str, str]] = []
        self.detail_scroll = 0
        self.action_selection = 0
        self.searching = False
        self.width = 80
        self.height = 24
        self.colors: dict[str, int] = {}
        self.refresh_items()

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
            ("available", curses.COLOR_GREEN, curses.A_NORMAL),
            ("pending", curses.COLOR_YELLOW, curses.A_NORMAL),
            ("approved", curses.COLOR_CYAN, curses.A_NORMAL),
            ("warning", curses.COLOR_YELLOW, curses.A_BOLD),
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
        limit = self.width - x - 1 if width is None else min(width, self.width - x - 1)
        result: list[str] = []
        used = 0
        for char in safe_text(text).replace("\n", " ").replace("\t", " "):
            size = char_width(char)
            if used + size > max(0, limit):
                break
            result.append(char)
            used += size
        if not result:
            return
        try:
            screen.addstr(y, x, "".join(result), self.colors.get(color, curses.A_NORMAL))
        except curses.error:
            pass

    def draw_frame(
        self,
        screen: Any,
        top: int,
        left: int,
        width: int,
        height: int,
        title: str = "",
    ) -> None:
        right = left + width - 1
        bottom = top + height - 1
        try:
            screen.addch(top, left, curses.ACS_ULCORNER)
            screen.addch(top, right, curses.ACS_URCORNER)
            screen.addch(bottom, left, curses.ACS_LLCORNER)
            screen.addch(bottom, right, curses.ACS_LRCORNER)
            screen.hline(top, left + 1, curses.ACS_HLINE, width - 2)
            screen.hline(bottom, left + 1, curses.ACS_HLINE, width - 2)
            screen.vline(top + 1, left, curses.ACS_VLINE, height - 2)
            screen.vline(top + 1, right, curses.ACS_VLINE, height - 2)
        except curses.error:
            pass
        if title:
            self.draw_text(screen, top, left + 2, f" {title} ", "title", width - 4)

    def refresh_items(self) -> None:
        needle = self.query.casefold()
        self.filtered_items = [
            item
            for item in self.items
            if (self.filter == "all" or item["status"] == self.filter)
            and (
                not needle
                or needle in item["code"].casefold()
                or needle in item["name"].casefold()
                or needle in item["description"].casefold()
            )
        ]
        if self.selected_code:
            self.selection = next(
                (
                    index
                    for index, item in enumerate(self.filtered_items)
                    if item["code"] == self.selected_code
                ),
                min(self.selection, max(0, len(self.filtered_items) - 1)),
            )
        else:
            self.selection = min(self.selection, max(0, len(self.filtered_items) - 1))
        if self.filtered_items:
            self.selected_code = self.filtered_items[self.selection]["code"]

    def change_filter(self, filter_name: str) -> None:
        self.filter = filter_name
        self.refresh_items()

    def selected_item(self) -> dict[str, Any] | None:
        if not self.filtered_items:
            return None
        self.selection = max(0, min(self.selection, len(self.filtered_items) - 1))
        return self.filtered_items[self.selection]

    def visible_range(self, visible_height: int) -> tuple[int, int]:
        start = max(0, self.selection - visible_height // 2)
        start = min(start, max(0, len(self.filtered_items) - visible_height))
        return start, min(len(self.filtered_items), start + visible_height)

    def catalog_visible_height(self) -> int:
        panel_height = min(32, self.height - 2)
        return max(1, panel_height - 12)

    def open_detail(self, item: dict[str, Any]) -> None:
        self.detail = item
        self.selected_code = item["code"]
        self.detail_lines = []
        for title, field in DETAIL_FIELDS:
            value = item.get(field, "")
            if not isinstance(value, str) or not value.strip():
                continue
            self.detail_lines.append((title, "title"))
            for line in value.splitlines() or [value]:
                self.detail_lines.extend(
                    (wrapped, "text")
                    for wrapped in wrap_text(line, max(10, self.width - 12))
                )
            self.detail_lines.append(("", "text"))
        self.detail_scroll = 0
        self.action_selection = 0
        if item["status"] != "available":
            self.action_selection = 0

    def actions(self) -> list[tuple[str, str]]:
        if self.detail is None:
            return []
        actions = []
        if self.detail["status"] == "available":
            actions.append(("submit", "Registrar entrega"))
        actions.extend((("chat", "Chat do exercício"), ("back", "Voltar ao catálogo")))
        return actions

    def draw_header(self, screen: Any, top: int, left: int, width: int) -> None:
        self.draw_text(
            screen, top + 1, left + 2, "SIMPLE · CATÁLOGO DE EXERCÍCIOS", "title", width - 4
        )
        self.draw_text(
            screen,
            top + 2,
            left + 2,
            f"Conectado como {self.username} · Saldo: {self.points} ponto(s)",
            "muted",
            width - 4,
        )
        try:
            screen.hline(top + 3, left + 2, curses.ACS_HLINE, width - 4)
        except curses.error:
            pass

    def draw_catalog(self, screen: Any, top: int, left: int, width: int, height: int) -> None:
        bottom = top + height - 1
        self.draw_header(screen, top, left, width)
        tabs = "  ".join(
            f"[{label}]" if name == self.filter else label
            for name, label in FILTERS.items()
        )
        self.draw_text(screen, top + 4, left + 2, tabs, "selected", width - 4)
        search_hint = (
            f"Busca: {self.query or 'digite para filtrar'}"
            if self.searching
            else f"Busca: {self.query or 'pressione /'}"
        )
        self.draw_text(screen, top + 5, left + 2, search_hint, "muted", width - 4)

        list_top = top + 6
        list_height = bottom - list_top - 3
        self.draw_frame(
            screen,
            list_top,
            left + 2,
            width - 4,
            list_height,
            f"EXERCÍCIOS · {len(self.filtered_items)}",
        )
        visible_height = self.catalog_visible_height()
        start, end = self.visible_range(visible_height)
        if not self.filtered_items:
            self.draw_text(
                screen,
                list_top + 1,
                left + 4,
                "Nenhum exercício corresponde ao filtro.",
                "muted",
                width - 8,
            )
        else:
            for row, index in enumerate(range(start, end), start=1):
                item = self.filtered_items[index]
                status = item["status"]
                label = (
                    f"{item['code']} · {item['name']}  [{STATUS_LABELS[status]}]"
                )
                selected = index == self.selection
                visible_number = index - start + 1
                direct_key = f"[{visible_number}]" if visible_number <= 9 else "   "
                self.draw_text(
                    screen,
                    list_top + row,
                    left + 4,
                    f"{'›' if selected else ' '} {direct_key} {label}",
                    "selected" if selected else status,
                    width - 8,
                )

        if self.searching:
            footer = "Digite para buscar · Enter aplicar · Esc cancelar"
        else:
            footer = "↑/↓ navegar · Enter detalhes · 1-9 abrir · / buscar · A/D/E/C filtrar · 0 voltar"
        self.draw_text(screen, bottom - 1, left + 2, footer, "muted", width - 4)

    def draw_detail(self, screen: Any, top: int, left: int, width: int, height: int) -> None:
        assert self.detail is not None
        bottom = top + height - 1
        item = self.detail
        self.draw_header(screen, top, left, width)
        status = STATUS_LABELS[item["status"]]
        self.draw_text(
            screen,
            top + 4,
            left + 2,
            f"{item['code']} · {item['name']} · {status}",
            "available" if item["status"] == "available" else item["status"],
            width - 4,
        )
        body_top = top + 5
        body_bottom = bottom - 5
        body_height = body_bottom - body_top + 1
        body_left = left + 2
        body_width = width - 4
        self.draw_frame(screen, body_top, body_left, body_width, body_height, "DETALHES")
        visible_height = max(1, body_height - 2)
        max_scroll = max(0, len(self.detail_lines) - visible_height)
        self.detail_scroll = min(self.detail_scroll, max_scroll)
        visible = self.detail_lines[
            self.detail_scroll : self.detail_scroll + visible_height
        ]
        for index, (line, color) in enumerate(visible, start=1):
            self.draw_text(
                screen, body_top + index, body_left + 2, line, color, body_width - 4
            )
        if max_scroll:
            self.draw_text(
                screen,
                body_top,
                body_left + body_width - 18,
                "PgUp/PgDn",
                "muted",
                16,
            )

        actions = self.actions()
        self.draw_text(screen, bottom - 4, left + 2, "AÇÕES", "title", width - 4)
        action_width = max(1, (width - 8) // len(actions))
        for index, (action, label) in enumerate(actions):
            key = {"submit": "1", "chat": "2", "back": "0"}[action]
            self.draw_text(
                screen,
                bottom - 3,
                left + 2 + index * action_width,
                f"[{key}] {label}",
                "selected" if index == self.action_selection else "text",
                action_width,
            )
        self.draw_text(
            screen,
            bottom - 1,
            left + 2,
            "↑/↓ escolher · Enter confirmar · PgUp/PgDn ler · Esc voltar",
            "muted",
            width - 4,
        )

    def draw(self, screen: Any) -> None:
        self.height, self.width = screen.getmaxyx()
        screen.erase()
        if self.width < 40 or self.height < 18:
            self.draw_text(screen, 0, 0, "Aumente a janela para abrir o catálogo.", "warning")
            self.draw_text(screen, self.height - 1, 0, "Esc voltar", "muted")
            screen.refresh()
            return
        panel_width = min(110, self.width - 2)
        panel_height = min(32, self.height - 2)
        left = (self.width - panel_width) // 2
        top = (self.height - panel_height) // 2
        self.draw_frame(screen, top, left, panel_width, panel_height)
        if self.detail is None:
            self.draw_catalog(screen, top, left, panel_width, panel_height)
        else:
            self.draw_detail(screen, top, left, panel_width, panel_height)
        screen.refresh()

    def choose(self, action: str) -> None:
        code = self.detail["code"] if self.detail else self.selected_code
        result = "\t".join(
            (
                action,
                code,
                self.filter,
                self.query.replace("\t", " "),
                self.selected_code,
            )
        )
        os.write(
            int(os.environ.get("EXERCISE_CATALOG_RESULT_FD", "1")),
            (result + "\n").encode("utf-8"),
        )

    def run(self, screen: Any) -> None:
        curses.cbreak()
        curses.noecho()
        screen.keypad(True)
        screen.timeout(-1)
        self.initialize_colors()
        while True:
            self.draw(screen)
            key = screen.get_wch()
            if key == curses.KEY_RESIZE:
                continue
            if self.detail is None:
                if self.searching:
                    if key in ("\n", "\r", curses.KEY_ENTER):
                        self.searching = False
                        self.refresh_items()
                    elif key in ("\x1b", 27):
                        self.searching = False
                    elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                        self.query = self.query[:-1]
                        self.refresh_items()
                    elif isinstance(key, str) and key.isprintable():
                        self.query += key
                        self.refresh_items()
                    continue
                if key in (curses.KEY_UP, curses.KEY_LEFT):
                    if self.filtered_items:
                        self.selection = (self.selection - 1) % len(self.filtered_items)
                        self.selected_code = self.filtered_items[self.selection]["code"]
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                    if self.filtered_items:
                        self.selection = (self.selection + 1) % len(self.filtered_items)
                        self.selected_code = self.filtered_items[self.selection]["code"]
                elif key in ("\n", "\r", curses.KEY_ENTER):
                    item = self.selected_item()
                    if item is not None:
                        self.open_detail(item)
                elif key in ("\x1b", 27, "0"):
                    self.choose("back")
                    return
                elif key in ("/",):
                    self.searching = True
                elif isinstance(key, str) and key.casefold() in {"a", "d", "e", "c"}:
                    aliases = {
                        "a": "all",
                        "d": "available",
                        "e": "pending",
                        "c": "approved",
                    }
                    self.change_filter(aliases[key.casefold()])
                elif isinstance(key, str) and key.isdigit() and key != "0":
                    visible_height = self.catalog_visible_height()
                    start, end = self.visible_range(visible_height)
                    index = start + int(key) - 1
                    if start <= index < end:
                        self.selection = index
                        self.open_detail(self.filtered_items[index])
            else:
                actions = self.actions()
                if key in (curses.KEY_UP, curses.KEY_LEFT):
                    self.action_selection = (self.action_selection - 1) % len(actions)
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                    self.action_selection = (self.action_selection + 1) % len(actions)
                elif key in (curses.KEY_PPAGE,):
                    self.detail_scroll = max(
                        0, self.detail_scroll - max(1, self.height // 3)
                    )
                elif key in (curses.KEY_NPAGE,):
                    self.detail_scroll += max(1, self.height // 3)
                elif key in ("\x1b", 27):
                    self.detail = None
                elif key in ("\n", "\r", curses.KEY_ENTER):
                    self.choose(actions[self.action_selection][0])
                    return
                elif isinstance(key, str) and key in {"1", "2", "0"}:
                    action_name = {"1": "submit", "2": "chat", "0": "back"}[key]
                    action_index = next(
                        (
                            index
                            for index, (action, _label) in enumerate(actions)
                            if action == action_name
                        ),
                        None,
                    )
                    if action_index is not None:
                        if action_name == "back":
                            self.detail = None
                        else:
                            self.choose(action_name)
                            return


def load_config() -> dict[str, Any]:
    config = json.loads(os.environ["EXERCISE_CATALOG_CONFIG"])
    if not isinstance(config, dict):
        raise ValueError("Configuração do catálogo inválida.")
    if not isinstance(config.get("username"), str):
        raise ValueError("Usuário do catálogo inválido.")
    if type(config.get("points")) is not int or config["points"] < 0:
        raise ValueError("Saldo de pontos inválido.")
    items = config.get("items")
    if not isinstance(items, list):
        raise ValueError("Lista de exercícios inválida.")
    required = {
        "code",
        "name",
        "description",
        "statement",
        "example",
        "delivery_directory",
        "delivery_files",
        "allowed_functions",
        "terminal_model",
        "hints",
        "status",
    }
    for item in items:
        if (
            not isinstance(item, dict)
            or not required.issubset(item)
            or any(not isinstance(item[field], str) for field in required)
            or item["status"] not in STATUS_LABELS
        ):
            raise ValueError("Dados de exercício inválidos.")
    return config


def main() -> int:
    try:
        tui = ExerciseCatalogTui(load_config())
        curses.wrapper(tui.run)
        return 0
    except (OSError, ValueError, KeyError, curses.error, json.JSONDecodeError) as error:
        print(f"Não foi possível abrir o catálogo de exercícios: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
