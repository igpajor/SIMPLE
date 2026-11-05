#!/usr/bin/env python3

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import sys
import struct
import subprocess
import termios
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
HOME_TUI = ROOT_DIR / "modules" / "home_tui.py"

sys.path.insert(0, str(ROOT_DIR))
from modules.home_tui import HomeTui


class HomeTuiTests(unittest.TestCase):
    def run_home(self, width: int, keys: list[bytes]) -> bytes:
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(
            slave_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", 24, width, 0, 0),
        )
        config = {
            "username": "student",
            "stats": [
                {"label": "Pontos", "value": "12"},
                {"label": "Aprovados", "value": "4"},
                {"label": "Avaliar", "value": "2"},
            ],
            "notification": "",
            "options": [
                {"key": str(index), "label": f"Opção {index}"}
                for index in range(1, 8)
            ]
            + [{"key": "0", "label": "Sair"}],
        }
        environment = os.environ.copy()
        environment.update(
            {
                "TERM": "xterm-256color",
                "HOME_TUI_CONFIG": json.dumps(config, ensure_ascii=False),
                "HOME_TUI_RESULT_FD": "1",
            }
        )
        process = subprocess.Popen(
            ["python3", str(HOME_TUI)],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=ROOT_DIR,
            env=environment,
            close_fds=True,
        )
        os.close(slave_fd)
        output = bytearray()

        def read_until(needle: bytes, timeout: float = 5.0) -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if needle in output:
                    return
                ready, _, _ = select.select([master_fd], [], [], 0.1)
                if ready:
                    try:
                        output.extend(os.read(master_fd, 8192))
                    except OSError:
                        break
                if process.poll() is not None:
                    break
            self.fail(f"Missing {needle!r} in PTY output: {output.decode(errors='replace')}")

        try:
            read_until("SIMPLE · INÍCIO".encode("utf-8"))
            for key in keys:
                os.write(master_fd, key)
                time.sleep(0.12)
            process.wait(timeout=5)
            while select.select([master_fd], [], [], 0.1)[0]:
                try:
                    output.extend(os.read(master_fd, 8192))
                except OSError:
                    break
            self.assertEqual(0, process.returncode, output.decode(errors="replace"))
            return bytes(output)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            os.close(master_fd)

    def test_arrow_keys_and_enter_select_home_option(self) -> None:
        output = self.run_home(80, [b"\x1bOB"] * 5 + [b"\r"])
        self.assertIn(b"MENU PRINCIPAL", output)
        self.assertIn(b"SIMPLE", output)
        self.assertIn(b"SEU RESUMO", output)
        self.assertIn(b"Pontos 12", output)
        self.assertIn(b"Aprovados 4", output)
        self.assertIn(b"Avaliar 2", output)
        self.assertTrue(output.endswith(b"6\r\n"), output[-100:])

    def test_narrow_terminal_keeps_home_navigable(self) -> None:
        output = self.run_home(48, [b"0"])
        self.assertNotIn(b"Aumente o terminal", output)
        self.assertTrue(output.endswith(b"0\r\n"), output[-100:])

    def test_wide_terminal_expands_home_to_chat_panel_width(self) -> None:
        tui = HomeTui(
            {
                "username": "student",
                "stats": [{"label": "Pontos", "value": "12"}],
                "options": [
                    {"key": "1", "label": "Exercícios"},
                    {"key": "0", "label": "Sair"},
                ],
            }
        )
        screen = Mock()
        screen.getmaxyx.return_value = (24, 140)

        with patch.multiple(
            "modules.home_tui.curses",
            create=True,
            ACS_ULCORNER=0,
            ACS_URCORNER=0,
            ACS_LLCORNER=0,
            ACS_LRCORNER=0,
            ACS_HLINE=0,
            ACS_VLINE=0,
        ):
            tui.draw(screen)

        outer_border = next(
            call for call in screen.hline.call_args_list if call.args[3] == 108
        )
        self.assertEqual(16, outer_border.args[1])

    def test_tall_terminal_expands_home_to_chat_panel_height(self) -> None:
        tui = HomeTui(
            {
                "username": "student",
                "stats": [{"label": "Pontos", "value": "12"}],
                "options": [
                    {"key": str(index), "label": f"Opção {index}"}
                    for index in range(1, 8)
                ]
                + [{"key": "0", "label": "Sair"}],
            }
        )
        screen = Mock()
        screen.getmaxyx.return_value = (40, 100)

        with patch.multiple(
            "modules.home_tui.curses",
            create=True,
            ACS_ULCORNER=0,
            ACS_URCORNER=0,
            ACS_LLCORNER=0,
            ACS_LRCORNER=0,
            ACS_HLINE=0,
            ACS_VLINE=0,
        ):
            tui.draw(screen)

        outer_side = next(
            call for call in screen.vline.call_args_list if call.args[3] == 30
        )
        self.assertEqual(5, outer_side.args[0])


if __name__ == "__main__":
    unittest.main()
