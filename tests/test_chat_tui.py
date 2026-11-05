from __future__ import annotations

import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from modules.chat_tui import ChatError, ChatTui, safe_text, wrap_text


class ChatTuiTests(unittest.TestCase):
    @staticmethod
    def message(message_id: int, body: str = "texto") -> dict:
        return {
            "id": message_id,
            "username": "alice",
            "user_id": 7,
            "body": body,
            "created_at": "2026-11-05 03:00:00",
            "pinned_at": None,
            "reactions": "",
            "mentioned": 0,
        }

    def make_tui(self, history_limit: int = 2) -> ChatTui:
        tui = ChatTui(
            token="token",
            user_id=7,
            username="alice",
            is_admin=False,
            socket_path="/tmp/broker.sock",
            channel="global",
            room_name="Chat global",
            auto_refresh=False,
            refresh_seconds=2,
            history_limit=history_limit,
        )
        tui.timezone = ZoneInfo("UTC")
        return tui

    def test_wrap_text_respects_wide_characters(self) -> None:
        self.assertEqual(["ab界", "cd"], wrap_text("ab界cd", 4))

    def test_safe_text_removes_terminal_control_sequences(self) -> None:
        self.assertEqual("oi 😀", safe_text("oi\x1b[2J 😀\x00"))

    def test_incremental_sync_and_history_keep_order_without_dropping_loaded_rows(self) -> None:
        tui = self.make_tui()
        initial = {
            "ok": True,
            "messages": [self.message(2), self.message(3)],
            "window": [self.message(2), self.message(3)],
            "last_id": 3,
            "has_more": True,
            "online_users": ["alice"],
            "typing_users": [],
            "pinned": [],
        }
        new_message = self.message(4, "novo")
        subsequent = {
            **initial,
            "messages": [new_message],
            "window": [self.message(3), new_message],
            "last_id": 4,
        }
        history = {
            "ok": True,
            "messages": [self.message(1, "antigo"), self.message(2, "anterior")],
            "has_more": False,
        }
        fourth = self.message(5, "mais novo")
        latest = {
            **subsequent,
            "messages": [fourth],
            "window": [self.message(4), fourth],
            "last_id": 5,
        }
        with patch(
            "modules.chat_tui.broker_request",
            side_effect=[initial, subsequent, history, latest],
        ):
            tui.sync()
            tui.sync()
            tui.load_history()
            tui.sync()

        self.assertEqual([1, 2, 3, 4, 5], [item["id"] for item in tui.messages])

    def test_rendered_messages_keep_actionable_id_time_and_author(self) -> None:
        tui = self.make_tui()
        tui.width = 80
        rows = tui.message_display_rows(self.message(42, "ola"))
        self.assertEqual("[#42 03:00] você: ola", rows[0][0])

    def test_failed_send_restores_the_draft(self) -> None:
        tui = self.make_tui()
        with patch.object(tui, "request", side_effect=ChatError("sem conexão")):
            tui.send_message(None, "mensagem")
        self.assertEqual("mensagem", tui.input_text)
        self.assertEqual(len("mensagem"), tui.cursor)
        self.assertEqual("sem conexão", tui.status)


if __name__ == "__main__":
    unittest.main()
