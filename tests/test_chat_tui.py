from __future__ import annotations

import curses
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

    def test_dm_return_preserves_parent_draft_history_and_scroll(self) -> None:
        tui = self.make_tui()
        tui.input_text = "rascunho global"
        tui.cursor = len(tui.input_text)
        tui.messages = [self.message(3, "mensagem global")]
        tui.scroll_offset = 2

        with patch.object(tui, "sync"):
            tui.open_dm("dm:7:8", "DM · bob")
            tui.input_text = "rascunho privado"
            tui.cursor = len(tui.input_text)
            tui.return_to_parent()

        self.assertEqual("global", tui.channel)
        self.assertEqual("rascunho global", tui.input_text)
        self.assertEqual(2, tui.scroll_offset)
        self.assertEqual([3], [message["id"] for message in tui.messages])
        self.assertEqual("rascunho privado", tui.dm_states["dm:7:8"]["input_text"])

    def test_dm_notification_includes_command_to_open_conversation(self) -> None:
        tui = self.make_tui()
        tui.unread_rooms = [
            {
                "kind": "room",
                "channel": "global",
                "name": "Chat global",
                "unread_count": 1,
                "preview": {"body": "aviso público"},
            },
            {
                "kind": "direct",
                "channel": "dm:7:8",
                "peer_username": "bob",
                "preview": {"body": "olá"},
            },
        ]
        self.assertEqual(
            "Nova DM de bob: olá · F2 ou /dm bob",
            tui.notification_hint(),
        )

    def test_f2_opens_unread_dm_without_replacing_the_draft(self) -> None:
        tui = self.make_tui()
        tui.input_text = "rascunho global"
        tui.cursor = len(tui.input_text)
        tui.unread_rooms = [
            {
                "kind": "direct",
                "channel": "dm:7:8",
                "name": "DM · bob",
            }
        ]
        with patch.object(tui, "sync"):
            self.assertTrue(tui.handle_key(None, curses.KEY_F2))
        self.assertEqual("dm:7:8", tui.channel)
        self.assertEqual("rascunho global", tui.dm_parent_state["input_text"])
        self.assertEqual("", tui.input_text)

    def test_escape_returns_from_dm_and_exits_global_chat(self) -> None:
        tui = self.make_tui()
        with patch.object(tui, "return_to_parent") as return_to_parent:
            tui.dm_parent_state = tui.capture_room_state()
            self.assertTrue(tui.handle_key(None, "\x1b"))
            return_to_parent.assert_called_once_with()
        tui.dm_parent_state = None
        self.assertFalse(tui.handle_key(None, "\x1b"))


if __name__ == "__main__":
    unittest.main()
