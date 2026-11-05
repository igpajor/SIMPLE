from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import termios
import time
import unittest
from pathlib import Path

from modules.exercise_catalog_tui import ExerciseCatalogTui


ROOT_DIR = Path(__file__).resolve().parents[1]
CATALOG_TUI = ROOT_DIR / "modules" / "exercise_catalog_tui.py"


def catalog_item(
    code: str, name: str, status: str, description: str = ""
) -> dict[str, str]:
    return {
        "code": code,
        "name": name,
        "description": description,
        "statement": f"Missão {name}",
        "example": "",
        "delivery_directory": "",
        "delivery_files": "",
        "allowed_functions": "",
        "terminal_model": "",
        "hints": "",
        "status": status,
    }


class ExerciseCatalogTuiTests(unittest.TestCase):
    def test_filters_and_search_keep_only_matching_exercises(self) -> None:
        tui = ExerciseCatalogTui(
            {
                "username": "student",
                "points": 1,
                "items": [
                    catalog_item("EX-01", "Primeiro", "available", "arquivos"),
                    catalog_item("EX-02", "Segundo", "pending", "texto"),
                    catalog_item("EX-03", "Terceiro", "approved", "arquivos"),
                    catalog_item("EX-04", "Quarto", "no_points", "texto"),
                ],
            }
        )

        tui.change_filter("pending")
        self.assertEqual(["EX-02"], [item["code"] for item in tui.filtered_items])
        tui.change_filter("all")
        tui.query = "ARQUIVOS"
        tui.refresh_items()
        self.assertEqual(
            ["EX-01", "EX-03"],
            [item["code"] for item in tui.filtered_items],
        )

    def test_detail_actions_only_offer_delivery_when_eligible(self) -> None:
        tui = ExerciseCatalogTui(
            {
                "username": "student",
                "points": 1,
                "items": [
                    catalog_item("EX-01", "Disponível", "available"),
                    catalog_item("EX-02", "Pendente", "pending"),
                ],
            }
        )
        tui.open_detail(tui.items[0])
        self.assertEqual(
            ["submit", "chat", "back"],
            [action for action, _label in tui.actions()],
        )
        tui.open_detail(tui.items[1])
        self.assertEqual(
            ["chat", "back"],
            [action for action, _label in tui.actions()],
        )

    def test_enter_on_catalog_then_details_returns_submit_action(self) -> None:
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        config = {
            "username": "student",
            "points": 1,
            "items": [
                catalog_item(
                    "EX-01",
                    "Primeiro exercício",
                    "available",
                    "Descrição do exercício",
                )
            ],
        }
        environment = os.environ.copy()
        environment.update(
            {
                "TERM": "xterm-256color",
                "EXERCISE_CATALOG_CONFIG": json.dumps(config, ensure_ascii=False),
                "EXERCISE_CATALOG_RESULT_FD": "1",
            }
        )
        process = subprocess.Popen(
            ["python3", str(CATALOG_TUI)],
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
            read_until("CATÁLOGO DE EXERCÍCIOS".encode("utf-8"))
            os.write(master_fd, b"\r")
            read_until(b"DETALHES")
            self.assertIn("Descrição do exercício".encode("utf-8"), output)
            os.write(master_fd, b"\r")
            process.wait(timeout=5)
            while select.select([master_fd], [], [], 0.1)[0]:
                try:
                    output.extend(os.read(master_fd, 8192))
                except OSError:
                    break
            self.assertEqual(0, process.returncode, output.decode(errors="replace"))
            self.assertIn(b"submit\tEX-01\tall\t\tEX-01\n", output)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            os.close(master_fd)


if __name__ == "__main__":
    unittest.main()
