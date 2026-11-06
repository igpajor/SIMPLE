#!/usr/bin/env python3
"""Unified profile, progress, and submissions dashboard."""

from __future__ import annotations

import curses
import json
import os
import sys
import unicodedata
from typing import Any


STATUS_LABELS = {
    "pending": "Em avaliação",
    "approved": "Aprovada",
    "rejected": "Reprovada",
}

BLOCK_GLYPHS = {
    "A": ("01110", "10001", "11111", "10001", "10001"),
    "B": ("11110", "10001", "11110", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "11110", "10000", "11111"),
    "F": ("11111", "10000", "11110", "10000", "10000"),
    "G": ("01111", "10000", "10111", "10001", "01111"),
    "H": ("10001", "10001", "11111", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "11100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001"),
    "O": ("01110", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "11110", "10000", "10000"),
    "Q": ("01110", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "11110", "10010", "10001"),
    "S": ("01111", "10000", "01110", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10101", "11011", "10001"),
    "X": ("10001", "01010", "00100", "01010", "10001"),
    "Y": ("10001", "01010", "00100", "00100", "00100"),
    "Z": ("11111", "00010", "00100", "01000", "11111"),
    "?": ("11110", "00010", "00100", "00000", "00100"),
}


def safe_text(value: Any) -> str:
    return "".join(
        char
        for char in str(value or "")
        if char == "\n" or unicodedata.category(char) not in {"Cc", "Cs"}
    )


def wrap_text(text: str, width: int) -> list[str]:
    width = max(1, width)
    lines: list[str] = []
    for source in safe_text(text).splitlines() or [""]:
        if not source:
            lines.append("")
            continue
        while len(source) > width:
            split_at = source.rfind(" ", 0, width + 1)
            if split_at <= 0:
                split_at = width
            lines.append(source[:split_at])
            source = source[split_at:].lstrip()
        lines.append(source)
    return lines


class AccountDashboardTui:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.username = safe_text(config["username"])
        self.display_name = safe_text(config.get("display_name", ""))
        self.github_username = safe_text(config.get("github_username", ""))
        self.bio = safe_text(config.get("bio", ""))
        self.points = config["points"]
        self.counts = config.get("counts", {})
        self.submissions = config["submissions"]
        self.points_history = config.get("points_history", [])
        self.requests = config.get("requests", [])
        self.notifications = config.get("notifications", [])
        self.review_history = config.get("review_history", [])
        self.pending_review_responses = config.get("pending_review_responses", [])
        self.selected_id = config.get("selected_submission_id")
        self.detail = config.get("detail")
        self.screen = (
            "detail"
            if self.detail
            else "progress"
            if config.get("open_progress")
            else "dashboard"
        )
        self.selection = 0
        if self.selected_id is not None:
            self.selection = next(
                (
                    index
                    for index, item in enumerate(self.submissions)
                    if item["id"] == self.selected_id
                ),
                0,
            )
        self.scroll = 0
        self.points_scroll = 0
        self.progress_scroll = 0
        self.form_selection = 0
        self.form_values = [self.display_name, self.github_username, self.bio]
        self.form_editing = False
        self.form_buffer = ""
        self.width = 80
        self.height = 24
        self.colors: dict[str, int] = {}

    def initialize_colors(self) -> None:
        if os.environ.get("NO_COLOR") or not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        for index, (name, foreground, attributes) in enumerate(
            (
                ("title", curses.COLOR_CYAN, curses.A_BOLD),
                ("selected", curses.COLOR_CYAN, curses.A_BOLD),
                ("text", curses.COLOR_WHITE, curses.A_NORMAL),
                ("muted", curses.COLOR_WHITE, curses.A_DIM),
                ("pending", curses.COLOR_YELLOW, curses.A_BOLD),
                ("approved", curses.COLOR_GREEN, curses.A_BOLD),
                ("rejected", curses.COLOR_RED, curses.A_BOLD),
                ("warning", curses.COLOR_YELLOW, curses.A_BOLD),
            ),
            start=1,
        ):
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
        if limit <= 0:
            return
        try:
            screen.addnstr(
                y,
                x,
                safe_text(text).replace("\n", " ").replace("\t", " "),
                limit,
                self.colors.get(color, curses.A_NORMAL),
            )
        except curses.error:
            pass

    def draw_frame(
        self, screen: Any, top: int, left: int, width: int, height: int, title: str = ""
    ) -> None:
        if width < 4 or height < 3:
            return
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
        if title:
            self.draw_text(screen, top, left + 2, f" {title} ", "title", width - 4)

    def selected_submission(self) -> dict[str, Any] | None:
        if self.detail:
            return self.detail
        if not self.submissions:
            return None
        self.selection = max(0, min(self.selection, len(self.submissions) - 1))
        return self.submissions[self.selection]

    def submission_requests(self, submission: dict[str, Any]) -> list[dict[str, Any]]:
        slot_id = str(submission.get("slot_id") or "")
        return [item for item in self.requests if str(item.get("slot_id")) == slot_id]

    def profile_initials(self) -> str:
        words = (self.display_name or self.username).split()
        if not words:
            return "?"
        initials = words[0][0]
        if len(words) > 1:
            initials += words[-1][0]
        normalized = unicodedata.normalize("NFKD", initials).upper()
        return "".join(
            char if char in BLOCK_GLYPHS else "?"
            for char in normalized
            if not unicodedata.combining(char)
        ) or "?"

    def draw_avatar(self, screen: Any, top: int, left: int) -> None:
        avatar_width = 14
        self.draw_frame(screen, top, left, avatar_width, 7)
        glyphs = [BLOCK_GLYPHS[char] for char in self.profile_initials()]
        for row in range(5):
            pixels = "  ".join(glyph[row] for glyph in glyphs)
            art = "".join("█" if pixel == "1" else " " for pixel in pixels)
            self.draw_text(
                screen,
                top + 1 + row,
                left + 1,
                art,
                "title",
                avatar_width - 2,
            )

    def draw_identity(self, screen: Any, left: int, top: int, width: int) -> None:
        height = 9
        self.draw_frame(screen, top, left, width, height, "PERFIL")
        avatar_left = left + 2
        self.draw_avatar(screen, top + 1, avatar_left)
        details_left = avatar_left + 16
        details_width = width - (details_left - left) - 2
        if details_width <= 0:
            return
        self.draw_text(
            screen,
            top + 2,
            details_left,
            self.display_name or self.username,
            "title",
            details_width,
        )
        row = top + 3
        if self.github_username:
            self.draw_text(
                screen,
                row,
                details_left,
                f"@{self.github_username}",
                "selected",
                details_width,
            )
            row += 1
        if self.bio:
            bio_lines = wrap_text(self.bio.replace("\n", " "), details_width)
            available = top + height - 1 - row
            if len(bio_lines) > available:
                bio_lines = bio_lines[:available]
                if bio_lines and len(bio_lines[-1]) >= details_width:
                    bio_lines[-1] = bio_lines[-1][: max(0, details_width - 1)] + "…"
            for line in bio_lines:
                self.draw_text(screen, row, details_left, line, "text", details_width)
                row += 1

    def stats_columns(self, width: int) -> int:
        if width >= 68:
            return 4
        if width >= 28:
            return 2
        return 1

    def draw_stats(self, screen: Any, left: int, top: int, width: int) -> int:
        columns = self.stats_columns(width - 2)
        metrics = [
            ("Pontos", self.points, "selected"),
            ("Aprovadas", self.counts.get("approved", 0), "approved"),
            ("Reprovadas", self.counts.get("rejected", 0), "rejected"),
            (
                "Em avaliação" if columns == 4 else "Em aval.",
                self.counts.get("pending", 0),
                "pending",
            ),
        ]
        rows = (len(metrics) + columns - 1) // columns
        height = rows + 2
        self.draw_frame(screen, top, left, width, height, "RESUMO")
        cell_width = max(1, (width - 2) // columns)
        for index, (label, value, color) in enumerate(metrics):
            row = index // columns
            column = index % columns
            self.draw_text(
                screen,
                top + 1 + row,
                left + 1 + column * cell_width,
                f"{label}: {value}",
                color,
                cell_width,
            )
        return height

    def draw_submissions_panel(
        self, screen: Any, left: int, top: int, width: int, height: int
    ) -> None:
        self.draw_frame(screen, top, left, width, height, "MINHAS ENTREGAS")
        visible = height - 2
        if visible <= 0:
            return
        if not self.submissions:
            self.draw_text(
                screen, top + 1, left + 1, "Nenhuma entrega.", "muted", width - 2
            )
            return
        self.selection = max(0, min(self.selection, len(self.submissions) - 1))
        start = max(0, self.selection - visible // 2)
        start = min(start, max(0, len(self.submissions) - visible))
        end = min(len(self.submissions), start + visible)
        for row, index in enumerate(range(start, end), start=top + 1):
            item = self.submissions[index]
            label = (
                f"{item['exercise_code']} · {item['exercise_name']} "
                f"[{STATUS_LABELS.get(item['status'], item['status'])}]"
            )
            if item.get("score") not in (None, ""):
                label += f" · {item['score']}/5"
            self.draw_text(
                screen,
                row,
                left + 1,
                f"› {label}" if index == self.selection else label,
                "selected"
                if index == self.selection
                else item["status"]
                if item["status"] in STATUS_LABELS
                else "text",
                width - 2,
            )

    def draw_notifications_panel(
        self, screen: Any, left: int, top: int, width: int, height: int
    ) -> None:
        self.draw_frame(
            screen,
            top,
            left,
            width,
            height,
            f"NOTIFICAÇÕES · {len(self.notifications)}",
        )
        visible = height - 2
        if visible <= 0:
            return
        if not self.notifications:
            self.draw_text(
                screen,
                top + 1,
                left + 1,
                "Nenhuma notificação nova.",
                "muted",
                width - 2,
            )
            return
        shown = visible if visible == 1 else min(len(self.notifications), visible - 1)
        for index, item in enumerate(self.notifications[:shown]):
            kind = (
                "Avaliação recebida"
                if item.get("type") == "review_received"
                else "Resposta recebida"
            )
            label = f"{item.get('exercise_code', '')} · {kind}"
            if item.get("other_user"):
                label += f" · {item['other_user']}"
            self.draw_text(
                screen,
                top + 1 + index,
                left + 1,
                label,
                "warning",
                width - 2,
            )
        remaining = len(self.notifications) - shown
        if remaining > 0 and visible > 1:
            self.draw_text(
                screen,
                top + height - 2,
                left + 1,
                f"+{remaining} · P para ver todas",
                "muted",
                width - 2,
            )

    def draw_dashboard(self, screen: Any) -> None:
        panel_width = min(120, self.width - 2)
        left = max(0, (self.width - panel_width) // 2)
        panel_height = self.height - 1
        self.draw_frame(
            screen,
            0,
            left,
            panel_width,
            panel_height,
            "SIMPLE / MEU CADASTRO",
        )
        content_left = left + 2
        content_width = panel_width - 4
        identity_top = 1
        stats_top = identity_top + 10
        columns = self.stats_columns(content_width - 2)
        stats_height = (4 + columns - 1) // columns + 2
        body_top = stats_top + stats_height + 1
        body_height = panel_height - 1 - body_top
        minimum_body_height = 3 if content_width >= 70 else 6
        if body_height < minimum_body_height:
            self.draw_text(
                screen,
                self.height - 3,
                left + 2,
                "Aumente a altura do terminal.",
                "warning",
            )
            return
        self.draw_identity(screen, content_left, identity_top, content_width)
        self.draw_stats(screen, content_left, stats_top, content_width)
        if content_width >= 70:
            gap = 1
            submissions_width = (content_width - gap) // 2
            notifications_width = content_width - gap - submissions_width
            self.draw_submissions_panel(
                screen, content_left, body_top, submissions_width, body_height
            )
            self.draw_notifications_panel(
                screen,
                content_left + submissions_width + gap,
                body_top,
                notifications_width,
                body_height,
            )
        elif body_height >= 6:
            submissions_height = body_height // 2
            self.draw_submissions_panel(
                screen,
                content_left,
                body_top,
                content_width,
                submissions_height,
            )
            self.draw_notifications_panel(
                screen,
                content_left,
                body_top + submissions_height,
                content_width,
                body_height - submissions_height,
            )
        footer = (
            "↑/↓ entregas · Enter detalhes · E editar · P progresso · "
            "F3 pontos · Esc voltar"
        )
        self.draw_text(
            screen,
            self.height - 1,
            left + 1,
            footer,
            "muted",
            panel_width - 2,
        )

    def detail_lines(self) -> list[tuple[str, str]]:
        assert self.detail is not None
        item = self.detail
        lines: list[tuple[str, str]] = [
            (
                f"{item.get('exercise_code', '')} · {item.get('exercise_name', '')}",
                "title",
            ),
            (
                "Status: "
                f"{STATUS_LABELS.get(item.get('status', ''), item.get('status', ''))}",
                item.get("status", "text"),
            ),
            (f"Enviada em: {item.get('submitted_at') or '—'}", "muted"),
        ]
        exercise = item.get("exercise", {})
        for label, field in (
            ("Resumo", "description"),
            ("Exercício", "statement"),
            ("Diretório de entrega", "delivery_directory"),
            ("Commit", "commit_url"),
        ):
            value = item.get(field) if field in item else exercise.get(field)
            if value:
                lines.append((f"{label}: {value}", "text"))
        if item.get("reviewer"):
            lines.append((f"Avaliador: {item['reviewer']}", "text"))
        if item.get("score") not in (None, ""):
            lines.append((f"Nota: {item['score']}/5", "selected"))
        if item.get("feedback"):
            lines.append((f"Feedback: {item['feedback']}", "text"))
        related = self.submission_requests(item)
        pending = next(
            (req for req in related if req.get("status") == "pending"), None
        )
        accepted = next(
            (req for req in related if req.get("status") == "accepted"), None
        )
        if pending:
            lines.append(
                (
                    f"Pedido de {pending.get('reviewer', 'avaliador')} "
                    "aguardando decisão.",
                    "warning",
                )
            )
            lines.append(("A: aceitar pedido   ·   D: recusar pedido", "selected"))
        elif accepted:
            lines.append(
                (
                    f"Avaliação aceita por {accepted.get('reviewer', 'avaliador')}.",
                    "approved",
                )
            )
            lines.append(("M: abrir DM e sala de avaliação", "selected"))
        elif item.get("status") == "pending":
            lines.append(("Aguardando um avaliador aceitar esta entrega.", "muted"))
        return lines

    def draw_detail(self, screen: Any) -> None:
        panel_width = min(112, self.width - 2)
        left = max(0, (self.width - panel_width) // 2)
        top = 1
        height = self.height - 3
        self.draw_frame(screen, top, left, panel_width, height, "DETALHES DA ENTREGA")
        content = [
            (wrapped, color)
            for text, color in self.detail_lines()
            for wrapped in wrap_text(text, panel_width - 5)
        ]
        visible = max(1, height - 2)
        self.scroll = min(self.scroll, max(0, len(content) - visible))
        for index, (line, color) in enumerate(
            content[self.scroll : self.scroll + visible]
        ):
            self.draw_text(
                screen, top + 1 + index, left + 2, line, color, panel_width - 4
            )
        self.draw_text(
            screen,
            self.height - 1,
            left + 1,
            "↑/↓ rolar · A/D responder · M abrir DM · Esc voltar",
            "muted",
            panel_width - 2,
        )

    def draw_profile(self, screen: Any) -> None:
        panel_width = min(76, self.width - 2)
        panel_height = min(13, self.height - 2)
        left = max(0, (self.width - panel_width) // 2)
        top = max(0, (self.height - panel_height) // 2)
        self.draw_frame(screen, top, left, panel_width, panel_height, "EDITAR PERFIL")
        labels = ("Nome de exibição", "Usuário GitHub", "Mini bio")
        for index, label in enumerate(labels):
            value = self.form_values[index]
            if index == self.form_selection and self.form_editing:
                value = self.form_buffer
            value = value.replace("\n", " ")
            marker = "› " if index == self.form_selection else "  "
            self.draw_text(
                screen,
                top + 2 + index * 2,
                left + 2,
                f"{marker}{label}: {value or '(vazio)'}",
                "selected" if index == self.form_selection else "text",
                panel_width - 4,
            )
        self.draw_text(
            screen,
            top + panel_height - 2,
            left + 2,
            "Enter editar/salvar · ↑/↓ campos · S gravar · Esc cancelar",
            "muted",
            panel_width - 4,
        )

    def draw_points(self, screen: Any) -> None:
        panel_width = min(92, self.width - 2)
        panel_height = min(self.height - 2, max(7, len(self.points_history) + 4))
        left = max(0, (self.width - panel_width) // 2)
        top = max(0, (self.height - panel_height) // 2)
        self.draw_frame(
            screen, top, left, panel_width, panel_height, "HISTÓRICO DE PONTOS · F3"
        )
        if not self.points_history:
            self.draw_text(
                screen,
                top + 2,
                left + 2,
                "Nenhuma movimentação registrada.",
                "muted",
            )
        visible = panel_height - 3
        self.points_scroll = min(
            self.points_scroll, max(0, len(self.points_history) - visible)
        )
        for index, entry in enumerate(
            self.points_history[self.points_scroll : self.points_scroll + visible]
        ):
            amount = entry.get("amount", 0)
            label = (
                f"{amount:+} · {entry.get('entry_type', '')} · "
                f"{entry.get('details', '')} ({entry.get('created_at', '')})"
            )
            self.draw_text(
                screen,
                top + 1 + index,
                left + 2,
                label,
                "text",
                panel_width - 4,
            )
        self.draw_text(
            screen,
            top + panel_height - 2,
            left + 2,
            "↑/↓ rolar · F3 ou Esc voltar",
            "muted",
        )

    def progress_lines(self) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [("NOTIFICAÇÕES", "title")]
        if not self.notifications:
            lines.append(("Nenhuma notificação nova.", "muted"))
        for item in self.notifications:
            notification_type = (
                "Nova avaliação"
                if item.get("type") == "review_received"
                else "Resposta de avaliação"
            )
            lines.append(
                (
                    f"{item.get('exercise_code', '')} · {notification_type} · "
                    f"{item.get('other_user', '')} · nota {item.get('score', '')}/5 "
                    f"({item.get('created_at', '')})",
                    "text",
                )
            )
            if item.get("feedback"):
                lines.append((f"Feedback: {item['feedback']}", "muted"))
        lines.append(("HISTÓRICO DE AVALIAÇÕES", "title"))
        if not self.review_history:
            lines.append(("Nenhuma avaliação registrada.", "muted"))
        for item in self.review_history:
            relation = (
                "recebida"
                if item.get("relation") == "received"
                else "realizada"
            )
            lines.append(
                (
                    f"{item.get('exercise_code', '')} · {relation} · "
                    f"nota {item.get('score', '')}/5 · avaliador "
                    f"{item.get('reviewer', '')} · {item.get('created_at', '')}",
                    "text",
                )
            )
            if item.get("feedback"):
                lines.append((f"Feedback: {item['feedback']}", "muted"))
            if item.get("recipient_score") not in (None, ""):
                lines.append(
                    (
                        f"Sua resposta: nota {item['recipient_score']}/5 · "
                        f"{item.get('recipient_feedback', '')}",
                        "muted",
                    )
                )
        lines.append(("RESPONDER A UMA AVALIAÇÃO", "title"))
        if self.pending_review_responses:
            for item in self.pending_review_responses:
                lines.append(
                    (
                        f"{item.get('exercise_code', '')} · avaliação de "
                        f"{item.get('reviewer', '')} · nota {item.get('score', '')}/5",
                        "warning",
                    )
                )
        else:
            lines.append(("Nenhuma avaliação aguardando sua resposta.", "muted"))
        return lines

    def draw_progress(self, screen: Any) -> None:
        panel_width = min(112, self.width - 2)
        left = max(0, (self.width - panel_width) // 2)
        top = 1
        height = self.height - 3
        self.draw_frame(screen, top, left, panel_width, height, "MEU PROGRESSO")
        content = [
            (wrapped, color)
            for text, color in self.progress_lines()
            for wrapped in wrap_text(text, panel_width - 5)
        ]
        visible = max(1, height - 2)
        max_scroll = max(0, len(content) - visible)
        self.progress_scroll = min(self.progress_scroll, max_scroll)
        for index, (line, color) in enumerate(
            content[self.progress_scroll : self.progress_scroll + visible]
        ):
            self.draw_text(
                screen, top + 1 + index, left + 2, line, color, panel_width - 4
            )
        self.draw_text(
            screen,
            self.height - 1,
            left + 1,
            "↑/↓ rolar · R responder pendente · Esc voltar",
            "muted",
            panel_width - 2,
        )

    def draw(self, screen: Any) -> None:
        self.height, self.width = screen.getmaxyx()
        screen.erase()
        if self.width < 36 or self.height < 12:
            self.draw_text(
                screen, 0, 0, "Aumente o terminal para continuar.", "warning"
            )
        elif self.screen == "dashboard":
            self.draw_dashboard(screen)
        elif self.screen == "detail":
            self.draw_detail(screen)
        elif self.screen == "profile":
            self.draw_profile(screen)
        elif self.screen == "points":
            self.draw_points(screen)
        elif self.screen == "progress":
            self.draw_progress(screen)
        screen.refresh()

    def result(self, action: str, **values: Any) -> dict[str, Any]:
        return {"action": action, **values}

    def run(self, screen: Any) -> dict[str, Any]:
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
            if self.screen == "dashboard":
                if key in (curses.KEY_UP, curses.KEY_LEFT):
                    self.selection = max(0, self.selection - 1)
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                    self.selection = min(len(self.submissions) - 1, self.selection + 1)
                elif key in ("\n", "\r", curses.KEY_ENTER) and self.submissions:
                    selected = self.selected_submission()
                    assert selected is not None
                    return self.result("open_submission", submission_id=selected["id"])
                elif isinstance(key, str) and key.casefold() == "e":
                    self.screen = "profile"
                    self.form_values = [
                        self.display_name,
                        self.github_username,
                        self.bio,
                    ]
                elif isinstance(key, str) and key.casefold() == "p":
                    return self.result("show_progress")
                elif key == curses.KEY_F3:
                    self.screen = "points"
                elif key in ("\x1b", 27):
                    return self.result("exit")
            elif self.screen == "detail":
                submission = self.selected_submission()
                if key in (curses.KEY_UP, curses.KEY_LEFT):
                    self.scroll = max(0, self.scroll - 1)
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                    self.scroll += 1
                elif isinstance(key, str) and key.casefold() in {"a", "d", "m"}:
                    if submission is None:
                        continue
                    related = self.submission_requests(submission)
                    pending = next(
                        (req for req in related if req.get("status") == "pending"),
                        None,
                    )
                    accepted = next(
                        (req for req in related if req.get("status") == "accepted"),
                        None,
                    )
                    if key.casefold() == "a" and pending:
                        return self.result(
                            "respond_request",
                            request_id=pending["request_id"],
                            decision="accept",
                        )
                    if key.casefold() == "d" and pending:
                        return self.result(
                            "respond_request",
                            request_id=pending["request_id"],
                            decision="decline",
                        )
                    if key.casefold() == "m" and accepted:
                        return self.result("open_room", slot_id=accepted["slot_id"])
                elif key in ("\x1b", 27):
                    return self.result("back")
            elif self.screen == "profile":
                if key in ("\x1b", 27) and not self.form_editing:
                    return self.result("back")
                if self.form_editing:
                    if key in ("\x1b", 27):
                        self.form_editing = False
                    elif key in ("\n", "\r", curses.KEY_ENTER):
                        self.form_values[self.form_selection] = self.form_buffer
                        self.form_editing = False
                    elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                        self.form_buffer = self.form_buffer[:-1]
                    elif isinstance(key, str) and key.isprintable():
                        limit = (80, 39, 500)[self.form_selection]
                        if len(self.form_buffer) < limit:
                            self.form_buffer += key
                elif key in (curses.KEY_UP, curses.KEY_LEFT):
                    self.form_selection = (self.form_selection - 1) % 3
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                    self.form_selection = (self.form_selection + 1) % 3
                elif key in ("\n", "\r", curses.KEY_ENTER):
                    self.form_buffer = self.form_values[self.form_selection]
                    self.form_editing = True
                elif isinstance(key, str) and key.casefold() == "s":
                    if self.form_editing:
                        self.form_values[self.form_selection] = self.form_buffer
                    return self.result(
                        "update_profile",
                        display_name=self.form_values[0],
                        github_username=self.form_values[1],
                        bio=self.form_values[2],
                    )
            elif self.screen == "points":
                if key in (curses.KEY_UP, curses.KEY_LEFT):
                    self.points_scroll = max(0, self.points_scroll - 1)
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                    visible = max(1, min(self.height - 5, len(self.points_history)))
                    self.points_scroll = min(
                        max(0, len(self.points_history) - visible),
                        self.points_scroll + 1,
                    )
                elif key in (curses.KEY_F3, "\x1b", 27):
                    self.screen = "dashboard"
            elif self.screen == "progress":
                if key in (curses.KEY_UP, curses.KEY_LEFT):
                    self.progress_scroll = max(0, self.progress_scroll - 1)
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                    self.progress_scroll += 1
                elif isinstance(key, str) and key.casefold() == "r":
                    if self.pending_review_responses:
                        return self.result(
                            "respond_review",
                            review_id=self.pending_review_responses[0]["review_id"],
                        )
                elif key in ("\x1b", 27):
                    return self.result("back")


def load_config() -> dict[str, Any]:
    config = json.loads(os.environ["ACCOUNT_DASHBOARD_CONFIG"])
    if not isinstance(config, dict):
        raise ValueError("Configuração do Meu espaço inválida.")
    if (
        not isinstance(config.get("username"), str)
        or type(config.get("points")) is not int
        or config["points"] < 0
        or not isinstance(config.get("submissions"), list)
        or not isinstance(config.get("points_history", []), list)
        or not isinstance(config.get("requests", []), list)
        or not isinstance(config.get("notifications", []), list)
        or not isinstance(config.get("review_history", []), list)
        or not isinstance(config.get("pending_review_responses", []), list)
    ):
        raise ValueError("Dados do Meu espaço inválidos.")
    for submission in config["submissions"]:
        if (
            not isinstance(submission, dict)
            or type(submission.get("id")) is not int
            or not isinstance(submission.get("exercise_code"), str)
            or not isinstance(submission.get("exercise_name"), str)
            or submission.get("status") not in STATUS_LABELS
        ):
            raise ValueError("Entrega inválida no Meu espaço.")
    return config


def main() -> int:
    try:
        result = curses.wrapper(AccountDashboardTui(load_config()).run)
        result_fd = int(os.environ.get("ACCOUNT_DASHBOARD_RESULT_FD", "1"))
        with os.fdopen(result_fd, "w", encoding="utf-8", closefd=False) as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        return 0
    except (OSError, ValueError, KeyError, curses.error, json.JSONDecodeError) as error:
        print(f"Não foi possível abrir Meu espaço: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
