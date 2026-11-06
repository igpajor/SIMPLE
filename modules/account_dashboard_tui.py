#!/usr/bin/env python3
"""Unified profile, progress, and submissions dashboard."""

from __future__ import annotations

import curses
import json
import os
import subprocess
import sys
import time
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


def build_notifications(
    stored: list[dict[str, Any]],
    submissions: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    accepted_requests: list[dict[str, Any]],
    chat_rooms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    notifications = [
        {**item, "category": item.get("type")}
        for item in stored
    ]
    submission_by_slot = {
        str(item.get("slot_id")): item
        for item in submissions
        if item.get("slot_id") is not None
    }
    accepted_ids: set[str] = set()
    for request in requests:
        submission = submission_by_slot.get(str(request.get("slot_id")), {})
        if request.get("status") == "accepted":
            notifications.append(
                {
                    **request,
                    "type": "evaluation_request_accepted",
                    "category": "evaluation_request_accepted",
                    "created_at": request.get("responded_at", ""),
                    "other_user": request.get("reviewer", ""),
                    "submission_id": submission.get("id"),
                }
            )
            accepted_ids.add(str(request.get("request_id")))
        elif request.get("status") == "pending":
            notifications.append(
                {
                    **request,
                    "type": "evaluation_request",
                    "category": "evaluation_request",
                    "created_at": request.get("requested_at", ""),
                    "submission_id": submission.get("id"),
                    "other_user": request.get("reviewer", ""),
                }
            )
    for request in accepted_requests:
        if str(request.get("request_id")) in accepted_ids:
            continue
        notifications.append(
            {
                **request,
                "type": "evaluation_accepted",
                "category": "evaluation_accepted",
                "created_at": request.get("accepted_at", ""),
                "other_user": request.get("submitter", ""),
            }
        )
    for room in chat_rooms:
        if room.get("kind") != "direct" and not room.get("mention_count"):
            continue
        preview = room.get("preview") or {}
        notifications.append(
            {
                "type": "chat_message",
                "category": "chat_message",
                "channel": room.get("channel", ""),
                "room_name": room.get("name", ""),
                "unread_count": room.get("unread_count", 0),
                "mention_count": room.get("mention_count", 0),
                "other_user": preview.get("username", ""),
                "feedback": preview.get("body", ""),
                "created_at": preview.get("created_at", ""),
            }
        )
    notifications.sort(
        key=lambda item: (
            str(item.get("created_at", "")),
            str(item.get("type", "")),
        ),
        reverse=True,
    )
    return notifications


class AccountDashboardTui:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.broker_socket = safe_text(config.get("broker_socket", ""))
        self.broker_client = safe_text(config.get("broker_client", ""))
        self.broker_token = safe_text(config.get("broker_token", ""))
        self.username = safe_text(config["username"])
        self.display_name = safe_text(config.get("display_name", ""))
        self.github_username = safe_text(config.get("github_username", ""))
        self.bio = safe_text(config.get("bio", ""))
        self.points = config["points"]
        self.counts = config.get("counts", {})
        self.submissions = config["submissions"]
        self.points_history = config.get("points_history", [])
        self.requests = config.get("requests", [])
        self.evaluation_slots = config.get("evaluation_slots", [])
        self.accepted_evaluation_requests = config.get(
            "accepted_evaluation_requests", []
        )
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
            else "evaluations"
            if config.get("open_evaluations")
            else "dashboard"
        )
        self.evaluation_return_dashboard = bool(
            config.get("evaluation_return_dashboard", False)
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
        self.notification_popup = False
        self.popup_request_id: int | None = None
        self.evaluation_selection = 0
        self.evaluation_scroll = 0
        self.status_message = ""
        self.confirmation: dict[str, Any] | None = None
        self.evaluation_form: dict[str, Any] | None = None
        self.evaluation_form_field = "score"
        self.evaluation_form_buffer = ""
        self.evaluation_form_score = ""
        self.live_refresh_error = ""
        self.last_notification_poll = time.monotonic()
        self.width = 80
        self.height = 24
        self.colors: dict[str, int] = {}
        self.popup_colors: dict[str, int] = {}
        self.popup_background = curses.A_REVERSE
        if any(
            item.get("category", item.get("type")) == "evaluation_request"
            for item in self.notifications
        ):
            self.notification_popup = True
            first_request = next(
                item for item in self.notifications
                if item.get("category", item.get("type")) == "evaluation_request"
            )
            self.popup_request_id = first_request.get("request_id")

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
        popup_palette = (
            ("title", curses.COLOR_CYAN, curses.A_BOLD),
            ("selected", curses.COLOR_CYAN, curses.A_BOLD),
            ("text", curses.COLOR_WHITE, curses.A_NORMAL),
            ("muted", curses.COLOR_WHITE, curses.A_DIM),
            ("pending", curses.COLOR_YELLOW, curses.A_BOLD),
            ("approved", curses.COLOR_GREEN, curses.A_BOLD),
            ("rejected", curses.COLOR_RED, curses.A_BOLD),
            ("warning", curses.COLOR_YELLOW, curses.A_BOLD),
        )
        for index, (name, foreground, attributes) in enumerate(
            popup_palette, start=9
        ):
            try:
                curses.init_pair(index, foreground, curses.COLOR_BLACK)
                self.popup_colors[name] = curses.color_pair(index) | attributes
            except curses.error:
                self.popup_colors[name] = self.colors.get(
                    name, curses.A_NORMAL
                ) | curses.A_REVERSE
        try:
            curses.init_pair(17, curses.COLOR_WHITE, curses.COLOR_BLACK)
            self.popup_background = curses.color_pair(17)
        except curses.error:
            self.popup_background = curses.A_REVERSE

    def draw_text(
        self,
        screen: Any,
        y: int,
        x: int,
        text: str,
        color: str = "text",
        width: int | None = None,
        *,
        opaque: bool = False,
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
                (
                    self.popup_colors.get(color, curses.A_REVERSE)
                    if opaque
                    else self.colors.get(color, curses.A_NORMAL)
                ),
            )
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
        opaque: bool = False,
    ) -> None:
        if width < 4 or height < 3:
            return
        right = left + width - 1
        bottom = top + height - 1
        attr = (
            self.popup_colors.get("muted", curses.A_REVERSE)
            if opaque
            else self.colors.get("muted", curses.A_NORMAL)
        )
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
            self.draw_text(
                screen,
                top,
                left + 2,
                f" {title} ",
                "title",
                width - 4,
                opaque=opaque,
            )

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
            label = self.notification_summary(item)
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
                f"+{remaining} · F2 última",
                "muted",
                width - 2,
            )

    @staticmethod
    def notification_summary(item: dict[str, Any]) -> str:
        notification_type = item.get("category", item.get("type", ""))
        if notification_type == "review_received":
            label = f"{item.get('exercise_code', '')} · Avaliação recebida"
        elif notification_type == "response_received":
            label = f"{item.get('exercise_code', '')} · Resposta de avaliação"
        elif notification_type == "evaluation_request":
            label = f"{item.get('exercise_code', '')} · Pedido de avaliação"
        elif notification_type == "evaluation_request_accepted":
            label = f"{item.get('exercise_code', '')} · Avaliador confirmado"
        elif notification_type == "evaluation_accepted":
            label = f"{item.get('exercise_code', '')} · Pedido aceito"
        elif notification_type == "chat_message":
            room = item.get("room_name", "Chat")
            label = f"{room} · Mensagem nova"
        else:
            label = f"{item.get('exercise_code', '')} · Atualização"
        if item.get("other_user"):
            label += f" · {item['other_user']}"
        return label

    def notification_lines(self, item: dict[str, Any]) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [
            (self.notification_summary(item), "title"),
            (f"Recebida em: {item.get('created_at') or '—'}", "muted"),
        ]
        if item.get("category", item.get("type")) == "chat_message":
            if item.get("feedback"):
                lines.append((f"Mensagem: {item['feedback']}", "text"))
            if item.get("mention_count"):
                lines.append((f"Menções: {item['mention_count']}", "warning"))
            if item.get("unread_count"):
                lines.append((f"Mensagens não lidas: {item['unread_count']}", "muted"))
        else:
            if item.get("score") not in (None, ""):
                lines.append((f"Nota: {item['score']}/5", "selected"))
            if item.get("feedback"):
                lines.append((f"Detalhes: {item['feedback']}", "text"))
        if item.get("category", item.get("type")) == "evaluation_request":
            lines.append(("A: aceitar · D: recusar · Enter: abrir entrega", "selected"))
        elif item.get("category", item.get("type")) == "evaluation_request_accepted":
            lines.append(("Seu avaliador foi confirmado · Enter: abrir entrega", "selected"))
        elif item.get("category", item.get("type")) == "evaluation_accepted":
            lines.extend(
                (
                    ("[1] Abrir sala de áudio e vídeo", "selected"),
                    ("[2] Avaliar submissão", "selected"),
                    ("[3] Liberar minha reserva", "selected"),
                    ("[0] Voltar", "muted"),
                )
            )
        elif item.get("category", item.get("type")) == "chat_message":
            lines.append(("Enter: abrir conversa", "selected"))
        elif item.get("category", item.get("type")) == "response_received":
            lines.append(("P/Enter: abrir histórico de avaliações", "selected"))
        else:
            lines.append(("Enter: abrir entrega", "selected"))
        return lines

    def evaluation_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        known_slots: set[str] = set()
        for slot in self.evaluation_slots:
            slot_id = str(slot.get("id", ""))
            if not slot_id:
                continue
            known_slots.add(slot_id)
            items.append(
                {
                    **slot,
                    "item_type": "reviewer_slot",
                    "label": (
                        f"{slot.get('exercise_code', '')} · {slot.get('username', '')} "
                        f"· {slot.get('exercise_name', '')}"
                    ),
                }
            )
        for request in self.accepted_evaluation_requests:
            slot_id = str(request.get("slot_id", ""))
            if not slot_id or slot_id in known_slots:
                continue
            known_slots.add(slot_id)
            items.append(
                {
                    **request,
                    "id": request.get("slot_id"),
                    "item_type": "reviewer_accepted",
                    "label": (
                        f"Avaliar · {request.get('exercise_code', '')} · "
                        f"{request.get('submitter', '')}"
                    ),
                }
            )
        submission_by_slot = {
            str(submission.get("slot_id")): submission
            for submission in self.submissions
            if submission.get("slot_id") is not None
        }
        for request in self.requests:
            submission = submission_by_slot.get(str(request.get("slot_id")), {})
            request_state = (
                "aguardando sua decisão"
                if request.get("status") == "pending"
                else "avaliador confirmado"
            )
            items.append(
                {
                    **request,
                    "id": request.get("slot_id"),
                    "submission_id": submission.get("id"),
                    "item_type": "author_request",
                    "label": (
                        f"Minha entrega · {request.get('exercise_code', '')} · "
                        f"{request.get('reviewer', '')} · {request_state}"
                    ),
                }
            )
        for response in self.pending_review_responses:
            items.append(
                {
                    **response,
                    "id": response.get("review_id"),
                    "item_type": "review_response",
                    "label": (
                        f"Responder feedback · {response.get('exercise_code', '')} · "
                        f"{response.get('reviewer', '')}"
                    ),
                }
            )
        priority = {
            "reviewer_accepted": 0,
            "author_request": 1,
            "review_response": 2,
            "reviewer_slot": 3,
        }
        items.sort(
            key=lambda item: (
                priority[item["item_type"]],
                0 if item.get("request_status") == "pending" else 1,
                str(item.get("created_at", "")),
                str(item.get("id", "")),
            )
        )
        return items

    def active_notification(self) -> dict[str, Any] | None:
        if self.popup_request_id is not None:
            pending = next(
                (
                    item
                    for item in self.notifications
                    if item.get("request_id") == self.popup_request_id
                    and item.get("category", item.get("type"))
                    == "evaluation_request"
                ),
                None,
            )
            if pending is not None:
                return pending
        return self.notifications[0] if self.notifications else None

    def draw_notification_popup(self, screen: Any) -> None:
        width = min(66, self.width - 4)
        height = min(12, self.height - 4)
        left = max(0, (self.width - width) // 2)
        top = max(1, (self.height - height) // 2)
        for row in range(top, min(top + height, self.height)):
            try:
                screen.addnstr(
                    row,
                    left,
                    " " * width,
                    min(width, self.width - left - 1),
                    self.popup_background,
                )
            except curses.error:
                pass
        self.draw_frame(
            screen,
            top,
            left,
            width,
            height,
            "ÚLTIMA NOTIFICAÇÃO · F2",
            opaque=True,
        )
        if not self.notifications:
            self.draw_text(
                screen,
                top + 2,
                left + 2,
                "Você não tem notificações pendentes.",
                "muted",
                width - 4,
                opaque=True,
            )
        else:
            item = self.active_notification()
            assert item is not None
            content = [
                (wrapped, color)
                for text, color in self.notification_lines(item)
                for wrapped in wrap_text(text, width - 5)
            ]
            visible = height - 3
            for index, (line, color) in enumerate(content[:visible]):
                self.draw_text(
                    screen,
                    top + 1 + index,
                    left + 2,
                    line,
                    color,
                    width - 4,
                    opaque=True,
                )
        self.draw_text(
            screen,
            top + height - 2,
            left + 2,
            "F2/Esc fechar",
            "muted",
            width - 4,
            opaque=True,
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
        footer = self.status_message or (
            "↑/↓ entregas · Enter detalhes · E editar · F2 última notif. · "
            "V avaliações · P progresso · F3 pontos · Esc voltar"
        )
        if self.live_refresh_error:
            footer = f"Atualização falhou: {self.live_refresh_error}"
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
            lines.extend(self.notification_lines(item))
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

    def draw_evaluations(self, screen: Any) -> None:
        panel_width = min(112, self.width - 2)
        left = max(0, (self.width - panel_width) // 2)
        top = 1
        height = self.height - 3
        self.draw_frame(screen, top, left, panel_width, height, "AVALIAÇÕES")
        items = self.evaluation_items()
        if not items:
            self.draw_text(
                screen,
                top + 2,
                left + 2,
                "Não há pedidos, entregas ou respostas de avaliação pendentes.",
                "muted",
                panel_width - 4,
            )
        else:
            visible = max(1, height - 6)
            self.evaluation_selection = min(
                self.evaluation_selection, len(items) - 1
            )
            if self.evaluation_selection < self.evaluation_scroll:
                self.evaluation_scroll = self.evaluation_selection
            elif self.evaluation_selection >= self.evaluation_scroll + visible:
                self.evaluation_scroll = self.evaluation_selection - visible + 1
            for index, item in enumerate(
                items[self.evaluation_scroll : self.evaluation_scroll + visible]
            ):
                selected = self.evaluation_scroll + index == self.evaluation_selection
                label = item["label"]
                if item["item_type"] == "reviewer_slot":
                    if item.get("request_status") == "pending":
                        label = f"Pedido enviado · {label}"
                    else:
                        label = f"Disponível · {label}"
                self.draw_text(
                    screen,
                    top + 1 + index,
                    left + 2,
                    f"{'›' if selected else ' '} {label}",
                    "selected" if selected else "text",
                    panel_width - 4,
                )
            selected = items[self.evaluation_selection]
            if selected["item_type"] == "reviewer_slot":
                hint = (
                    "C cancelar pedido · Esc voltar"
                    if selected.get("request_status") == "pending"
                    else "R solicitar avaliação · Esc voltar"
                )
            elif selected["item_type"] == "reviewer_accepted":
                hint = "1 sala · 2 avaliar entrega · 3 liberar reserva · Esc voltar"
            elif selected["item_type"] == "author_request":
                hint = (
                    "A aceitar · D recusar · Enter abrir entrega · Esc voltar"
                    if selected.get("status") == "pending"
                    else "Enter abrir entrega · Esc voltar"
                )
            else:
                hint = "Enter responder · Esc voltar"
            self.draw_text(
                screen,
                top + height - 2,
                left + 2,
                hint,
                "muted",
                panel_width - 4,
            )
        if self.status_message:
            self.draw_text(
                screen,
                top + height - 3,
                left + 2,
                self.status_message,
                "approved",
                panel_width - 4,
            )
        if self.live_refresh_error:
            self.draw_text(
                screen,
                top + height - 4,
                left + 2,
                self.live_refresh_error,
                "warning",
                panel_width - 4,
            )
        self.draw_text(
            screen,
            self.height - 1,
            left + 1,
            "↑/↓ selecionar · use a ação indicada · Esc voltar",
            "muted",
            panel_width - 2,
        )

    def draw_evaluation_confirmation(self, screen: Any) -> None:
        panel_width = min(72, self.width - 4)
        panel_height = 8
        left = max(0, (self.width - panel_width) // 2)
        top = max(0, (self.height - panel_height) // 2)
        self.draw_frame(
            screen,
            top,
            left,
            panel_width,
            panel_height,
            "CONFIRMAR AÇÃO",
            opaque=True,
        )
        message = safe_text((self.confirmation or {}).get("message", ""))
        for index, line in enumerate(wrap_text(message, panel_width - 6)[:2]):
            self.draw_text(
                screen,
                top + 2 + index,
                left + 3,
                line,
                "text",
                panel_width - 6,
                opaque=True,
            )
        self.draw_text(
            screen,
            top + panel_height - 2,
            left + 3,
            "S confirmar · N/Esc cancelar",
            "selected",
            panel_width - 6,
            opaque=True,
        )

    def draw_evaluation_form(self, screen: Any) -> None:
        form = self.evaluation_form or {}
        panel_width = min(112, self.width - 2)
        left = max(0, (self.width - panel_width) // 2)
        top = 1
        height = self.height - 3
        available_height = height - 2
        if available_height < 18 or panel_width < 30:
            self.draw_text(
                screen,
                top + 2,
                left + 3,
                "Aumente o terminal para preencher a avaliação.",
                "warning",
                panel_width - 6,
            )
            self.draw_text(
                screen,
                self.height - 1,
                left + 1,
                "Esc cancelar",
                "muted",
                panel_width - 2,
            )
            return
        self.draw_frame(
            screen,
            top,
            left,
            panel_width,
            height,
            safe_text(form.get("title", "AVALIAÇÃO")),
        )

        inner_left = left + 1
        inner_width = panel_width - 2
        row = top + 1

        def draw_pair(
            y: int,
            section_height: int,
            first_title: str,
            first_value: str,
            second_title: str,
            second_value: str,
            first_ratio: float = 0.5,
        ) -> None:
            gap = 1
            available_width = inner_width - gap
            box_width = int(available_width * first_ratio)
            second_width = available_width - box_width
            second_left = inner_left + box_width + gap
            self.draw_frame(
                screen, y, inner_left, box_width, section_height, first_title
            )
            self.draw_frame(
                screen, y, second_left, second_width, section_height, second_title
            )
            for index, line in enumerate(
                wrap_text(first_value, box_width - 4)[: section_height - 2]
            ):
                self.draw_text(
                    screen,
                    y + 1 + index,
                    inner_left + 2,
                    line,
                    "text",
                    box_width - 4,
                )
            for index, line in enumerate(
                wrap_text(second_value, second_width - 4)[: section_height - 2]
            ):
                self.draw_text(
                    screen,
                    y + 1 + index,
                    second_left + 2,
                    line,
                    "text",
                    second_width - 4,
                )

        mode = form.get("mode")
        exercise = safe_text(form.get("exercise", ""))
        counterpart_title = "AVALIADO" if mode == "review" else "AVALIADOR"
        counterpart = safe_text(
            form.get("reviewee" if mode == "review" else "reviewer", "")
        )
        draw_pair(
            row,
            3,
            "EXERCÍCIO",
            exercise or "—",
            counterpart_title,
            counterpart or "—",
        )
        row += 3

        content_height = (
            5
            if available_height < 22
            else min(8, max(6, available_height // 3))
        )
        content_title = (
            "DESCRIÇÃO E ENUNCIADO"
            if mode == "review"
            else "AVALIAÇÃO RECEBIDA"
        )
        self.draw_frame(
            screen, row, inner_left, inner_width, content_height, content_title
        )
        divider_y = row + content_height // 2
        try:
            screen.hline(
                divider_y,
                inner_left + 1,
                curses.ACS_HLINE,
                inner_width - 2,
                self.colors.get("muted", curses.A_NORMAL),
            )
        except curses.error:
            pass

        if mode == "review":
            first_label = "Descrição"
            first_value = safe_text(form.get("description", ""))
            second_label = "Enunciado"
            second_value = safe_text(form.get("statement", ""))
        else:
            first_label = "Nota recebida"
            first_value = safe_text(form.get("received_score", ""))
            second_label = "Feedback recebido"
            second_value = safe_text(form.get("received_feedback", ""))

        def draw_content(
            start_y: int, end_y: int, label: str, value: str
        ) -> None:
            if start_y > end_y:
                return
            x = inner_left + 2
            text_width = inner_width - 4
            value_lines = wrap_text(value or "—", max(1, text_width - len(label) - 2))
            available_rows = end_y - start_y + 1
            is_truncated = len(value_lines) > available_rows
            value_lines = value_lines[:available_rows]
            first_line = f"{label}: {value_lines[0]}"
            if is_truncated and available_rows == 1:
                first_line = first_line[: max(0, text_width - 1)] + "…"
            self.draw_text(
                screen,
                start_y,
                x,
                first_line,
                "text",
                text_width,
            )
            for offset, line in enumerate(value_lines[1:], start=1):
                if start_y + offset > end_y:
                    break
                if is_truncated and offset == available_rows - 1:
                    line = line[: max(0, text_width - 1)] + "…"
                self.draw_text(
                    screen, start_y + offset, x, line, "text", text_width
                )

        draw_content(row + 1, divider_y - 1, first_label, first_value)
        self.draw_text(
            screen,
            divider_y,
            inner_left + 2,
            f" {second_label.upper()} ",
            "title",
            inner_width - 4,
        )
        draw_content(
            divider_y + 1,
            row + content_height - 2,
            second_label,
            second_value,
        )
        row += content_height

        if mode == "review":
            commit_value = safe_text(form.get("commit_url", ""))
            if not commit_value:
                commit_value = safe_text(form.get("content", "")) or "—"
                commit_title = "CONTEÚDO"
            else:
                commit_title = "COMMIT"
            directory_value = safe_text(form.get("delivery_directory", "")) or "—"
            draw_pair(
                row,
                4,
                commit_title,
                commit_value,
                "DIRETÓRIO",
                directory_value,
                first_ratio=0.66,
            )
            row += 4

        score_height = 3
        feedback_height = top + height - 1 - row
        self.draw_frame(
            screen,
            row,
            inner_left,
            inner_width,
            score_height,
            (
                "NOTA (0-5) · ATIVA"
                if self.evaluation_form_field == "score"
                else "NOTA (0-5)"
            ),
        )
        self.draw_text(
            screen,
            row + 1,
            inner_left + 2,
            self.evaluation_form_score or "Digite uma nota de 0 a 5",
            "selected",
            inner_width - 4,
        )
        row += score_height
        feedback_title = (
            "FEEDBACK DETALHADO · ATIVO"
            if self.evaluation_form_field == "feedback"
            else "FEEDBACK DETALHADO"
        )
        self.draw_frame(
            screen, row, inner_left, inner_width, feedback_height, feedback_title
        )
        feedback_lines = wrap_text(
            self.evaluation_form_buffer or "Escreva aqui um feedback detalhado.",
            inner_width - 4,
        )
        feedback_rows = max(1, feedback_height - 2)
        visible_feedback = feedback_lines[-feedback_rows:]
        for index, line in enumerate(visible_feedback):
            self.draw_text(
                screen,
                row + 1 + index,
                inner_left + 2,
                line,
                "text",
                inner_width - 4,
            )
        if self.live_refresh_error:
            self.draw_text(
                screen,
                self.height - 2,
                left + 1,
                self.live_refresh_error,
                "warning",
                panel_width - 2,
            )
        instructions = (
            "Nota 0-5 · Enter/Tab ir ao feedback · feedback multilinha · "
            "Ctrl+S enviar · Esc cancelar"
            if self.evaluation_form_field == "score"
            else "Feedback multilinha · Tab ir à nota · Ctrl+S enviar · Esc cancelar"
        )
        self.draw_text(
            screen,
            self.height - 1,
            left + 1,
            instructions,
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
        elif self.screen == "evaluations":
            self.draw_evaluations(screen)
        elif self.screen == "evaluation_confirm":
            return_screen = (self.confirmation or {}).get("return_screen")
            if return_screen == "dashboard":
                self.draw_dashboard(screen)
                if self.notification_popup:
                    self.draw_notification_popup(screen)
            elif return_screen == "detail":
                self.draw_detail(screen)
            else:
                self.draw_evaluations(screen)
            self.draw_evaluation_confirmation(screen)
        elif self.screen == "evaluation_form":
            self.draw_evaluation_form(screen)
        if self.notification_popup and self.screen == "dashboard":
            self.draw_notification_popup(screen)
        screen.refresh()

    def result(self, action: str, **values: Any) -> dict[str, Any]:
        return {"action": action, **values}

    def broker_request(self, method: str, **fields: str) -> dict[str, Any]:
        if not self.broker_socket or not self.broker_client or not self.broker_token:
            raise RuntimeError(
                "Configuração de atualização do Broker ausente "
                "(socket, cliente ou sessão)."
            )
        field_names = list(fields)
        completed = subprocess.run(
            [
                sys.executable,
                self.broker_client,
                self.broker_socket,
                method,
                "token",
                *field_names,
            ],
            input=(
                "\0".join(
                    [self.broker_token, *(str(fields[name]) for name in field_names)]
                )
                + "\0"
            ).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
            check=False,
        )
        if completed.returncode:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(error or f"Broker recusou {method}.")
        response = json.loads(completed.stdout.decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise RuntimeError(
                str(response.get("error", "Resposta inválida do Broker."))
                if isinstance(response, dict)
                else "Resposta inválida do Broker."
            )
        return response

    def refresh_notifications(self) -> None:
        progress = self.broker_request("progress.get")
        submissions = self.broker_request("submissions.list").get("submissions")
        requests = self.broker_request("evaluations.requests.list").get("requests")
        chat = self.broker_request("chat.dashboard")
        accepted_requests = progress.get("accepted_evaluation_requests", [])
        review_history = progress.get("review_history", [])
        pending_review_responses = progress.get("pending_review_responses", [])
        if not all(
            isinstance(value, list)
            for value in (
                submissions,
                requests,
                progress.get("notifications"),
                accepted_requests,
                review_history,
                pending_review_responses,
                chat.get("unread_rooms"),
            )
        ):
            raise RuntimeError("O Broker retornou listas inválidas de notificações.")
        fresh = build_notifications(
            progress["notifications"],
            submissions,
            requests,
            accepted_requests,
            chat["unread_rooms"],
        )
        old_request_ids = {
            item.get("request_id")
            for item in self.notifications
            if item.get("category", item.get("type")) == "evaluation_request"
        }
        new_requests = [
            item
            for item in fresh
            if item.get("category", item.get("type")) == "evaluation_request"
            and item.get("request_id") not in old_request_ids
        ]
        self.notifications = fresh
        self.requests = requests
        self.submissions = submissions
        self.accepted_evaluation_requests = accepted_requests
        self.review_history = review_history
        self.pending_review_responses = pending_review_responses
        self.points_history = progress.get("points_history", self.points_history)
        if new_requests:
            self.notification_popup = True
            self.popup_request_id = new_requests[0].get("request_id")
        self.live_refresh_error = ""
        if self.screen == "evaluations":
            slots = self.broker_request("evaluations.slots.list").get("slots")
            if not isinstance(slots, list):
                raise RuntimeError("O Broker retornou uma lista inválida de avaliações.")
            self.evaluation_slots = slots
            self.evaluation_selection = min(
                self.evaluation_selection,
                max(0, len(self.evaluation_items()) - 1),
            )

    def poll_notifications(self) -> None:
        now = time.monotonic()
        if now - self.last_notification_poll < 2:
            return
        self.last_notification_poll = now
        try:
            self.refresh_notifications()
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            self.live_refresh_error = safe_text(error)

    def show_evaluations(self, *, return_dashboard: bool = True) -> None:
        self.screen = "evaluations"
        self.evaluation_return_dashboard = return_dashboard
        self.status_message = ""
        try:
            self.refresh_notifications()
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            self.live_refresh_error = safe_text(error)

    def open_submission(self, submission_id: int) -> None:
        try:
            response = self.broker_request(
                "submissions.get", submission_id=str(submission_id)
            )
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            self.live_refresh_error = safe_text(error)
            return
        detail = response.get("submission")
        if not isinstance(detail, dict):
            self.live_refresh_error = "O Broker retornou uma entrega inválida."
            return
        self.detail = detail
        self.selected_id = submission_id
        self.screen = "detail"
        self.scroll = 0
        self.notification_popup = False

    def request_confirmation(
        self,
        method: str,
        fields: dict[str, str],
        message: str,
        return_screen: str,
    ) -> None:
        self.confirmation = {
            "method": method,
            "fields": fields,
            "message": message,
            "return_screen": return_screen,
        }
        self.screen = "evaluation_confirm"

    def complete_confirmation(self, accepted: bool) -> None:
        confirmation = self.confirmation
        if confirmation is None:
            self.screen = "evaluations"
            return
        self.confirmation = None
        self.screen = confirmation["return_screen"]
        if not accepted:
            return
        try:
            response = self.broker_request(
                confirmation["method"], **confirmation["fields"]
            )
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            self.live_refresh_error = safe_text(error)
            return
        method = confirmation["method"]
        if method == "evaluations.accept":
            self.status_message = "Pedido enviado ao autor para autorização."
        elif method == "evaluations.cancel":
            self.status_message = "Pedido/reserva liberado."
        elif method == "evaluations.request.respond":
            decision = confirmation["fields"]["decision"]
            self.status_message = (
                "Pedido aceito; a avaliação foi confirmada."
                if decision == "accept"
                else "Pedido recusado; a reserva foi liberada."
            )
        else:
            self.status_message = safe_text(response.get("message", "Ação concluída."))
        try:
            self.refresh_notifications()
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            self.live_refresh_error = safe_text(error)

    def start_evaluation_form(self, item: dict[str, Any]) -> None:
        item_type = item.get("item_type")
        try:
            if item_type == "reviewer_accepted":
                response = self.broker_request(
                    "evaluations.slot.get", slot_id=str(item["slot_id"])
                )
                slot = response.get("slot")
                if not isinstance(slot, dict):
                    raise RuntimeError("O Broker retornou uma entrega inválida.")
                self.evaluation_form = {
                    "mode": "review",
                    "return_screen": item.get("return_screen", "evaluations"),
                    "title": f"AVALIAR · {slot.get('exercise_code', '')}",
                    "exercise": (
                        f"{slot.get('exercise_code', '')} — "
                        f"{slot.get('exercise_name', '')}"
                    ),
                    "reviewee": slot.get("username", ""),
                    "description": slot.get("description", ""),
                    "statement": slot.get("statement", ""),
                    "commit_url": slot.get("commit_url", ""),
                    "delivery_directory": slot.get("delivery_directory", ""),
                    "content": slot.get("content", ""),
                    "slot_id": str(item["slot_id"]),
                }
            elif item_type == "review_response":
                response = self.broker_request(
                    "evaluations.review.get", review_id=str(item["review_id"])
                )
                review = response.get("review")
                if not isinstance(review, dict):
                    raise RuntimeError("O Broker retornou uma avaliação inválida.")
                self.evaluation_form = {
                    "mode": "response",
                    "return_screen": item.get("return_screen", "evaluations"),
                    "title": f"RESPONDER · {review.get('exercise_code', '')}",
                    "exercise": review.get("exercise_code", ""),
                    "reviewer": review.get("reviewer", ""),
                    "received_score": f"{review.get('score', '')}/5",
                    "received_feedback": review.get("feedback", ""),
                    "review_id": str(item["review_id"]),
                }
            else:
                return
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            self.live_refresh_error = safe_text(error)
            return
        self.evaluation_form_field = "score"
        self.evaluation_form_buffer = ""
        self.evaluation_form_score = ""
        self.screen = "evaluation_form"

    def submit_evaluation_form(self) -> None:
        form = self.evaluation_form
        feedback = self.evaluation_form_buffer
        if form is None or not feedback.strip():
            self.live_refresh_error = "O feedback obrigatório não pode ficar vazio."
            return
        if not self.evaluation_form_score.isdigit() or not (
            0 <= int(self.evaluation_form_score) <= 5
        ):
            self.live_refresh_error = "A nota deve ser um inteiro entre 0 e 5."
            self.evaluation_form_field = "score"
            return
        if form["mode"] == "review":
            method = "evaluations.review.submit"
            fields = {
                "slot_id": form["slot_id"],
                "score": self.evaluation_form_score,
                "feedback": feedback,
            }
        else:
            method = "evaluations.review.respond"
            fields = {
                "review_id": form["review_id"],
                "score": self.evaluation_form_score,
                "feedback": feedback,
            }
        try:
            response = self.broker_request(method, **fields)
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            self.live_refresh_error = safe_text(error)
            return
        if form["mode"] == "review":
            status = response.get("status")
            self.status_message = (
                f"Avaliação registrada: {'aprovado' if status == 'approved' else 'reprovado'} · "
                f"saldo {response.get('points', '—')} ponto(s)."
            )
        else:
            self.status_message = "Resposta registrada; o avaliador foi notificado."
        self.evaluation_form = None
        self.screen = form.get("return_screen", "evaluations")
        self.live_refresh_error = ""
        try:
            self.refresh_notifications()
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            self.live_refresh_error = safe_text(error)

    def run(self, screen: Any) -> dict[str, Any]:
        curses.cbreak()
        curses.noecho()
        screen.keypad(True)
        screen.timeout(500)
        self.initialize_colors()
        if self.screen == "evaluations":
            self.show_evaluations(
                return_dashboard=self.evaluation_return_dashboard
            )
        while True:
            self.draw(screen)
            try:
                key = screen.get_wch()
            except curses.error:
                self.poll_notifications()
                continue
            self.poll_notifications()
            if key == curses.KEY_RESIZE:
                continue
            if self.notification_popup and self.screen == "dashboard":
                if key in (curses.KEY_F2, "\x1b", 27):
                    self.notification_popup = False
                    self.popup_request_id = None
                elif not self.notifications:
                    self.notification_popup = False
                else:
                    item = self.active_notification()
                    if item is None:
                        self.notification_popup = False
                        continue
                    notification_type = item.get("category", item.get("type"))
                    if notification_type == "evaluation_request":
                        if isinstance(key, str) and key.casefold() in {"a", "d"}:
                            decision = "accept" if key.casefold() == "a" else "decline"
                            self.request_confirmation(
                                "evaluations.request.respond",
                                {
                                    "request_id": str(item["request_id"]),
                                    "decision": decision,
                                },
                                "Aceitar este avaliador?"
                                if decision == "accept"
                                else "Recusar este pedido de avaliação?",
                                "dashboard",
                            )
                        elif key in ("\n", "\r", curses.KEY_ENTER):
                            submission_id = item.get("submission_id")
                            if submission_id is not None:
                                self.open_submission(submission_id)
                    elif notification_type == "evaluation_request_accepted":
                        if key in ("\n", "\r", curses.KEY_ENTER):
                            submission_id = item.get("submission_id")
                            if submission_id is not None:
                                self.open_submission(submission_id)
                    elif notification_type == "evaluation_accepted":
                        if key in ("1",):
                            return self.result(
                                "open_room", slot_id=item["slot_id"]
                            )
                        elif key in ("2",):
                            self.start_evaluation_form(
                                {
                                    **item,
                                    "item_type": "reviewer_accepted",
                                    "return_screen": "dashboard",
                                }
                            )
                        elif key in ("3",):
                            self.request_confirmation(
                                "evaluations.cancel",
                                {"slot_id": str(item["slot_id"])},
                                "Liberar sua reserva desta avaliação?",
                                "dashboard",
                            )
                        elif key in ("0", "\n", "\r", curses.KEY_ENTER):
                            self.notification_popup = False
                            self.popup_request_id = None
                    elif notification_type == "chat_message":
                        if key in ("\n", "\r", curses.KEY_ENTER):
                            return self.result(
                                "open_notification_room",
                                channel=item["channel"],
                                room_name=item["room_name"],
                            )
                    elif notification_type == "response_received":
                        if (
                            key in ("\n", "\r", curses.KEY_ENTER)
                            or isinstance(key, str)
                            and key.casefold() == "p"
                        ):
                            self.screen = "progress"
                            self.notification_popup = False
                            self.popup_request_id = None
                    elif notification_type == "review_received":
                        if key in ("\n", "\r", curses.KEY_ENTER):
                            submission_id = item.get("submission_id")
                            if submission_id is not None:
                                self.open_submission(submission_id)
                    if key in ("\n", "\r", curses.KEY_ENTER):
                        self.notification_popup = False
                continue
            if self.screen == "dashboard":
                if key in (curses.KEY_UP, curses.KEY_LEFT):
                    self.selection = max(0, self.selection - 1)
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                    self.selection = min(len(self.submissions) - 1, self.selection + 1)
                elif key in ("\n", "\r", curses.KEY_ENTER) and self.submissions:
                    selected = self.selected_submission()
                    assert selected is not None
                    self.open_submission(selected["id"])
                elif isinstance(key, str) and key.casefold() == "e":
                    self.screen = "profile"
                    self.form_values = [
                        self.display_name,
                        self.github_username,
                        self.bio,
                    ]
                elif isinstance(key, str) and key.casefold() == "p":
                    self.screen = "progress"
                elif isinstance(key, str) and key.casefold() == "v":
                    self.show_evaluations(return_dashboard=True)
                elif key == curses.KEY_F3:
                    self.screen = "points"
                elif key == curses.KEY_F2:
                    self.notification_popup = True
                    self.popup_request_id = None
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
                        self.request_confirmation(
                            "evaluations.request.respond",
                            {
                                "request_id": str(pending["request_id"]),
                                "decision": "accept",
                            },
                            "Aceitar este avaliador?",
                            "detail",
                        )
                    if key.casefold() == "d" and pending:
                        self.request_confirmation(
                            "evaluations.request.respond",
                            {
                                "request_id": str(pending["request_id"]),
                                "decision": "decline",
                            },
                            "Recusar este pedido de avaliação?",
                            "detail",
                        )
                    if key.casefold() == "m" and accepted:
                        return self.result("open_room", slot_id=accepted["slot_id"])
                elif key in ("\x1b", 27):
                    self.detail = None
                    self.screen = "dashboard"
            elif self.screen == "evaluations":
                items = self.evaluation_items()
                selected = (
                    items[self.evaluation_selection]
                    if items and self.evaluation_selection < len(items)
                    else None
                )
                if key in (curses.KEY_UP, curses.KEY_LEFT) and items:
                    self.evaluation_selection = max(0, self.evaluation_selection - 1)
                    self.status_message = ""
                elif key in (curses.KEY_DOWN, curses.KEY_RIGHT) and items:
                    self.evaluation_selection = min(
                        len(items) - 1, self.evaluation_selection + 1
                    )
                    self.status_message = ""
                elif isinstance(key, str) and key.casefold() == "r" and selected:
                    if selected["item_type"] == "reviewer_slot" and not selected.get(
                        "request_status"
                    ):
                        self.request_confirmation(
                            "evaluations.accept",
                            {"slot_id": str(selected["id"])},
                            "Solicitar autorização para avaliar esta entrega?",
                            "evaluations",
                        )
                elif isinstance(key, str) and key.casefold() == "c" and selected:
                    if (
                        selected["item_type"] == "reviewer_slot"
                        and selected.get("request_status") == "pending"
                    ):
                        self.request_confirmation(
                            "evaluations.cancel",
                            {"slot_id": str(selected["id"])},
                            "Cancelar seu pedido para esta avaliação?",
                            "evaluations",
                        )
                elif isinstance(key, str) and key.casefold() in {"a", "d"} and selected:
                    if (
                        selected["item_type"] == "author_request"
                        and selected.get("status") == "pending"
                    ):
                        decision = "accept" if key.casefold() == "a" else "decline"
                        self.request_confirmation(
                            "evaluations.request.respond",
                            {
                                "request_id": str(selected["request_id"]),
                                "decision": decision,
                            },
                            "Aceitar este avaliador?"
                            if decision == "accept"
                            else "Recusar este pedido de avaliação?",
                            "evaluations",
                        )
                elif key == "1" and selected:
                    if selected["item_type"] == "reviewer_accepted":
                        return self.result(
                            "open_room",
                            slot_id=selected["slot_id"],
                            resume_evaluations=True,
                            evaluation_return_dashboard=(
                                self.evaluation_return_dashboard
                            ),
                        )
                elif key == "2" and selected:
                    if selected["item_type"] == "reviewer_accepted":
                        self.start_evaluation_form(selected)
                elif key == "3" and selected:
                    if selected["item_type"] == "reviewer_accepted":
                        self.request_confirmation(
                            "evaluations.cancel",
                            {"slot_id": str(selected["slot_id"])},
                            "Liberar sua reserva desta avaliação?",
                            "evaluations",
                        )
                elif key in ("\n", "\r", curses.KEY_ENTER) and selected:
                    if selected["item_type"] == "author_request":
                        submission_id = selected.get("submission_id")
                        if submission_id is not None:
                            self.open_submission(submission_id)
                    elif selected["item_type"] == "review_response":
                        self.start_evaluation_form(selected)
                elif key in ("\x1b", 27):
                    self.status_message = ""
                    if self.evaluation_return_dashboard:
                        self.screen = "dashboard"
                    elif self.config.get("open_evaluations"):
                        return self.result("exit")
                    self.screen = "dashboard"
            elif self.screen == "evaluation_confirm":
                if isinstance(key, str) and key.casefold() == "s":
                    self.complete_confirmation(True)
                elif (
                    isinstance(key, str) and key.casefold() == "n"
                ) or key in ("\x1b", 27):
                    self.complete_confirmation(False)
            elif self.screen == "evaluation_form":
                if key in ("\x1b", 27):
                    return_screen = (self.evaluation_form or {}).get(
                        "return_screen", "evaluations"
                    )
                    self.evaluation_form = None
                    self.screen = return_screen
                elif key == "\x13":
                    self.submit_evaluation_form()
                elif key in ("\t", curses.KEY_BTAB):
                    self.evaluation_form_field = (
                        "feedback"
                        if self.evaluation_form_field == "score"
                        else "score"
                    )
                elif self.evaluation_form_field == "score":
                    if isinstance(key, str) and key in "012345":
                        self.evaluation_form_score = key
                    elif key in ("\n", "\r", curses.KEY_ENTER):
                        if self.evaluation_form_score:
                            self.evaluation_form_field = "feedback"
                            self.live_refresh_error = ""
                    elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                        self.evaluation_form_score = ""
                elif isinstance(key, str) and key.isprintable():
                    if len(self.evaluation_form_buffer) < 2000:
                        self.evaluation_form_buffer += key
                elif key in ("\n", "\r", curses.KEY_ENTER):
                    if len(self.evaluation_form_buffer) < 2000:
                        self.evaluation_form_buffer += "\n"
                elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                    self.evaluation_form_buffer = self.evaluation_form_buffer[:-1]
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
                        self.start_evaluation_form(
                            {
                                **self.pending_review_responses[0],
                                "item_type": "review_response",
                                "return_screen": "progress",
                            }
                        )
                elif key in ("\x1b", 27):
                    try:
                        self.broker_request("notifications.mark_read")
                    except (
                        OSError,
                        RuntimeError,
                        subprocess.TimeoutExpired,
                        json.JSONDecodeError,
                    ) as error:
                        self.live_refresh_error = safe_text(error)
                    self.screen = "dashboard"


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
        or not isinstance(config.get("evaluation_slots", []), list)
        or not isinstance(config.get("accepted_evaluation_requests", []), list)
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
