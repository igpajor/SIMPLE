#!/usr/bin/env python3
"""Responsive curses catalog and exercise detail screens."""

from __future__ import annotations

import curses
import io
import json
import os
import sys
import unicodedata
from http.client import HTTPException
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


FILTERS = {
    "all": "A Todos",
    "available": "D Disponíveis",
    "pending": "E Em avaliação",
    "approved": "C Aprovados",
}
PDF_MAX_BYTES = 20 * 1024 * 1024
PDF_MAX_PAGES = 500
PDF_DOWNLOAD_TIMEOUT_SECONDS = 15
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
    ("PDF ORIGINAL", "pdf_url"),
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
        self.form_open = False
        self.form_focus = 0
        self.commit_url = ""
        self.delivery_directory = ""
        self.form_cursor = 0
        self.form_error = ""
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
        color: str = "text",
    ) -> None:
        right = left + width - 1
        bottom = top + height - 1
        attr = self.colors.get(color, curses.A_NORMAL)
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
        return max(1, panel_height - 14)

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
        if self.detail.get("pdf_url"):
            actions.append(("pdf", "Ler PDF original"))
        actions.extend((("chat", "Chat do exercício"), ("back", "Voltar ao catálogo")))
        return actions

    @staticmethod
    def fetch_pdf_pages(url: str) -> list[str]:
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError as error:
            raise ValueError("O link do PDF não é uma URL HTTPS válida.") from error
        if (
            len(url) > 2048
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(char.isspace() for char in url)
        ):
            raise ValueError("O link do PDF não é uma URL HTTPS válida.")

        try:
            from pypdf import PdfReader
            from pypdf.errors import PyPdfError
        except ImportError as error:
            raise RuntimeError(
                "O leitor PDF requer a dependência pypdf. Instale os requisitos do projeto."
            ) from error

        request = Request(url, headers={"User-Agent": "SIMPLE PDF reader"})
        with urlopen(request, timeout=PDF_DOWNLOAD_TIMEOUT_SECONDS) as response:
            try:
                final_url = urlsplit(response.geturl())
                final_url.port
            except ValueError as error:
                raise ValueError(
                    "O servidor redirecionou para um endereço inseguro."
                ) from error
            if (
                final_url.scheme != "https"
                or not final_url.hostname
                or final_url.username is not None
                or final_url.password is not None
                or len(response.geturl()) > 2048
                or any(char.isspace() for char in response.geturl())
            ):
                raise ValueError("O servidor redirecionou para um endereço inseguro.")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > PDF_MAX_BYTES:
                raise ValueError("O PDF excede o limite de 20 MB.")
            data = bytearray()
            while True:
                chunk = response.read(min(65536, PDF_MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > PDF_MAX_BYTES:
                    raise ValueError("O PDF excede o limite de 20 MB.")

        if b"%PDF-" not in data[:1024]:
            raise ValueError("O endereço não retornou um arquivo PDF válido.")

        try:
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted:
                raise ValueError("Este PDF é protegido por senha e não pode ser lido.")
            if len(reader.pages) > PDF_MAX_PAGES:
                raise ValueError("O PDF excede o limite de 500 páginas.")
            pages = [page.extract_text() or "" for page in reader.pages]
            if not pages:
                raise ValueError("O PDF não contém páginas.")
            return pages
        except PyPdfError as error:
            raise ValueError(f"Não foi possível ler o PDF: {error}") from error

    def draw_pdf_page(
        self,
        screen: Any,
        pages: list[str] | None = None,
        page_index: int = 0,
        scroll: int = 0,
        error: str = "",
    ) -> int:
        self.height, self.width = screen.getmaxyx()
        screen.erase()
        panel_width = min(110, self.width - 2)
        panel_height = min(30, self.height - 2)
        if panel_width < 4 or panel_height < 5:
            self.draw_text(screen, 0, 0, "Aumente o tamanho do terminal.", "warning")
            screen.refresh()
            return 0

        left = (self.width - panel_width) // 2
        top = (self.height - panel_height) // 2
        page_count = len(pages or [])
        title = (
            f"PDF ORIGINAL · PÁGINA {page_index + 1}/{page_count}"
            if pages
            else "LEITOR DE PDF"
        )
        self.draw_frame(screen, top, left, panel_width, panel_height, title, "title")
        body_width = panel_width - 4
        body_height = panel_height - 4
        if error:
            lines = wrap_text(error, body_width)
        else:
            page_text = (pages or [""])[page_index]
            lines = wrap_text(page_text or "Esta página não contém texto extraível.", body_width)
        max_scroll = max(0, len(lines) - body_height)
        scroll = min(max(0, scroll), max_scroll)
        for offset, line in enumerate(lines[scroll : scroll + body_height]):
            self.draw_text(screen, top + 2 + offset, left + 2, line, "text", body_width)
        footer = (
            "Pressione qualquer tecla para voltar"
            if error
            else "↑/↓ rolar · PgUp/PgDn rolar mais · ←/→ página anterior/próxima · Esc voltar"
        )
        self.draw_text(screen, top + panel_height - 2, left + 2, footer, "muted", body_width)
        screen.refresh()
        return max_scroll

    def read_pdf(self, screen: Any) -> None:
        assert self.detail is not None
        screen.erase()
        self.draw_text(screen, 1, 2, "Baixando e preparando o PDF...", "muted")
        screen.refresh()
        try:
            pages = self.fetch_pdf_pages(self.detail["pdf_url"])
        except (HTTPException, OSError, RuntimeError, ValueError, URLError) as error:
            self.draw_pdf_page(screen, error=str(error))
            screen.get_wch()
            return

        page_index = 0
        scroll = 0
        while True:
            max_scroll = self.draw_pdf_page(screen, pages, page_index, scroll)
            key = screen.get_wch()
            if key in ("\x1b", 27):
                return
            if key in (curses.KEY_UP,):
                scroll = max(0, scroll - 1)
            elif key in (curses.KEY_DOWN,):
                scroll = min(max_scroll, scroll + 1)
            elif key == curses.KEY_PPAGE:
                scroll = max(0, scroll - max(1, (self.height - 6) // 2))
            elif key == curses.KEY_NPAGE:
                scroll = min(max_scroll, scroll + max(1, (self.height - 6) // 2))
            elif key in (curses.KEY_LEFT,):
                page_index = max(0, page_index - 1)
                scroll = 0
            elif key in (curses.KEY_RIGHT, "\n", "\r", curses.KEY_ENTER):
                page_index = min(len(pages) - 1, page_index + 1)
                scroll = 0

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
            option_width = min(76, width - 10)
            option_left = left + (width - option_width) // 2
            option_y = list_top + 1
            for index in range(start, end):
                item = self.filtered_items[index]
                status = item["status"]
                label = (
                    f"{item['code']} · {item['name']}  [{STATUS_LABELS[status]}]"
                )
                selected = index == self.selection
                if selected:
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
                        f"› {label}",
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
                        label,
                        status,
                        option_width - 4,
                    )
                    option_y += 1

        if self.searching:
            footer = "Digite para buscar · Enter aplicar · Esc cancelar"
        else:
            footer = "↑/↓ navegar · Enter detalhes · / buscar · A/D/E/C filtrar · Esc voltar"
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
        body_bottom = bottom - 10
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
        self.draw_text(screen, bottom - 6, left + 2, "AÇÕES", "title", width - 4)
        action_gap = 2
        action_width = min(
            38,
            max(1, (width - 8 - (len(actions) - 1) * action_gap) // len(actions)),
        )
        total_actions_width = len(actions) * action_width + (len(actions) - 1) * action_gap
        action_left = left + max(2, (width - total_actions_width) // 2)
        for index, (action, label) in enumerate(actions):
            x = action_left + index * (action_width + action_gap)
            if index == self.action_selection:
                self.draw_text(
                    screen,
                    bottom - 5,
                    x,
                    f"┌{'─' * (action_width - 2)}┐",
                    "selected",
                    action_width,
                )
                self.draw_text(
                    screen,
                    bottom - 4,
                    x + 2,
                    label,
                    "selected",
                    action_width - 4,
                )
                self.draw_text(
                    screen,
                    bottom - 3,
                    x,
                    f"└{'─' * (action_width - 2)}┘",
                    "selected",
                    action_width,
                )
            else:
                self.draw_text(
                    screen,
                    bottom - 4,
                    x + 2,
                    label,
                    "text",
                    action_width - 4,
                )
        self.draw_text(
            screen,
            bottom - 1,
            left + 2,
            "↑/↓ escolher · Enter confirmar · PgUp/PgDn ler · Esc voltar",
            "muted",
            width - 4,
        )

    def draw_form(self, screen: Any, top: int, left: int, width: int, height: int) -> None:
        bottom = top + height - 1
        self.draw_header(screen, top, left, width)
        code = self.detail["code"] if self.detail else ""
        self.draw_text(
            screen,
            top + 4,
            left + 2,
            f"REGISTRAR ENTREGA · {code}",
            "title",
            width - 4,
        )
        self.draw_text(
            screen,
            top + 5,
            left + 2,
            "O SIMPLE registra apenas a referência; não recebe nem executa seu código.",
            "muted",
            width - 4,
        )

        inner_left = left + 2
        inner_width = width - 4
        url_top = top + 7
        self.draw_frame(
            screen,
            url_top,
            inner_left,
            inner_width,
            4,
            "LINK DO COMMIT GITHUB",
            "selected" if self.form_focus == 0 else "text",
        )
        url_attr = "selected" if self.form_focus == 0 else "text"
        url_max_chars = max(1, inner_width - 4)
        url_start = (
            max(0, self.form_cursor - url_max_chars + 1)
            if self.form_focus == 0
            else max(0, len(self.commit_url) - url_max_chars)
        )
        visible_url = self.commit_url[url_start : url_start + url_max_chars]
        self.draw_text(
            screen,
            url_top + 1,
            inner_left + 2,
            visible_url
            or (
                ""
                if self.form_focus == 0
                else "https://github.com/usuario/repositorio/commit/SHA"
            ),
            url_attr if self.commit_url else "muted",
            inner_width - 4,
        )
        self.draw_text(
            screen,
            url_top + 2,
            inner_left + 2,
            "Digite ou cole o link direto do commit.",
            "muted",
            inner_width - 4,
        )
        directory_top = url_top + 5
        self.draw_frame(
            screen,
            directory_top,
            inner_left,
            inner_width,
            4,
            "DIRETÓRIO DA ENTREGA",
            "selected" if self.form_focus == 1 else "text",
        )
        directory_attr = "selected" if self.form_focus == 1 else "text"
        directory_max_chars = max(1, inner_width - 4)
        directory_start = (
            max(0, self.form_cursor - directory_max_chars + 1)
            if self.form_focus == 1
            else max(0, len(self.delivery_directory) - directory_max_chars)
        )
        visible_directory = self.delivery_directory[
            directory_start : directory_start + directory_max_chars
        ]
        self.draw_text(
            screen,
            directory_top + 1,
            inner_left + 2,
            visible_directory
            or (
                ""
                if self.form_focus == 1
                else "ex00 (ou . se estiver na raiz)"
            ),
            directory_attr if self.delivery_directory else "muted",
            inner_width - 4,
        )
        self.draw_text(
            screen,
            directory_top + 2,
            inner_left + 2,
            "Informe a pasta dos arquivos; use . para a raiz.",
            "muted",
            inner_width - 4,
        )
        self.draw_text(
            screen,
            directory_top + 4,
            inner_left,
            "O link deve conter /commit/SHA; não use links de arquivo ou pasta.",
            "muted",
            inner_width,
        )

        confirm_top = bottom - 5
        confirm_width = min(44, inner_width)
        confirm_left = inner_left + (inner_width - confirm_width) // 2
        if self.form_focus == 2:
            self.draw_text(
                screen,
                confirm_top,
                confirm_left,
                f"┌{'─' * (confirm_width - 2)}┐",
                "selected",
                confirm_width,
            )
            self.draw_text(
                screen,
                confirm_top + 1,
                confirm_left + 2,
                "Confirmar envio da referência",
                "selected",
                confirm_width - 4,
            )
            self.draw_text(
                screen,
                confirm_top + 2,
                confirm_left,
                f"└{'─' * (confirm_width - 2)}┘",
                "selected",
                confirm_width,
            )
        else:
            self.draw_text(
                screen,
                confirm_top + 1,
                confirm_left + 2,
                "Confirmar envio da referência",
                "text",
                confirm_width - 4,
            )
        self.draw_text(
            screen,
            bottom - 1,
            left + 2,
            "Tab/↑↓ mudar campo · Enter editar/avançar · Esc cancelar",
            "muted",
            width - 4,
        )
        if self.form_error:
            self.draw_text(
                screen,
                bottom - 2,
                left + 2,
                self.form_error,
                "warning",
                width - 4,
            )
        if self.form_focus < 2:
            field_value = (
                self.commit_url
                if self.form_focus == 0
                else self.delivery_directory
            )
            field_x = inner_left + 2
            field_y = url_top + 1 if self.form_focus == 0 else directory_top + 1
            max_chars = max(1, inner_width - 4)
            cursor = min(self.form_cursor, len(field_value))
            visible_start = max(0, cursor - max_chars + 1)
            visible_text = field_value[visible_start : visible_start + max_chars]
            cursor_x = field_x + sum(
                char_width(char) for char in visible_text[: cursor - visible_start]
            )
            cursor_x = min(inner_left + inner_width - 2, cursor_x)
            try:
                curses.curs_set(1)
                screen.move(field_y, cursor_x)
            except curses.error:
                pass

    def edit_form_field(self, key: Any) -> None:
        field_name = "commit_url" if self.form_focus == 0 else "delivery_directory"
        current = getattr(self, field_name)
        if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            if self.form_cursor:
                setattr(
                    self,
                    field_name,
                    current[: self.form_cursor - 1] + current[self.form_cursor :],
                )
                self.form_cursor -= 1
        elif key == curses.KEY_DC:
            setattr(
                self,
                field_name,
                current[: self.form_cursor] + current[self.form_cursor + 1 :],
            )
        elif key == curses.KEY_LEFT:
            self.form_cursor = max(0, self.form_cursor - 1)
        elif key == curses.KEY_RIGHT:
            self.form_cursor = min(len(current), self.form_cursor + 1)
        elif key == curses.KEY_HOME:
            self.form_cursor = 0
        elif key == curses.KEY_END:
            self.form_cursor = len(current)
        elif key == "\x15":
            setattr(self, field_name, "")
            self.form_cursor = 0
        elif isinstance(key, str) and key.isprintable() and key not in "\t\r\n":
            max_length = 2048 if field_name == "commit_url" else 256
            if len(current) >= max_length:
                self.form_error = "O campo atingiu o limite de caracteres."
                return
            setattr(
                self,
                field_name,
                current[: self.form_cursor] + key + current[self.form_cursor :],
            )
            self.form_cursor += len(key)

    def move_form_focus(self, step: int) -> None:
        self.form_focus = (self.form_focus + step) % 3
        if self.form_focus == 0:
            self.form_cursor = len(self.commit_url)
        elif self.form_focus == 1:
            self.form_cursor = len(self.delivery_directory)

    def draw(self, screen: Any) -> None:
        self.height, self.width = screen.getmaxyx()
        screen.erase()
        minimum_height = 27 if self.detail is not None else 18
        if self.width < 40 or self.height < minimum_height:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.draw_text(screen, 0, 0, "Aumente a janela para abrir o catálogo.", "warning")
            self.draw_text(screen, self.height - 1, 0, "Esc voltar", "muted")
            screen.refresh()
            return
        panel_width = min(110, self.width - 2)
        panel_height = min(32, self.height - 2)
        left = (self.width - panel_width) // 2
        top = (self.height - panel_height) // 2
        self.draw_frame(screen, top, left, panel_width, panel_height)
        if self.form_open:
            self.draw_form(screen, top, left, panel_width, panel_height)
        elif self.detail is None:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.draw_catalog(screen, top, left, panel_width, panel_height)
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
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
                self.commit_url if action == "submit" else "",
                self.delivery_directory if action == "submit" else "",
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
            if self.form_open:
                if key in ("\x1b", 27):
                    self.form_open = False
                    self.form_error = ""
                elif key in ("\t", curses.KEY_DOWN, curses.KEY_UP):
                    step = -1 if key == curses.KEY_UP else 1
                    self.move_form_focus(step)
                    self.form_error = ""
                elif key in ("\n", "\r", curses.KEY_ENTER):
                    if self.form_focus < 2:
                        self.move_form_focus(1)
                    elif self.commit_url and self.delivery_directory:
                        self.choose("submit")
                        return
                    else:
                        self.form_error = "Informe o link do commit e o diretório da entrega."
                elif self.form_focus < 2:
                    self.edit_form_field(key)
                    self.form_error = ""
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
                elif key in ("\x1b", 27):
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
                    action = actions[self.action_selection][0]
                    if action == "back":
                        self.detail = None
                    elif action == "submit":
                        self.commit_url = ""
                        self.delivery_directory = ""
                        self.form_focus = 0
                        self.form_error = ""
                        self.form_open = True
                    elif action == "pdf":
                        self.read_pdf(screen)
                    else:
                        self.choose(action)
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
        "pdf_url",
        "status",
    }
    for item in items:
        if isinstance(item, dict):
            item.setdefault("pdf_url", "")
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
