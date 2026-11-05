#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import pty
import select
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import termios
import fcntl
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "broker"))

import simple_broker
from simple_broker import (
    Broker,
    BrokerServer,
    backup_database,
    check_database_integrity,
    initialize_database,
    validate_database,
)


class BrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "simple.db"
        self.socket_path = self.root / "broker.sock"
        initialize_database(self.database_path, ROOT_DIR / "database" / "schema.sql")
        self.broker = Broker(self.database_path)
        self.server = BrokerServer(self.socket_path, self.broker)
        os.chmod(self.socket_path, 0o600)
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.socket_path.unlink(missing_ok=True)
        self.temporary_directory.cleanup()

    def call(self, request: dict[str, object]) -> dict[str, object]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(self.socket_path))
            connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
            return json.loads(connection.makefile("rb").readline())

    def create_user(self, username: str = "student") -> dict[str, object]:
        return self.call(
            {"method": "auth.register", "username": username, "password": "secure-pass-123"}
        )

    def test_registration_login_and_scoped_profile(self) -> None:
        registered = self.create_user()
        self.assertTrue(registered["ok"])
        self.assertFalse(registered["is_admin"])
        with closing(sqlite3.connect(self.database_path)) as connection:
            balance = connection.execute(
                "SELECT SUM(amount) FROM points_ledger WHERE user_id = ?",
                (registered["user_id"],),
            ).fetchone()[0]
        self.assertEqual(1, balance)

        rejected = self.call(
            {"method": "auth.login", "username": "student", "password": "incorrect"}
        )
        self.assertEqual("Usuário ou senha inválidos.", rejected["error"])

        login = self.call(
            {"method": "auth.login", "username": "STUDENT", "password": "secure-pass-123"}
        )
        self.assertTrue(login["ok"])
        profile = self.call({"method": "profile.get", "token": login["token"]})
        self.assertEqual("student", profile["username"])
        self.assertEqual(1, profile["points"])

    def test_profile_validation_and_catalog_are_allowlisted(self) -> None:
        registered = self.create_user()
        token = registered["token"]
        updated = self.call(
            {
                "method": "profile.update",
                "token": token,
                "display_name": "Estudante",
                "github_username": "student-gh",
                "bio": "Linha um\nLinha dois",
            }
        )
        self.assertTrue(updated["ok"])
        profile = self.call({"method": "profile.get", "token": token})
        self.assertEqual("student-gh", profile["github_username"])
        self.assertEqual("Linha um\nLinha dois", profile["bio"])

        invalid = self.call(
            {
                "method": "profile.update",
                "token": token,
                "display_name": "",
                "github_username": "invalid name",
                "bio": "",
            }
        )
        self.assertFalse(invalid["ok"])
        self.assertIn("usuário GitHub válido", invalid["error"])

        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO exercises (code, name, description) VALUES (?, ?, ?)",
                ("C-01", "Exercício de catálogo", "Somente teste"),
            )
        catalog = self.call({"method": "catalog.list", "token": token})
        catalog_by_code = {item["code"]: item["name"] for item in catalog["exercises"]}
        self.assertEqual("Exercício de catálogo", catalog_by_code["C-01"])
        self.assertEqual(
            {f"SHELL00-EX{index:02d}" for index in range(10)},
            {code for code in catalog_by_code if code.startswith("SHELL00-")},
        )
        exercise = self.call(
            {
                "method": "catalog.get",
                "token": token,
                "exercise_code": "SHELL00-EX02",
            }
        )
        self.assertIn("Crie todos esses arquivos e diretórios", exercise["exercise"]["statement"])
        self.assertEqual("ex02/", exercise["exercise"]["delivery_directory"])

    def test_chat_validation_history_rate_limit_and_presence(self) -> None:
        first_user = self.create_user("chat-first")
        second_user = self.create_user("chat-second")
        missing_token = self.call({"method": "chat.status"})
        self.assertFalse(missing_token["ok"])
        self.assertIn("Campos inválidos", missing_token["error"])
        invalid_cursor = self.call(
            {
                "method": "chat.messages",
                "token": first_user["token"],
                "after_id": "-1",
            }
        )
        self.assertIn("ID da última mensagem inválido", invalid_cursor["error"])

        status = self.call({"method": "chat.status", "token": first_user["token"]})
        self.assertEqual(2, status["online_count"])
        self.assertEqual(["chat-first", "chat-second"], status["online_users"])

        empty_history = self.call(
            {
                "method": "chat.messages",
                "token": first_user["token"],
                "after_id": "0",
            }
        )
        self.assertEqual([], empty_history["messages"])
        self.assertEqual(0, empty_history["last_id"])

        for invalid_message in ("", " \t ", "linha\nquebrada", "x" * 501, "a\x85b"):
            rejected = self.call(
                {
                    "method": "chat.send",
                    "token": first_user["token"],
                    "message": invalid_message,
                }
            )
            self.assertFalse(rejected["ok"], invalid_message)

        first_sent = self.call(
            {
                "method": "chat.send",
                "token": first_user["token"],
                "message": "  Olá, grupo!  ",
            }
        )
        self.assertTrue(first_sent["ok"], first_sent)
        limited = self.call(
            {
                "method": "chat.send",
                "token": first_user["token"],
                "message": "Outra mensagem",
            }
        )
        self.assertIn("Aguarde", limited["error"])

        with patch.object(simple_broker, "CHAT_SEND_INTERVAL_SECONDS", 0.0):
            for index in range(51):
                sent = self.call(
                    {
                        "method": "chat.send",
                        "token": second_user["token"],
                        "message": f"histórico-{index}",
                    }
                )
                self.assertTrue(sent["ok"], sent)

        latest = self.call(
            {
                "method": "chat.messages",
                "token": first_user["token"],
                "after_id": "0",
            }
        )
        self.assertEqual(50, len(latest["messages"]))
        self.assertEqual("histórico-1", latest["messages"][0]["body"])
        self.assertEqual("histórico-50", latest["messages"][-1]["body"])
        page = self.call(
            {
                "method": "chat.messages",
                "token": first_user["token"],
                "after_id": str(first_sent["message_id"]),
            }
        )
        self.assertEqual(50, len(page["messages"]))
        self.assertEqual("histórico-0", page["messages"][0]["body"])
        self.assertEqual("histórico-49", page["messages"][-1]["body"])
        final_page = self.call(
            {
                "method": "chat.messages",
                "token": first_user["token"],
                "after_id": str(page["last_id"]),
            }
        )
        self.assertEqual(["histórico-50"], [item["body"] for item in final_page["messages"]])
        older_history = self.call(
            {
                "method": "chat.history",
                "token": first_user["token"],
                "channel": "global",
                "before_id": str(latest["messages"][0]["id"]),
            }
        )
        self.assertEqual(
            ["Olá, grupo!", "histórico-0"],
            [item["body"] for item in older_history["messages"]],
        )
        self.assertFalse(older_history["has_more"])

        third_user = self.create_user("chat-third")
        third_user_id = third_user["user_id"]
        recent_time = time.monotonic() + 301
        with patch("simple_broker.time.monotonic", return_value=recent_time):
            expired_presence = self.call(
                {"method": "chat.status", "token": first_user["token"]}
            )
        self.assertEqual(["chat-first"], expired_presence["online_users"])

        admin = self.create_user("chat-admin")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?", (admin["user_id"],)
            )
        admin_login = self.call(
            {
                "method": "auth.login",
                "username": "chat-admin",
                "password": "secure-pass-123",
            }
        )
        disabled = self.call(
            {
                "method": "admin.user.state",
                "token": admin_login["token"],
                "user_id": str(third_user_id),
                "is_active": "0",
            }
        )
        self.assertTrue(disabled["ok"], disabled)
        self.assertNotIn(
            "chat-third",
            self.call({"method": "chat.status", "token": first_user["token"]})[
                "online_users"
            ],
        )
        self.assertTrue(
            self.call({"method": "auth.logout", "token": second_user["token"]})["ok"]
        )
        logged_out_status = self.call(
            {"method": "chat.status", "token": first_user["token"]}
        )
        self.assertNotIn("chat-second", logged_out_status["online_users"])
        another_session = self.call(
            {
                "method": "auth.login",
                "username": "chat-first",
                "password": "secure-pass-123",
            }
        )
        self.assertTrue(
            self.call({"method": "auth.logout", "token": first_user["token"]})["ok"]
        )
        remaining_session_status = self.call(
            {"method": "chat.status", "token": another_session["token"]}
        )
        self.assertIn("chat-first", remaining_session_status["online_users"])

    def test_chat_rooms_unread_direct_messages_search_reactions_and_moderation(self) -> None:
        alice = self.create_user("chat-alice")
        bob = self.create_user("chat-bob")
        carol = self.create_user("chat-carol")
        admin = self.create_user("chat-moderator")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?", (admin["user_id"],)
            )
        admin_login = self.call(
            {
                "method": "auth.login",
                "username": "chat-moderator",
                "password": "secure-pass-123",
            }
        )

        posted = self.call(
            {
                "method": "chat.post",
                "token": alice["token"],
                "channel": "global",
                "message": "Olá @chat-bob, procure esta Frase Árvore",
            }
        )
        self.assertTrue(posted["ok"], posted)
        dashboard = self.call(
            {"method": "chat.dashboard", "token": bob["token"]}
        )
        self.assertEqual(1, dashboard["unread_count"])
        self.assertEqual(1, dashboard["mention_count"])
        self.assertEqual(
            "Olá @chat-bob, procure esta Frase Árvore",
            dashboard["preview"]["body"],
        )
        late_user = self.create_user("chat-latejoiner")
        late_dashboard = self.call(
            {"method": "chat.dashboard", "token": late_user["token"]}
        )
        self.assertEqual(0, late_dashboard["unread_count"])

        direct = self.call(
            {
                "method": "chat.dm.open",
                "token": alice["token"],
                "username": "chat-bob",
            }
        )
        self.assertTrue(direct["ok"], direct)
        with patch.object(simple_broker, "CHAT_SEND_INTERVAL_SECONDS", 0.0):
            direct_message = self.call(
                {
                    "method": "chat.post",
                    "token": alice["token"],
                    "channel": direct["channel"],
                    "message": "Mensagem privada",
                }
            )
        self.assertTrue(direct_message["ok"], direct_message)
        unauthorized = self.call(
            {
                "method": "chat.sync",
                "token": carol["token"],
                "channel": direct["channel"],
                "after_id": "0",
            }
        )
        self.assertFalse(unauthorized["ok"])
        self.assertIn("Acesso não autorizado", unauthorized["error"])
        rooms = self.call({"method": "chat.rooms", "token": bob["token"]})
        self.assertTrue(
            any(room["channel"] == direct["channel"] for room in rooms["rooms"])
        )
        direct_sync = self.call(
            {
                "method": "chat.sync",
                "token": bob["token"],
                "channel": direct["channel"],
                "after_id": "0",
            }
        )
        self.assertEqual(
            ["Mensagem privada"],
            [message["body"] for message in direct_sync["messages"]],
        )
        self.assertEqual(1, direct_sync["unread_count"])

        search = self.call(
            {
                "method": "chat.search",
                "token": bob["token"],
                "channel": "global",
                "query": "frase árvore",
            }
        )
        self.assertEqual(1, len(search["messages"]))
        global_sync = self.call(
            {
                "method": "chat.sync",
                "token": bob["token"],
                "channel": "global",
                "after_id": "0",
            }
        )
        self.assertEqual(0, global_sync["unread_count"])
        self.assertEqual(1, global_sync["messages"][0]["mentioned"])
        reacted = self.call(
            {
                "method": "chat.react",
                "token": bob["token"],
                "message_id": str(posted["message_id"]),
                "reaction": "👍",
            }
        )
        self.assertTrue(reacted["active"])
        reaction_refresh = self.call(
            {
                "method": "chat.sync",
                "token": bob["token"],
                "channel": "global",
                "after_id": str(global_sync["last_id"]),
            }
        )
        self.assertEqual([], reaction_refresh["messages"])
        self.assertIn("👍:1", reaction_refresh["window"][0]["reactions"])

        typing = self.call(
            {
                "method": "chat.typing",
                "token": alice["token"],
                "channel": "global",
                "active": "1",
            }
        )
        self.assertTrue(typing["ok"], typing)
        typing_sync = self.call(
            {
                "method": "chat.sync",
                "token": bob["token"],
                "channel": "global",
                "after_id": "0",
            }
        )
        self.assertIn("chat-alice", typing_sync["typing_users"])
        self.assertTrue(
            self.call(
                {
                    "method": "chat.typing",
                    "token": alice["token"],
                    "channel": "global",
                    "active": "0",
                }
            )["ok"]
        )

        report = self.call(
            {
                "method": "chat.report",
                "token": carol["token"],
                "message_id": str(posted["message_id"]),
                "reason": "Conteúdo inadequado",
            }
        )
        self.assertTrue(report["ok"], report)
        reports = self.call(
            {"method": "chat.admin.reports", "token": admin_login["token"]}
        )
        self.assertEqual(1, len(reports["reports"]))
        pinned = self.call(
            {
                "method": "chat.admin.moderate",
                "token": admin_login["token"],
                "message_id": str(posted["message_id"]),
                "action": "pin",
            }
        )
        self.assertTrue(pinned["ok"], pinned)
        hidden = self.call(
            {
                "method": "chat.admin.moderate",
                "token": admin_login["token"],
                "message_id": str(posted["message_id"]),
                "action": "hide",
            }
        )
        self.assertTrue(hidden["ok"], hidden)
        hidden_sync = self.call(
            {
                "method": "chat.sync",
                "token": bob["token"],
                "channel": "global",
                "after_id": "0",
            }
        )
        self.assertEqual([], hidden_sync["messages"])
        with closing(sqlite3.connect(self.database_path)) as connection:
            report_status = connection.execute(
                "SELECT status FROM chat_reports WHERE message_id = ?",
                (posted["message_id"],),
            ).fetchone()[0]
        self.assertEqual("resolved", report_status)
        restored = self.call(
            {
                "method": "chat.admin.moderate",
                "token": admin_login["token"],
                "message_id": str(posted["message_id"]),
                "action": "restore",
            }
        )
        self.assertTrue(restored["ok"], restored)
        restored_sync = self.call(
            {
                "method": "chat.sync",
                "token": bob["token"],
                "channel": "global",
                "after_id": "0",
            }
        )
        self.assertTrue(restored_sync["messages"][0]["pinned_at"])

    def test_chat_admin_permanently_clears_only_the_selected_room(self) -> None:
        author = self.create_user("clear-author")
        reporter = self.create_user("clear-reporter")
        admin = self.create_user("clear-admin")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?",
                (admin["user_id"],),
            )
        admin_login = self.call(
            {
                "method": "auth.login",
                "username": "clear-admin",
                "password": "secure-pass-123",
            }
        )
        with patch.object(simple_broker, "CHAT_SEND_INTERVAL_SECONDS", 0.0):
            global_post = self.call(
                {
                    "method": "chat.post",
                    "token": author["token"],
                    "channel": "global",
                    "message": "Apagar nesta sala",
                }
            )
            help_post = self.call(
                {
                    "method": "chat.post",
                    "token": author["token"],
                    "channel": "help",
                    "message": "Manter na sala de ajuda",
                }
            )
        self.assertTrue(global_post["ok"], global_post)
        self.assertTrue(help_post["ok"], help_post)
        reaction = self.call(
            {
                "method": "chat.react",
                "token": reporter["token"],
                "message_id": str(global_post["message_id"]),
                "reaction": "+1",
            }
        )
        report = self.call(
            {
                "method": "chat.report",
                "token": reporter["token"],
                "message_id": str(global_post["message_id"]),
                "reason": "Teste de exclusão",
            }
        )
        self.assertTrue(reaction["ok"], reaction)
        self.assertTrue(report["ok"], report)

        rejected = self.call(
            {
                "method": "chat.admin.clear",
                "token": author["token"],
                "channel": "global",
            }
        )
        self.assertFalse(rejected["ok"])
        deleted = self.call(
            {
                "method": "chat.admin.clear",
                "token": admin_login["token"],
                "channel": "global",
            }
        )
        self.assertTrue(deleted["ok"], deleted)
        self.assertEqual(1, deleted["deleted_count"])
        global_sync = self.call(
            {
                "method": "chat.sync",
                "token": author["token"],
                "channel": "global",
                "after_id": "0",
            }
        )
        help_sync = self.call(
            {
                "method": "chat.sync",
                "token": author["token"],
                "channel": "help",
                "after_id": "0",
            }
        )
        self.assertEqual([], global_sync["messages"])
        self.assertEqual(
            ["Manter na sala de ajuda"],
            [message["body"] for message in help_sync["messages"]],
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM chat_reports WHERE message_id = ?",
                    (global_post["message_id"],),
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM chat_reactions WHERE message_id = ?",
                    (global_post["message_id"],),
                ).fetchone()[0],
            )

    def test_broker_tui_chat_can_send_and_refresh_messages(self) -> None:
        self.create_user("chat-tui")
        peer = self.create_user("chat-peer")
        with patch.object(simple_broker, "CHAT_SEND_INTERVAL_SECONDS", 0.0):
            for body in ("mensagem anterior", "histórico antigo"):
                sent = self.call(
                    {
                        "method": "chat.send",
                        "token": peer["token"],
                        "message": body,
                    }
                )
                self.assertTrue(sent["ok"], sent)
        environment = os.environ.copy()
        environment.update(
            {"SIMPLE_MODE": "broker", "BROKER_SOCKET": str(self.socket_path)}
        )
        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\nchat-tui\nsecure-pass-123\n"
                "6\n\nHello everyone\n\n/sair\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("CHAT GLOBAL", process.stdout)
        self.assertIn("Online agora: 2", process.stdout)
        self.assertIn("você: Hello everyone", process.stdout)
        last_chat_screen = process.stdout.rfind("CHAT GLOBAL")
        self.assertIn("mensagem anterior", process.stdout[last_chat_screen:])
        self.assertIn("Hello everyone", process.stdout[last_chat_screen:])
        self.assertIn("Autoatualização desativada", process.stdout)
        self.assertEqual(1, process.stdout.count("CHAT GLOBAL"))
        self.assertNotIn("Operação não suportada", process.stdout)

    def test_chat_send_appends_message_without_clearing_terminal(self) -> None:
        self.create_user("chat-no-clear")
        environment = os.environ.copy()
        environment.update(
            {
                "SIMPLE_MODE": "broker",
                "BROKER_SOCKET": str(self.socket_path),
                "NO_COLOR": "1",
                "COLUMNS": "80",
            }
        )
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        process = subprocess.Popen(
            ["bash", str(ROOT_DIR / "simple.sh")],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=ROOT_DIR,
            env=environment,
            close_fds=True,
        )
        os.close(slave_fd)
        output = bytearray()

        def read_until(
            needle: bytes,
            timeout: float = 12.0,
            after: bytes | None = None,
        ) -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                start = output.find(after) + len(after) if after is not None else 0
                if output.find(needle, start) >= 0:
                    return
                ready, _, _ = select.select([master_fd], [], [], 0.2)
                if ready:
                    try:
                        output.extend(os.read(master_fd, 8192))
                    except OSError:
                        break
                if process.poll() is not None:
                    break
            start = output.find(after) + len(after) if after is not None else 0
            self.assertGreaterEqual(
                output.find(needle, start), 0, output.decode("utf-8", errors="replace")
            )

        try:
            read_until(b"OP\xc3\x87\xc3\x83O > ")
            os.write(master_fd, b"2\r")
            read_until(b"Nome de usu\xc3\xa1rio > ")
            os.write(master_fd, b"chat-no-clear\r")
            read_until(b"Senha > ")
            os.write(master_fd, b"secure-pass-123\r")
            read_until(b"[6] Chat global")
            read_until(b"OP\xc3\x87\xc3\x83O > ", after=b"[6] Chat global")
            os.write(master_fd, b"6\r")
            read_until(b"MENSAGEM > ")
            clear_count_before_send = output.count(b"\x1b[2J")
            os.write(master_fd, b"Mensagem sem piscar\r")
            sent_message = "você: Mensagem sem piscar".encode("utf-8")
            read_until(sent_message)
            self.assertEqual(clear_count_before_send, output.count(b"\x1b[2J"))
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            os.close(master_fd)

    def test_broker_tui_chat_preserves_draft_across_idle_refreshes(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "NO_COLOR": "1",
                "COLUMNS": "80",
            }
        )
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        original_terminal = termios.tcgetattr(slave_fd)
        reader_script = (
            'SCRIPT_DIR="$1"; source "$SCRIPT_DIR/core/database.sh"; '
            'source "$SCRIPT_DIR/core/broker_client.sh"; '
            'source "$SCRIPT_DIR/core/ui.sh"; '
            'source "$SCRIPT_DIR/modules/chat.sh"; '
            "CURRENT_USER_ID=42; CURRENT_USERNAME='me'; "
            "chat_mark_typing() { return 0; }; "
            "poll_count=0; "
            "chat_sync() { ((poll_count += 1)); "
            "if ((poll_count == 1)); then "
            "CHAT_NEW_MESSAGE_COUNT=1; "
            "CHAT_SYNC_RESPONSE='{\"messages\":[{\"id\":9,"
            "\"username\":\"peer\",\"user_id\":7,"
            "\"body\":\"Mensagem instantanea\","
            "\"created_at\":\"2026-11-04 23:00:00\","
            "\"pinned_at\":null,\"reactions\":\"\","
            "\"mentioned\":0}]}'; "
            "else CHAT_NEW_MESSAGE_COUNT=0; "
            "CHAT_SYNC_RESPONSE='{\"messages\":[]}'; fi; }; "
            "while true; do "
            "chat_read_message 1 1; status=$?; "
            "if ((status == 0)); then "
            'printf "LINE:%s\\n" "$CHAT_INPUT_LINE"; exit 0; '
            "else exit \"$status\"; fi; done"
        )
        process = subprocess.Popen(
            ["bash", "-c", reader_script, "chat-reader", str(ROOT_DIR)],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=ROOT_DIR,
            env=environment,
            close_fds=True,
        )
        output = bytearray()

        def read_until(needle: bytes, timeout: float = 12.0) -> None:
            deadline = time.monotonic() + timeout
            while needle not in output and time.monotonic() < deadline:
                ready, _, _ = select.select([master_fd], [], [], 0.2)
                if ready:
                    try:
                        output.extend(os.read(master_fd, 8192))
                    except OSError:
                        break
                if process.poll() is not None:
                    break
            self.assertIn(needle, output, output.decode("utf-8", errors="replace"))

        def drain_for(seconds: float) -> None:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master_fd], [], [], 0.1)
                if ready:
                    try:
                        output.extend(os.read(master_fd, 8192))
                    except OSError:
                        break

        def read_until_count(needle: bytes, count: int, timeout: float = 6.0) -> None:
            deadline = time.monotonic() + timeout
            while output.count(needle) < count and time.monotonic() < deadline:
                ready, _, _ = select.select([master_fd], [], [], 0.2)
                if ready:
                    try:
                        output.extend(os.read(master_fd, 8192))
                    except OSError:
                        break
                if process.poll() is not None:
                    break
            self.assertGreaterEqual(
                output.count(needle),
                count,
                output.decode("utf-8", errors="replace"),
            )

        try:
            read_until(b"MENSAGEM > ")
            clear_count_before_poll = output.count(b"\x1b[2J")
            os.write(master_fd, b"ab")
            read_until(b"Mensagem instantanea")
            read_until_count(b"MENSAGEM > ab", 2)
            self.assertEqual(clear_count_before_poll, output.count(b"\x1b[2J"))
            os.write(master_fd, b"cd\r")
            read_until(b"LINE:abcd", timeout=5.0)
            process.wait(timeout=10)
            self.assertEqual(0, process.returncode, output.decode("utf-8", errors="replace"))
            self.assertEqual(original_terminal, termios.tcgetattr(slave_fd))
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            os.close(master_fd)
            os.close(slave_fd)

    def test_broker_admin_menu_orders_chat_before_submissions(self) -> None:
        user = self.create_user("chat-admin-menu")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?",
                (user["user_id"],),
            )
        environment = os.environ.copy()
        environment.update(
            {"SIMPLE_MODE": "broker", "BROKER_SOCKET": str(self.socket_path)}
        )
        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input="2\nchat-admin-menu\nsecure-pass-123\n0\n",
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        option_lines = process.stdout.splitlines()
        positions = [
            next(index for index, line in enumerate(option_lines) if label in line)
            for label in (
                "[5] Administração",
                "[6] Chat global",
                "[7] Minhas entregas",
            )
        ]
        self.assertEqual(sorted(positions), positions)

    def test_broker_admin_can_confirm_room_history_deletion_in_chat(self) -> None:
        author = self.create_user("clear-cli-author")
        admin = self.create_user("clear-cli-admin")
        self.assertTrue(
            self.call(
                {
                    "method": "chat.post",
                    "token": author["token"],
                    "channel": "global",
                    "message": "Mensagem para apagar",
                }
            )["ok"]
        )
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?",
                (admin["user_id"],),
            )
        environment = os.environ.copy()
        environment.update(
            {"SIMPLE_MODE": "broker", "BROKER_SOCKET": str(self.socket_path)}
        )
        cancelled = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\nclear-cli-admin\nsecure-pass-123\n"
                "6\n/apagar-historico\nn\n/sair\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, cancelled.returncode, cancelled.stderr)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE channel = 'global'"
                ).fetchone()[0],
            )
        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\nclear-cli-admin\nsecure-pass-123\n"
                "6\n/apagar-historico\ns\n/sair\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("1 mensagem(ns) apagada(s) permanentemente", process.stdout)
        self.assertEqual(
            0,
            self.call(
                {
                    "method": "chat.sync",
                    "token": author["token"],
                    "channel": "global",
                    "after_id": "0",
                }
            )["last_id"],
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE channel = 'global'"
                ).fetchone()[0],
            )

    def test_broker_exercise_detail_opens_its_chat_room(self) -> None:
        self.create_user("exercise-chat-tui")
        environment = os.environ.copy()
        environment.update(
            {"SIMPLE_MODE": "broker", "BROKER_SOCKET": str(self.socket_path)}
        )
        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\nexercise-chat-tui\nsecure-pass-123\n"
                "1\n1\n2\n/sair\n0\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("Chat deste exercício", process.stdout)
        self.assertIn("SALA · SHELL00-EX00", process.stdout)
        self.assertIn("Online (1): você", process.stdout)

    def test_temporary_password_is_enforced_by_broker(self) -> None:
        registered = self.create_user()
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET must_change_password = 1 WHERE id = ?",
                (registered["user_id"],),
            )
        login = self.call(
            {"method": "auth.login", "username": "student", "password": "secure-pass-123"}
        )
        blocked = self.call({"method": "profile.get", "token": login["token"]})
        self.assertIn("Troque a senha temporária", blocked["error"])
        blocked_chat = self.call({"method": "chat.status", "token": login["token"]})
        self.assertIn("Troque a senha temporária", blocked_chat["error"])
        changed = self.call(
            {
                "method": "auth.change_password",
                "token": login["token"],
                "password": "replacement-pass",
            }
        )
        self.assertTrue(changed["ok"])
        self.assertNotEqual(login["token"], changed["token"])
        self.assertIn(
            "Sessão inválida",
            self.call({"method": "profile.get", "token": login["token"]})["error"],
        )
        self.assertTrue(
            self.call({"method": "profile.get", "token": changed["token"]})["ok"]
        )

    def test_password_change_revokes_every_old_session_and_preserves_other_users(self) -> None:
        user = self.create_user("password-owner")
        second_session = self.call(
            {
                "method": "auth.login",
                "username": "password-owner",
                "password": "secure-pass-123",
            }
        )
        other_user = self.create_user("unrelated-user")

        changed = self.call(
            {
                "method": "auth.change_password",
                "token": user["token"],
                "password": "replacement-pass",
            }
        )
        self.assertTrue(changed["ok"], changed)
        for token in (user["token"], second_session["token"]):
            rejected = self.call({"method": "profile.get", "token": token})
            self.assertIn("Sessão inválida", rejected["error"])
        self.assertTrue(
            self.call({"method": "profile.get", "token": changed["token"]})["ok"]
        )
        self.assertTrue(
            self.call({"method": "profile.get", "token": other_user["token"]})["ok"]
        )
        self.assertTrue(
            self.call(
                {
                    "method": "auth.login",
                    "username": "password-owner",
                    "password": "replacement-pass",
                }
            )["ok"]
        )

    def test_concurrent_password_changes_are_atomic_and_revoke_old_tokens(self) -> None:
        user = self.create_user("racing-password")
        other_user = self.create_user("unrelated-race-user")
        requests = [
            {
                "method": "auth.change_password",
                "token": user["token"],
                "password": candidate,
            }
            for candidate in ("first-password", "second-password")
        ]
        requests.extend(
            (
                {"method": "profile.get", "token": user["token"]},
                {"method": "profile.get", "token": other_user["token"]},
            )
        )
        start_together = threading.Barrier(len(requests))

        def concurrent_call(request: dict[str, object]) -> dict[str, object]:
            start_together.wait(timeout=5)
            return self.call(request)

        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            responses = list(executor.map(concurrent_call, requests))

        successful_changes = [response for response in responses[:2] if response["ok"]]
        self.assertEqual(1, len(successful_changes), responses)
        self.assertTrue(
            responses[3]["ok"],
            "A operação concorrente de outra conta deve continuar válida.",
        )
        self.assertTrue(
            responses[2]["ok"]
            or "Sessão inválida" in str(responses[2].get("error")),
            responses[2],
        )
        self.assertIn(
            "Sessão inválida",
            self.call({"method": "profile.get", "token": user["token"]})["error"],
        )
        valid_passwords = [
            password
            for password in ("first-password", "second-password")
            if self.call(
                {
                    "method": "auth.login",
                    "username": "racing-password",
                    "password": password,
                }
            )["ok"]
        ]
        self.assertEqual(1, len(valid_passwords))
        self.assertTrue(
            self.call({"method": "profile.get", "token": other_user["token"]})["ok"]
        )
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])

    def test_no_anonymous_or_arbitrary_sql_operation(self) -> None:
        anonymous = self.call({"method": "catalog.list", "token": ""})
        self.assertFalse(anonymous["ok"])

        arbitrary_sql = self.call(
            {"method": "db.query", "sql": "DELETE FROM users"}
        )
        self.assertEqual("Operação não suportada.", arbitrary_sql["error"])
        malformed_method = self.call({"method": []})
        self.assertEqual("Operação não suportada.", malformed_method["error"])
        registered = self.create_user()
        profile_other_user = self.call(
            {
                "method": "profile.get",
                "token": registered["token"],
                "user_id": 1,
            }
        )
        self.assertIn("Campos inválidos", profile_other_user["error"])
        self.assertTrue(
            self.call({"method": "auth.logout", "token": registered["token"]})["ok"]
        )
        expired_session = self.call(
            {"method": "profile.get", "token": registered["token"]}
        )
        self.assertIn("Sessão inválida", expired_session["error"])

    def test_incompatible_schema_is_not_migrated_implicitly(self) -> None:
        legacy_database = self.root / "legacy.db"
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            connection.execute(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)"
            )
        os.chmod(legacy_database, 0o600)
        with self.assertRaisesRegex(RuntimeError, "migração explícita"):
            validate_database(legacy_database)
        with closing(sqlite3.connect(legacy_database)) as connection:
            columns = connection.execute("PRAGMA table_info(users)").fetchall()
        self.assertEqual(["id", "username"], [column[1] for column in columns])

    def test_explicit_initialization_backs_up_an_existing_database(self) -> None:
        legacy_database = self.root / "legacy-existing.db"
        schema_text = (ROOT_DIR / "database" / "schema.sql").read_text(
            encoding="utf-8"
        )
        for definition in (
            "    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),\n",
            "    accepted_by INTEGER REFERENCES users(id),\n",
            "    accepted_at DATETIME,\n",
        ):
            schema_text = schema_text.replace(definition, "", 1)
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            connection.executescript(schema_text)
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("preserved-user", "hash"),
            )
            connection.execute(
                "INSERT INTO exercises (code, name, description) VALUES (?, ?, ?)",
                ("KEEP-01", "Preservado", "Dado existente"),
            )
        os.chmod(legacy_database, 0o600)
        backup_path = initialize_database(
            legacy_database, ROOT_DIR / "database" / "schema.sql"
        )
        self.assertIsNotNone(backup_path)
        assert backup_path is not None
        self.assertEqual(0o600, backup_path.stat().st_mode & 0o777)
        with closing(sqlite3.connect(backup_path)) as connection, connection:
            self.assertEqual(
                ("Preservado",),
                connection.execute(
                    "SELECT name FROM exercises WHERE code = 'KEEP-01'"
                ).fetchone(),
            )
            self.assertNotIn(
                "is_active",
                {row[1] for row in connection.execute("PRAGMA table_info(users)")},
            )
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            self.assertIn(
                "is_active",
                {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(users)")
                },
            )
            self.assertEqual(
                "Preservado",
                connection.execute(
                    "SELECT name FROM exercises WHERE code = 'KEEP-01'"
                ).fetchone()[0],
            )
        validate_database(legacy_database)

    def test_completed_legacy_reservation_migrates_as_completed(self) -> None:
        owner = self.create_user("legacy-owner")
        reviewer = self.create_user("legacy-reviewer")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO exercises (code, name, description) VALUES (?, ?, ?)",
                ("LEGACY-01", "Legado", "Exercício de migração"),
            )
        submission = self.call(
            {
                "method": "submissions.create",
                "token": owner["token"],
                "exercise_code": "LEGACY-01",
                "commit_url": "https://github.com/student/project/commit/abcdef0123456",
                "delivery_directory": ".",
            }
        )
        self.assertTrue(submission["ok"], submission)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                UPDATE evaluation_slots
                SET status = 'completed', reviewer_id = ?, accepted_by = ?,
                    accepted_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (reviewer["user_id"], reviewer["user_id"], submission["slot_id"]),
            )
            connection.execute("DELETE FROM evaluation_requests")

        initialize_database(self.database_path, ROOT_DIR / "database" / "schema.sql")

        with closing(sqlite3.connect(self.database_path)) as connection:
            request = connection.execute(
                "SELECT status FROM evaluation_requests WHERE slot_id = ?",
                (submission["slot_id"],),
            ).fetchone()
        self.assertEqual(("completed",), request)

    def test_failed_migration_rolls_back_and_preserves_valid_backup(self) -> None:
        legacy_database = self.root / "legacy-failed-migration.db"
        schema_text = (ROOT_DIR / "database" / "schema.sql").read_text(
            encoding="utf-8"
        )
        for definition in (
            "    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),\n",
            "    accepted_by INTEGER REFERENCES users(id),\n",
            "    accepted_at DATETIME,\n",
        ):
            schema_text = schema_text.replace(definition, "", 1)
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            connection.executescript(schema_text)
            connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("rollback-user", "unchanged-hash"),
            )
        os.chmod(legacy_database, 0o600)

        def database_snapshot(
            path: Path,
        ) -> tuple[list[tuple[str, str, str]], list[str], tuple[str, str] | None]:
            with closing(sqlite3.connect(path)) as connection, connection:
                schema = connection.execute(
                    """
                    SELECT type, name, sql FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                ).fetchall()
                users_columns = [
                    row[1] for row in connection.execute("PRAGMA table_info(users)")
                ]
                user = connection.execute(
                    "SELECT username, password_hash FROM users WHERE username = ?",
                    ("rollback-user",),
                ).fetchone()
            return schema, users_columns, user

        original_snapshot = database_snapshot(legacy_database)
        failed_migrations = (
            *simple_broker.MIGRATIONS,
            ("users", "temporary_migration_column", "TEXT"),
            ("users", "invalid_migration_column", "TEXT, extra_column TEXT"),
        )
        with patch("simple_broker.MIGRATIONS", failed_migrations):
            with self.assertRaises(sqlite3.OperationalError):
                initialize_database(
                    legacy_database, ROOT_DIR / "database" / "schema.sql"
                )

        self.assertEqual(original_snapshot, database_snapshot(legacy_database))
        backups = list(self.root.glob("legacy-failed-migration.db.backup-*.sqlite"))
        self.assertEqual(1, len(backups))
        validate_database(backups[0], allow_pre_migration_schema=True)
        check_database_integrity(backups[0])
        self.assertEqual(original_snapshot, database_snapshot(backups[0]))

    def test_invalid_backup_stops_migration_and_removes_incomplete_backup(self) -> None:
        original_snapshot = self.database_path.read_bytes()
        original_validate = validate_database

        def corrupt_backup(path: Path, *, allow_pre_migration_schema: bool = False) -> None:
            if ".backup-" in path.name:
                path.write_bytes(b"incomplete SQLite backup")
            original_validate(
                path, allow_pre_migration_schema=allow_pre_migration_schema
            )

        with patch("simple_broker.validate_database", side_effect=corrupt_backup):
            with self.assertRaises((RuntimeError, sqlite3.Error)):
                initialize_database(
                    self.database_path, ROOT_DIR / "database" / "schema.sql"
                )

        self.assertEqual(original_snapshot, self.database_path.read_bytes())
        self.assertEqual([], list(self.root.glob("simple.db.backup-*.sqlite")))

    def test_failed_backup_copy_removes_incomplete_backup(self) -> None:
        original_snapshot = self.database_path.read_bytes()
        original_connect = sqlite3.connect

        def fail_source_connection(
            database: str | Path, *args: object, **kwargs: object
        ):
            if Path(database) == self.database_path:
                raise sqlite3.OperationalError("simulated source read failure")
            return original_connect(database, *args, **kwargs)

        with patch("simple_broker.sqlite3.connect", side_effect=fail_source_connection):
            with self.assertRaisesRegex(sqlite3.OperationalError, "source read failure"):
                initialize_database(
                    self.database_path, ROOT_DIR / "database" / "schema.sql"
                )

        self.assertEqual(original_snapshot, self.database_path.read_bytes())
        self.assertEqual([], list(self.root.glob("simple.db.backup-*.sqlite")))

    def test_schema_transaction_controls_are_rejected_before_backup(self) -> None:
        schema_path = self.root / "incompatible-schema.sql"
        schema_path.write_text(
            "CREATE TABLE migration_marker (id INTEGER);\nCOMMIT;\n",
            encoding="utf-8",
        )
        original_snapshot = self.database_path.read_bytes()

        with self.assertRaisesRegex(RuntimeError, "incompatível"):
            initialize_database(self.database_path, schema_path)

        self.assertEqual(original_snapshot, self.database_path.read_bytes())
        self.assertEqual([], list(self.root.glob("simple.db.backup-*.sqlite")))

    def test_one_shot_database_initialization_refuses_existing_database(self) -> None:
        database_path = self.root / "production-candidate.db"
        command = [
            sys.executable,
            str(ROOT_DIR / "broker" / "simple_broker.py"),
            "--database",
            str(database_path),
            "--schema",
            str(ROOT_DIR / "database" / "schema.sql"),
            "--initialize-database-only",
        ]
        initialized = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=10
        )
        self.assertEqual(0, initialized.returncode, initialized.stderr)
        self.assertIn("inicializado e íntegro", initialized.stderr)
        self.assertEqual(0o600, database_path.stat().st_mode & 0o777)
        check_database_integrity(database_path)
        original_contents = database_path.read_bytes()

        rejected = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=10
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("O banco já existe", rejected.stderr)
        self.assertEqual(original_contents, database_path.read_bytes())

    def test_broker_backup_is_private_verified_and_never_overwritten(self) -> None:
        self.create_user("backup-user")
        backup_path = self.root / "backups" / "simple-copy.sqlite"
        backup_path.parent.mkdir(mode=0o700)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT_DIR / "broker" / "simple_broker.py"),
                "--database",
                str(self.database_path),
                "--backup-database",
                str(backup_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("Backup SQLite validado", completed.stderr)
        self.assertEqual(0o600, backup_path.stat().st_mode & 0o777)
        check_database_integrity(backup_path)
        with closing(sqlite3.connect(backup_path)) as connection, connection:
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT COUNT(*) FROM users WHERE username = 'backup-user'"
                ).fetchone()[0],
            )
        original_contents = backup_path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "já existe"):
            backup_database(self.database_path, backup_path)
        self.assertEqual(original_contents, backup_path.read_bytes())

    def test_explicit_migration_mode_creates_backup_and_does_not_start_server(self) -> None:
        existing_socket_identity = (
            self.socket_path.stat().st_dev,
            self.socket_path.stat().st_ino,
        )
        command = [
            sys.executable,
            str(ROOT_DIR / "broker" / "simple_broker.py"),
            "--database",
            str(self.database_path),
            "--schema",
            str(ROOT_DIR / "database" / "schema.sql"),
            "--migrate-database-only",
        ]
        migrated = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=10
        )

        self.assertEqual(0, migrated.returncode, migrated.stderr)
        self.assertIn("Banco SQLite migrado e íntegro", migrated.stderr)
        self.assertEqual(
            existing_socket_identity,
            (self.socket_path.stat().st_dev, self.socket_path.stat().st_ino),
        )
        self.assertEqual(
            1,
            len(list(self.root.glob("simple.db.backup-*.sqlite"))),
        )
        check_database_integrity(self.database_path)

    def test_broker_entrypoint_removes_its_socket_after_sigterm(self) -> None:
        socket_path = self.root / "broker-sigterm.sock"
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT_DIR / "broker" / "simple_broker.py"),
                "--socket",
                str(socket_path),
                "--database",
                str(self.database_path),
            ],
            cwd=ROOT_DIR,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=1)
                    self.fail(f"Broker saiu na inicialização: {stdout}{stderr}")
                if socket_path.exists():
                    try:
                        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                            probe.connect(str(socket_path))
                        break
                    except OSError:
                        pass
                time.sleep(0.05)
            else:
                self.fail("socket do Broker não ficou pronto")

            self.assertTrue(self.server.daemon_threads)
            self.assertFalse(self.server.block_on_close)
            process.send_signal(signal.SIGTERM)
            try:
                _stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _stdout, stderr = process.communicate(timeout=5)
                self.fail(f"Broker não encerrou em SIGTERM: {stderr}")
            self.assertEqual(0, process.returncode, stderr)
            self.assertNotIn("BrokenPipeError", stderr)
            self.assertFalse(socket_path.exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_disconnected_client_releases_broker_slot_without_traceback(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(str(self.socket_path))

        def available_slots() -> int:
            acquired = 0
            while acquired < simple_broker.BROKER_MAX_CONNECTIONS:
                if not self.server.connection_slots.acquire(blocking=False):
                    break
                acquired += 1
            for _ in range(acquired):
                self.server.connection_slots.release()
            return acquired

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and available_slots() != (
            simple_broker.BROKER_MAX_CONNECTIONS - 1
        ):
            time.sleep(0.01)
        self.assertEqual(
            simple_broker.BROKER_MAX_CONNECTIONS - 1, available_slots()
        )

        with patch.object(
            self.server, "handle_error", wraps=self.server.handle_error
        ) as handle_error:
            connection.close()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and available_slots() != (
                simple_broker.BROKER_MAX_CONNECTIONS
            ):
                time.sleep(0.01)
            self.assertEqual(
                simple_broker.BROKER_MAX_CONNECTIONS, available_slots()
            )
            handle_error.assert_not_called()

    def test_broker_releases_connection_slot_when_worker_cannot_start(self) -> None:
        client, peer = socket.socketpair()
        try:
            with patch.object(
                threading.Thread, "start", side_effect=RuntimeError("thread start failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                    self.server.process_request(client, None)

            acquired = 0
            while self.server.connection_slots.acquire(blocking=False):
                acquired += 1
            for _ in range(acquired):
                self.server.connection_slots.release()
            self.assertEqual(simple_broker.BROKER_MAX_CONNECTIONS, acquired)
        finally:
            client.close()
            peer.close()

    def test_broker_connect_closes_connection_when_setup_fails(self) -> None:
        connection = sqlite3.connect(self.database_path)
        with (
            patch("simple_broker.sqlite3.connect", return_value=connection),
            patch(
                "simple_broker._validate_database_files",
                side_effect=[self.database_path, RuntimeError("validation failed")],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "validation failed"):
                self.broker._connect()

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_database_validation_rejects_foreign_key_violations(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                """
                INSERT INTO admin_point_adjustments (user_id, admin_id, amount, reason)
                VALUES (999, 999, 1, 'orphaned test data')
                """
            )
        with self.assertRaisesRegex(RuntimeError, "integridade referencial"):
            validate_database(self.database_path)

    def test_database_permissions_are_restricted(self) -> None:
        self.assertEqual(0o600, self.database_path.stat().st_mode & 0o777)
        exposed_database = self.root / "exposed.db"
        initialize_database(exposed_database, ROOT_DIR / "database" / "schema.sql")
        os.chmod(exposed_database, 0o640)
        with self.assertRaisesRegex(RuntimeError, "permissões privadas"):
            validate_database(exposed_database)

    def test_chat_schema_migrates_existing_database_with_validated_backup(self) -> None:
        user = self.create_user("chat-migration")
        author = self.create_user("chat-migration-peer")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            for table in (
                "chat_mentions",
                "chat_reactions",
                "chat_reports",
                "chat_direct_rooms",
                "chat_reads",
                "chat_messages",
            ):
                connection.execute(f"DROP TABLE {table}")
            connection.execute(
                """
                CREATE TABLE chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    body TEXT NOT NULL CHECK (length(trim(body)) BETWEEN 1 AND 500),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "INSERT INTO chat_messages (user_id, body) VALUES (?, ?)",
                (author["user_id"], "mensagem anterior à migração"),
            )

        validate_database(self.database_path, allow_pre_migration_schema=True)
        with self.assertRaisesRegex(RuntimeError, "chat_messages"):
            validate_database(self.database_path)

        backup_path = initialize_database(
            self.database_path, ROOT_DIR / "database" / "schema.sql"
        )
        self.assertIsNotNone(backup_path)
        validate_database(self.database_path)
        assert backup_path is not None
        validate_database(backup_path, allow_pre_migration_schema=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(chat_messages)")
            }
            migrated_message = connection.execute(
                "SELECT body, channel FROM chat_messages WHERE id = 1"
            ).fetchone()
        self.assertEqual(
            {
                "id", "user_id", "body", "channel", "recipient_id", "created_at",
                "hidden_at", "pinned_at",
            },
            columns,
        )
        self.assertEqual(
            ("mensagem anterior à migração", "global"), migrated_message
        )
        self.assertEqual(
            0,
            self.call(
                {"method": "chat.dashboard", "token": user["token"]}
            )["unread_count"],
        )

    def test_database_rejects_relative_paths_and_untrusted_directories(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "absoluto"):
            validate_database(Path("relative.db"))

        os.chmod(self.root, 0o755)
        try:
            with self.assertRaisesRegex(RuntimeError, "deve ser privado"):
                validate_database(self.database_path)
            with self.assertRaisesRegex(RuntimeError, "deve existir previamente"):
                initialize_database(
                    self.root / "not-created" / "simple.db",
                    ROOT_DIR / "database" / "schema.sql",
                )
        finally:
            os.chmod(self.root, 0o700)

    def test_database_rejects_wrong_directory_owner(self) -> None:
        with patch("simple_broker.os.geteuid", return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(RuntimeError, "deve pertencer"):
                validate_database(self.database_path)

    def test_database_rejects_symlink_nonregular_and_unsafe_auxiliary_files(self) -> None:
        symlink_database = self.root / "symlink.db"
        symlink_database.symlink_to(self.database_path)
        with self.assertRaisesRegex(RuntimeError, "arquivo regular"):
            validate_database(symlink_database)

        nonregular_database = self.root / "fifo.db"
        os.mkfifo(nonregular_database, 0o600)
        with self.assertRaisesRegex(RuntimeError, "arquivo regular"):
            validate_database(nonregular_database)

        for suffix in ("-wal", "-shm", "-journal"):
            auxiliary_path = Path(f"{self.database_path}{suffix}")
            auxiliary_path.write_text("test", encoding="utf-8")
            os.chmod(auxiliary_path, 0o644)
            try:
                with self.assertRaisesRegex(RuntimeError, "arquivo auxiliar"):
                    validate_database(self.database_path)
            finally:
                auxiliary_path.unlink()

            auxiliary_path.symlink_to(self.database_path)
            try:
                with self.assertRaisesRegex(RuntimeError, "arquivo regular"):
                    validate_database(self.database_path)
            finally:
                auxiliary_path.unlink()

    def test_database_initialization_rejects_an_unsafe_backup_directory(self) -> None:
        unsafe_directory = self.root / "unsafe-backups"
        unsafe_directory.mkdir()
        existing_database = unsafe_directory / "simple.db"
        os.chmod(unsafe_directory, 0o700)
        initialize_database(existing_database, ROOT_DIR / "database" / "schema.sql")
        os.chmod(unsafe_directory, 0o755)
        try:
            with self.assertRaisesRegex(RuntimeError, "deve ser privado"):
                initialize_database(
                    existing_database, ROOT_DIR / "database" / "schema.sql"
                )
        finally:
            os.chmod(unsafe_directory, 0o700)

    def test_protocol_rejects_duplicate_keys_invalid_json_and_oversized_requests(self) -> None:
        requests = (
            b'{"method":"auth.logout","token":"first","token":"second"}\n',
            b'{"method":"catalog.list","token":"token","value":NaN}\n',
            b"x" * 65537 + b"\n",
        )
        expected = (
            "Chave duplicada",
            "Constante JSON inválida",
            "muito grande",
        )
        for request, message in zip(requests, expected):
            with self.subTest(message=message):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(3)
                    connection.connect(str(self.socket_path))
                    connection.sendall(request)
                    response = json.loads(connection.makefile("rb").readline())
                self.assertFalse(response["ok"])
                self.assertIn(message, response["error"])

    def test_socket_mode_and_bash_client_flow(self) -> None:
        self.assertEqual(0o600, self.socket_path.stat().st_mode & 0o777)
        environment = os.environ.copy()
        environment.update(
            {
                "SIMPLE_MODE": "broker",
                "BROKER_SOCKET": str(self.socket_path),
            }
        )
        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input="1\nbroker-user\nsecure-pass-123\nsecure-pass-123\n0\n",
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("Conta criada.", process.stdout)
        with closing(sqlite3.connect(self.database_path)) as connection:
            user_count = connection.execute(
                "SELECT COUNT(*) FROM users WHERE username = 'broker-user'"
            ).fetchone()[0]
        self.assertEqual(1, user_count)

    def test_broker_mode_profile_and_catalog(self) -> None:
        self.create_user()
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO exercises (code, name, description) VALUES (?, ?, ?)",
                ("C-01", "Exercício do broker", "Somente leitura"),
            )
        environment = os.environ.copy()
        environment.update(
            {
                "SIMPLE_MODE": "broker",
                "BROKER_SOCKET": str(self.socket_path),
            }
        )
        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\nstudent\nsecure-pass-123\n"
                "1\n1\n0\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("C-01 - Exercício do broker", process.stdout)
        self.assertIn("Somente leitura", process.stdout)
        self.assertNotIn("Acesso SQLite local bloqueado", process.stderr)

        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input="2\nstudent\nsecure-pass-123\n4\n0\n0\n",
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("MEU PERFIL", process.stdout)
        self.assertIn("Saldo de pontos: 1", process.stdout)

    def test_broker_submission_reservation_and_review_workflow(self) -> None:
        owner = self.create_user("exercise-owner")
        reviewer = self.create_user("exercise-reviewer")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO exercises (code, name, description, statement)
                VALUES (?, ?, ?, ?)
                """,
                ("C-42", "Exercício de teste", "Descrição", "Missão"),
            )
        submission = self.call(
            {
                "method": "submissions.create",
                "token": owner["token"],
                "exercise_code": "C-42",
                "commit_url": "https://github.com/student/project/commit/abcdef0123456",
                "delivery_directory": "src",
            }
        )
        self.assertTrue(submission["ok"], submission)
        self.assertEqual(0, submission["points"])
        requirements = self.call(
            {
                "method": "submissions.requirements",
                "token": owner["token"],
                "exercise_code": "C-42",
            }
        )
        self.assertFalse(requirements["eligible"])
        submitted_list = self.call({"method": "submissions.list", "token": owner["token"]})
        submitted_detail = self.call(
            {
                "method": "submissions.get",
                "token": owner["token"],
                "submission_id": str(submission["submission_id"]),
            }
        )
        self.assertEqual(submission["submission_id"], submitted_list["submissions"][0]["id"])
        self.assertEqual("pending", submitted_detail["submission"]["status"])
        self.assertEqual(
            "Descrição",
            submitted_detail["submission"]["exercise"]["description"],
        )
        self.assertEqual(
            "Missão",
            submitted_detail["submission"]["exercise"]["statement"],
        )
        self.assertEqual(
            0,
            self.call({"method": "points.balance", "token": owner["token"]})["points"],
        )
        self.assertEqual(
            -1,
            self.call({"method": "points.history", "token": owner["token"]})["entries"][0]["amount"],
        )

        slots = self.call(
            {"method": "evaluations.slots.list", "token": reviewer["token"]}
        )
        self.assertEqual(submission["slot_id"], slots["slots"][0]["id"])
        accepted = self.call(
            {
                "method": "evaluations.accept",
                "token": reviewer["token"],
                "slot_id": str(submission["slot_id"]),
            }
        )
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual("pending", accepted["status"])
        self.assertFalse(
            self.call(
                {
                    "method": "evaluations.slot.get",
                    "token": reviewer["token"],
                    "slot_id": str(submission["slot_id"]),
                }
            )["ok"]
        )
        incoming_requests = self.call(
            {"method": "evaluations.requests.list", "token": owner["token"]}
        )
        self.assertEqual(accepted["request_id"], incoming_requests["requests"][0]["request_id"])
        self.assertEqual("exercise-reviewer", incoming_requests["requests"][0]["reviewer"])
        self.assertEqual(
            1,
            self.call({"method": "notifications.count", "token": owner["token"]})[
                "count"
            ],
        )
        self.assertEqual(
            1,
            self.call({"method": "progress.get", "token": owner["token"]})[
                "pending_evaluation_requests"
            ],
        )
        owner_accept = self.call(
            {
                "method": "evaluations.request.respond",
                "token": owner["token"],
                "request_id": str(accepted["request_id"]),
                "decision": "accept",
            }
        )
        self.assertTrue(owner_accept["ok"], owner_accept)
        self.assertEqual("accepted", owner_accept["status"])
        self.assertEqual(
            1,
            self.call({"method": "notifications.count", "token": reviewer["token"]})[
                "count"
            ],
        )
        reviewer_progress = self.call(
            {"method": "progress.get", "token": reviewer["token"]}
        )
        self.assertEqual(
            "exercise-owner",
            reviewer_progress["accepted_evaluation_requests"][0]["submitter"],
        )
        self.assertEqual(
            submission["slot_id"],
            reviewer_progress["accepted_evaluation_requests"][0]["slot_id"],
        )
        self.assertEqual(
            0,
            self.call({"method": "notifications.count", "token": owner["token"]})[
                "count"
            ],
        )
        slot_detail = self.call(
            {
                "method": "evaluations.slot.get",
                "token": reviewer["token"],
                "slot_id": str(submission["slot_id"]),
            }
        )
        self.assertTrue(slot_detail["ok"], slot_detail)
        self.assertEqual("Descrição", slot_detail["slot"]["description"])
        duplicate_accept = self.call(
            {
                "method": "evaluations.accept",
                "token": reviewer["token"],
                "slot_id": str(submission["slot_id"]),
            }
        )
        self.assertTrue(duplicate_accept["ok"], duplicate_accept)
        self.assertEqual("accepted", duplicate_accept["status"])
        cancelled = self.call(
            {
                "method": "evaluations.cancel",
                "token": reviewer["token"],
                "slot_id": str(submission["slot_id"]),
            }
        )
        self.assertTrue(cancelled["ok"], cancelled)
        accepted_again = self.call(
            {
                "method": "evaluations.accept",
                "token": reviewer["token"],
                "slot_id": str(submission["slot_id"]),
            }
        )
        self.assertTrue(accepted_again["ok"], accepted_again)
        owner_accept_again = self.call(
            {
                "method": "evaluations.request.respond",
                "token": owner["token"],
                "request_id": str(accepted_again["request_id"]),
                "decision": "accept",
            }
        )
        self.assertTrue(owner_accept_again["ok"], owner_accept_again)

        missing_feedback = self.call(
            {
                "method": "evaluations.review.submit",
                "token": reviewer["token"],
                "slot_id": str(submission["slot_id"]),
                "score": "4",
                "feedback": "   ",
            }
        )
        self.assertFalse(missing_feedback["ok"])
        review = self.call(
            {
                "method": "evaluations.review.submit",
                "token": reviewer["token"],
                "slot_id": str(submission["slot_id"]),
                "score": "4",
                "feedback": "Boa solução.",
            }
        )
        self.assertTrue(review["ok"], review)
        self.assertEqual("approved", review["status"])
        self.assertEqual(2, review["points"])
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            status = connection.execute(
                "SELECT status FROM submissions WHERE id = ?",
                (submission["submission_id"],),
            ).fetchone()[0]
            ledger_rows = connection.execute(
                "SELECT COUNT(*) FROM points_ledger WHERE entry_type = 'review_completed'"
            ).fetchone()[0]
        self.assertEqual("approved", status)
        self.assertEqual(1, ledger_rows)
        history = self.call(
            {"method": "evaluations.history", "token": reviewer["token"]}
        )
        self.assertEqual("given", history["reviews"][0]["relation"])
        progress = self.call({"method": "progress.get", "token": owner["token"]})
        self.assertEqual(1, progress["submissions"]["approved"])
        completed_reviewer_progress = self.call(
            {"method": "progress.get", "token": reviewer["token"]}
        )
        self.assertEqual(
            [], completed_reviewer_progress["accepted_evaluation_requests"]
        )
        self.assertEqual(
            0,
            self.call({"method": "notifications.count", "token": reviewer["token"]})[
                "count"
            ],
        )
        self.assertEqual("review_received", progress["notifications"][0]["type"])
        self.assertEqual("Boa solução.", progress["review_history"][0]["feedback"])
        self.assertEqual(1, self.call(
            {"method": "notifications.count", "token": owner["token"]}
        )["count"])

        response = self.call(
            {
                "method": "evaluations.review.respond",
                "token": owner["token"],
                "review_id": str(review["review_id"]),
                "score": "5",
                "feedback": "Feedback claro e respeitoso.",
            }
        )
        self.assertTrue(response["ok"], response)
        duplicate_response = self.call(
            {
                "method": "evaluations.review.respond",
                "token": owner["token"],
                "review_id": str(review["review_id"]),
                "score": "4",
                "feedback": "Resposta duplicada.",
            }
        )
        self.assertFalse(duplicate_response["ok"])
        updated_progress = self.call(
            {"method": "progress.get", "token": owner["token"]}
        )
        self.assertEqual([], updated_progress["pending_review_responses"])
        self.assertEqual(5, updated_progress["review_history"][0]["recipient_score"])
        self.assertEqual(
            "Feedback claro e respeitoso.",
            updated_progress["review_history"][0]["recipient_feedback"],
        )
        reviewer_progress = self.call(
            {"method": "progress.get", "token": reviewer["token"]}
        )
        self.assertEqual("response_received", reviewer_progress["notifications"][0]["type"])
        self.assertEqual(
            1,
            self.call(
                {"method": "notifications.count", "token": reviewer["token"]}
            )["count"],
        )
        self.assertTrue(
            self.call(
                {"method": "notifications.mark_read", "token": reviewer["token"]}
            )["ok"]
        )
        self.assertEqual(
            0,
            self.call(
                {"method": "notifications.count", "token": reviewer["token"]}
            )["count"],
        )

        stale_reservation = self.call(
            {
                "method": "evaluations.cancel",
                "token": reviewer["token"],
                "slot_id": str(submission["slot_id"]),
            }
        )
        self.assertFalse(stale_reservation["ok"])

    def test_only_one_reviewer_can_reserve_a_slot_concurrently(self) -> None:
        owner = self.create_user("slot-owner")
        reviewer_one = self.create_user("slot-reviewer-one")
        reviewer_two = self.create_user("slot-reviewer-two")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO exercises (code, name, description) VALUES (?, ?, ?)",
                ("SLOT-01", "Slot concorrente", "Teste de concorrência"),
            )
        submission = self.call(
            {
                "method": "submissions.create",
                "token": owner["token"],
                "exercise_code": "SLOT-01",
                "commit_url": "https://github.com/student/project/commit/abcdef0123456",
                "delivery_directory": ".",
            }
        )
        self.assertTrue(submission["ok"], submission)
        requests = (
            {
                "method": "evaluations.accept",
                "token": reviewer_one["token"],
                "slot_id": str(submission["slot_id"]),
            },
            {
                "method": "evaluations.accept",
                "token": reviewer_two["token"],
                "slot_id": str(submission["slot_id"]),
            },
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(self.call, requests))
        self.assertEqual(1, sum(bool(response["ok"]) for response in responses), responses)

    def test_declined_request_blocks_same_reviewer_and_pending_request_expires(self) -> None:
        owner = self.create_user("consent-owner")
        declined_reviewer = self.create_user("declined-reviewer")
        pending_reviewer = self.create_user("pending-reviewer")
        available_reviewer = self.create_user("available-reviewer")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO exercises (code, name, description) VALUES (?, ?, ?)",
                ("CONSENT-01", "Consentimento", "Teste de pedido"),
            )
        submission = self.call(
            {
                "method": "submissions.create",
                "token": owner["token"],
                "exercise_code": "CONSENT-01",
                "commit_url": "https://github.com/student/project/commit/abcdef0123456",
                "delivery_directory": ".",
            }
        )
        first_request = self.call(
            {
                "method": "evaluations.accept",
                "token": declined_reviewer["token"],
                "slot_id": str(submission["slot_id"]),
            }
        )
        self.assertTrue(first_request["ok"], first_request)
        declined = self.call(
            {
                "method": "evaluations.request.respond",
                "token": owner["token"],
                "request_id": str(first_request["request_id"]),
                "decision": "decline",
            }
        )
        self.assertEqual("declined", declined["status"])
        repeated = self.call(
            {
                "method": "evaluations.accept",
                "token": declined_reviewer["token"],
                "slot_id": str(submission["slot_id"]),
            }
        )
        self.assertFalse(repeated["ok"])

        pending = self.call(
            {
                "method": "evaluations.accept",
                "token": pending_reviewer["token"],
                "slot_id": str(submission["slot_id"]),
            }
        )
        self.assertTrue(pending["ok"], pending)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                UPDATE evaluation_requests
                SET requested_at = datetime('now', '-25 hours')
                WHERE id = ?
                """,
                (pending["request_id"],),
            )
        expired_list = self.call(
            {"method": "evaluations.requests.list", "token": owner["token"]}
        )
        self.assertEqual([], expired_list["requests"])
        expired_response = self.call(
            {
                "method": "evaluations.request.respond",
                "token": owner["token"],
                "request_id": str(pending["request_id"]),
                "decision": "accept",
            }
        )
        self.assertFalse(expired_response["ok"])
        available = self.call(
            {
                "method": "evaluations.slots.list",
                "token": available_reviewer["token"],
            }
        )
        self.assertEqual(submission["slot_id"], available["slots"][0]["id"])

    def test_admin_api_authorization_and_management(self) -> None:
        student = self.create_user("managed-student")
        rejected = self.call(
            {"method": "admin.users.list", "token": student["token"]}
        )
        self.assertIn("Acesso administrativo", rejected["error"])

        admin = self.create_user("system-admin")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?", (admin["user_id"],)
            )
        admin_login = self.call(
            {
                "method": "auth.login",
                "username": "system-admin",
                "password": "secure-pass-123",
            }
        )
        self.assertTrue(admin_login["ok"])
        token = admin_login["token"]
        self_disable = self.call(
            {
                "method": "admin.user.state",
                "token": token,
                "user_id": str(admin["user_id"]),
                "is_active": "0",
            }
        )
        self.assertIn("própria conta", self_disable["error"])
        malformed_state = self.call(
            {
                "method": "admin.user.state",
                "token": token,
                "user_id": str(student["user_id"]),
                "is_active": [],
            }
        )
        self.assertIn("Estado da conta", malformed_state["error"])
        created = self.call(
            {
                "method": "admin.exercise.create",
                "token": token,
                "code": "ADMIN-01",
                "name": "Exercício administrativo",
                "description": "Descrição detalhada",
                "statement": "Missão completa",
                "example": "entrada\\nsaída",
                "delivery_directory": "ex00",
                "delivery_files": "main.c",
                "allowed_functions": "write",
                "terminal_model": "$ ./a.out",
                "hints": "Leia a missão",
            }
        )
        self.assertTrue(created["ok"], created)
        self.assertEqual("ADMIN-01", created["code"])
        edited = self.call(
            {
                "method": "admin.exercise.update",
                "token": token,
                "exercise_code": "ADMIN-01",
                "field": "description",
                "value": "Descrição atualizada",
            }
        )
        self.assertTrue(edited["ok"], edited)
        catalog = self.call({"method": "catalog.list", "token": student["token"]})
        self.assertIn("ADMIN-01", {item["code"] for item in catalog["exercises"]})
        admin_submissions = self.call(
            {"method": "admin.submissions.list", "token": token}
        )
        self.assertEqual([], admin_submissions["submissions"])
        admin_evaluations = self.call(
            {"method": "admin.evaluations.list", "token": token}
        )
        self.assertEqual([], admin_evaluations["evaluations"])

        archived = self.call(
            {
                "method": "admin.exercise.archive",
                "token": token,
                "exercise_code": "ADMIN-01",
            }
        )
        self.assertEqual("archived", archived["status"])
        reactivated = self.call(
            {
                "method": "admin.exercise.reactivate",
                "token": token,
                "exercise_code": "ADMIN-01",
            }
        )
        self.assertEqual("available", reactivated["status"])

        adjusted = self.call(
            {
                "method": "admin.points.adjust",
                "token": token,
                "user_id": str(student["user_id"]),
                "amount": "2",
                "reason": "Contribuição documentada",
            }
        )
        self.assertEqual(3, adjusted["points"])
        profile = self.call(
            {
                "method": "admin.user.get",
                "token": token,
                "user_id": str(student["user_id"]),
            }
        )
        self.assertEqual(3, profile["user"]["points"])
        point_history = self.call(
            {
                "method": "admin.points.history",
                "token": token,
                "user_id": str(student["user_id"]),
            }
        )
        self.assertIn(
            "Contribuição documentada",
            [entry["details"] for entry in point_history["entries"]],
        )
        report = self.call({"method": "admin.report", "token": token})
        self.assertEqual(2, report["report"]["users"])

        reset = self.call(
            {
                "method": "admin.password.reset",
                "token": token,
                "user_id": str(student["user_id"]),
                "password": "temporary-pass",
            }
        )
        self.assertTrue(reset["ok"])
        blocked_session = self.call(
            {"method": "profile.get", "token": student["token"]}
        )
        self.assertIn("Sessão inválida", blocked_session["error"])
        new_login = self.call(
            {
                "method": "auth.login",
                "username": "managed-student",
                "password": "temporary-pass",
            }
        )
        self.assertTrue(new_login["must_change_password"])

        disabled = self.call(
            {
                "method": "admin.user.state",
                "token": token,
                "user_id": str(student["user_id"]),
                "is_active": "0",
            }
        )
        self.assertTrue(disabled["ok"], disabled)
        self.assertFalse(disabled["is_active"])
        disabled_login = self.call(
            {
                "method": "auth.login",
                "username": "managed-student",
                "password": "temporary-pass",
            }
        )
        self.assertFalse(disabled_login["ok"])

    def test_uid_identity_binding_is_admin_managed_and_enforces_account_state(self) -> None:
        participant = self.create_user("linked-participant")
        denied = self.call(
            {
                "method": "admin.identity.bind",
                "token": participant["token"],
                "linux_uid": "230001",
                "user_id": str(participant["user_id"]),
            }
        )
        self.assertIn("Acesso administrativo", denied["error"])
        declared_uid = self.call(
            {
                "method": "profile.get",
                "token": participant["token"],
                "linux_uid": "230001",
            }
        )
        self.assertIn("Campos inválidos", declared_uid["error"])

        admin = self.create_user("identity-admin")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?", (admin["user_id"],)
            )
        admin_login = self.call(
            {
                "method": "auth.login",
                "username": "identity-admin",
                "password": "secure-pass-123",
            }
        )
        token = admin_login["token"]
        binding = {
            "method": "admin.identity.bind",
            "token": token,
            "linux_uid": "230001",
            "user_id": str(participant["user_id"]),
        }
        self.assertTrue(self.call(binding)["ok"])
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(
                participant["user_id"],
                Broker._resolve_linux_uid(connection, "230001"),
            )

        duplicate_uid = self.call(
            {
                **binding,
                "user_id": str(admin["user_id"]),
            }
        )
        self.assertIn("já possui vínculo", duplicate_uid["error"])
        duplicate_user = self.call(
            {
                **binding,
                "linux_uid": "230002",
            }
        )
        self.assertIn("já possui vínculo", duplicate_user["error"])
        service_uid = self.call({**binding, "linux_uid": str(os.geteuid())})
        self.assertIn("UID do serviço", service_uid["error"])

        listing = self.call({"method": "admin.identity.list", "token": token})
        self.assertEqual(1, len(listing["identities"]))
        self.assertEqual("linked-participant", listing["identities"][0]["username"])
        updated = self.call(
            {
                "method": "admin.identity.update",
                "token": token,
                "linux_uid": "230001",
                "new_linux_uid": "230002",
            }
        )
        self.assertTrue(updated["ok"], updated)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            with self.assertRaisesRegex(PermissionError, "sem vínculo ativo"):
                Broker._resolve_linux_uid(connection, "230001")
            self.assertEqual(
                participant["user_id"],
                Broker._resolve_linux_uid(connection, "230002"),
            )

        revoked = self.call(
            {
                "method": "admin.identity.revoke",
                "token": token,
                "linux_uid": "230002",
            }
        )
        self.assertTrue(revoked["ok"], revoked)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            with self.assertRaisesRegex(PermissionError, "sem vínculo ativo"):
                Broker._resolve_linux_uid(connection, "230002")

        rebound = self.call(
            {
                **binding,
                "linux_uid": "230002",
            }
        )
        self.assertTrue(rebound["ok"], rebound)
        disabled = self.call(
            {
                "method": "admin.user.state",
                "token": token,
                "user_id": str(participant["user_id"]),
                "is_active": "0",
            }
        )
        self.assertTrue(disabled["ok"])
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            with self.assertRaisesRegex(
                PermissionError, "conta SIMPLE vinculada está desativada"
            ):
                Broker._resolve_linux_uid(connection, "230002")
        rejected_binding = self.call(
            {
                **binding,
                "linux_uid": "230003",
            }
        )
        self.assertIn("desativada", rejected_binding["error"])

    def test_admin_tui_can_create_a_uid_identity_binding(self) -> None:
        participant = self.create_user("tui-linked-user")
        admin = self.create_user("tui-identity-admin")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?", (admin["user_id"],)
            )
        environment = os.environ.copy()
        environment.update(
            {"SIMPLE_MODE": "broker", "BROKER_SOCKET": str(self.socket_path)}
        )
        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\ntui-identity-admin\nsecure-pass-123\n"
                "5\n6\n1\n230100\n"
                f"{participant['user_id']}\ns\n0\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("VÍNCULOS DE IDENTIDADE LINUX", process.stdout)
        self.assertIn("Vínculo criado.", process.stdout)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            self.assertEqual(
                (participant["user_id"], 1),
                connection.execute(
                    "SELECT user_id, is_active FROM identity_bindings WHERE linux_uid = 230100"
                ).fetchone(),
            )

    def test_broker_tui_submissions_reviews_and_admin_dashboard(self) -> None:
        owner = self.create_user("tui-owner")
        reviewer = self.create_user("tui-reviewer")
        admin = self.create_user("tui-admin")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?", (admin["user_id"],)
            )
            connection.execute(
                """
                INSERT INTO exercises (code, name, description, statement)
                VALUES (?, ?, ?, ?)
                """,
                ("TUI-01", "Fluxo TUI", "Exercício de integração", "Resolva o exercício"),
            )
        environment = os.environ.copy()
        environment.update(
            {"SIMPLE_MODE": "broker", "BROKER_SOCKET": str(self.socket_path)}
        )
        submitted = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\ntui-owner\nsecure-pass-123\n"
                "1\n11\n1\n"
                "https://github.com/student/project/commit/abcdef0123456\n"
                "src\ns\n0\n5\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, submitted.returncode, submitted.stderr)
        self.assertIn("Solução enviada.", submitted.stdout)
        self.assertIn("Solução enviada. Slot ", submitted.stdout)
        self.assertNotIn("Acesso SQLite local bloqueado", submitted.stderr)

        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            slot_id = connection.execute(
                "SELECT id FROM evaluation_slots ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        requested = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\ntui-reviewer\nsecure-pass-123\n"
                f"2\n{slot_id}\ns\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, requested.returncode, requested.stderr)
        self.assertIn("Pedido enviado", requested.stdout)

        accepted = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\ntui-owner\nsecure-pass-123\n"
                "5\n1\n0\n1\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        self.assertIn("Pedido aceito", accepted.stdout)
        self.assertIn("MINHAS ENTREGAS", accepted.stdout)
        self.assertIn("Resolva o exercício", accepted.stdout)
        self.assertIn("AVALIAÇÃO", accepted.stdout)
        self.assertIn("Aguardando avaliação", accepted.stdout)

        reviewer_progress = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\ntui-reviewer\nsecure-pass-123\n"
                "3\n\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, reviewer_progress.returncode, reviewer_progress.stderr)
        self.assertIn("PEDIDOS ACEITOS PARA VOCÊ AVALIAR", reviewer_progress.stdout)
        self.assertIn("Pedido #", reviewer_progress.stdout)
        self.assertIn("Avaliações abertas", reviewer_progress.stdout)

        reviewed = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\ntui-reviewer\nsecure-pass-123\n"
                f"2\n{slot_id}\n1\n5\nBoa solução.\n.fim\ns\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, reviewed.returncode, reviewed.stderr)
        self.assertIn("Avaliação registrada: aprovado.", reviewed.stdout)

        owner_submission_detail = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\ntui-owner\nsecure-pass-123\n"
                "5\n1\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(
            0, owner_submission_detail.returncode, owner_submission_detail.stderr
        )
        self.assertIn("Boa solução.", owner_submission_detail.stdout)
        self.assertIn("5/5", owner_submission_detail.stdout)

        progress = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\ntui-reviewer\nsecure-pass-123\n"
                "3\n\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, progress.returncode, progress.stderr)
        self.assertIn("MEU PROGRESSO", progress.stdout)
        self.assertIn("review_completed", progress.stdout)

        admin_dashboard = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\ntui-admin\nsecure-pass-123\n"
                "5\n1\nc\n"
                "UI-NEW\nExercício via TUI\n"
                "Descrição completa\n.fim\n"
                "Missão completa\n.fim\n"
                "src\n"
                "main.c\n.fim\n"
                "write\n.fim\n"
                "entrada e saída\n.fim\n"
                "modelo terminal\n.fim\n"
                "dica\n.fim\n\n0\n2\n"
                f"{owner['user_id']}\n0\n0\n"
                "5\n\n0\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, admin_dashboard.returncode, admin_dashboard.stderr)
        self.assertIn("PERFIL DO USUÁRIO", admin_dashboard.stdout)
        self.assertIn("EXERCÍCIOS APROVADOS", admin_dashboard.stdout)
        self.assertIn(
            "TUI-01 - Fluxo TUI",
            admin_dashboard.stdout,
            admin_dashboard.stderr,
        )
        self.assertIn("Exercício UI-NEW cadastrado.", admin_dashboard.stdout)
        self.assertIn("VISÃO GERAL DO SISTEMA", admin_dashboard.stdout)
        self.assertNotIn("Acesso SQLite local bloqueado", admin_dashboard.stderr)

    def test_broker_mode_requires_temporary_password_change(self) -> None:
        user = self.create_user()
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET must_change_password = 1 WHERE id = ?",
                (user["user_id"],),
            )
        environment = os.environ.copy()
        environment.update(
            {
                "SIMPLE_MODE": "broker",
                "BROKER_SOCKET": str(self.socket_path),
            }
        )
        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\nstudent\nsecure-pass-123\n"
                "replacement-pass\nreplacement-pass\n"
                "0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("Senha atualizada.", process.stdout)
        with closing(sqlite3.connect(self.database_path)) as connection:
            must_change_password = connection.execute(
                "SELECT must_change_password FROM users WHERE username = 'student'"
            ).fetchone()[0]
        self.assertEqual(0, must_change_password)


if __name__ == "__main__":
    unittest.main()
