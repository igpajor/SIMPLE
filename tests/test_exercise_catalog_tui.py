from __future__ import annotations

import curses
import fcntl
import io
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from modules.exercise_catalog_tui import (
    ExerciseCatalogTui,
    format_pdf_text,
    load_config,
)


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
        "pdf_url": "",
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
            ["submit", "pdf", "chat", "back"],
            [action for action, _label in tui.actions()],
        )
        tui.open_detail(tui.items[1])
        self.assertEqual(
            ["pdf", "chat", "back"],
            [action for action, _label in tui.actions()],
        )
        self.assertIn(
            "PDF não cadastrado.",
            "\n".join(line for line, _color in tui.detail_lines),
        )
        self.assertEqual(
            "PDF original indisponível",
            tui.actions()[0][1],
        )

    def test_pdf_url_is_shown_and_reader_action_is_available(self) -> None:
        item = catalog_item("EX-01", "Com PDF", "available")
        item["pdf_url"] = "https://example.org/exercise.pdf"
        tui = ExerciseCatalogTui(
            {"username": "student", "points": 1, "items": [item]}
        )
        tui.open_detail(item)
        self.assertIn(("pdf", "Ler PDF original"), tui.actions())
        self.assertIn(
            ("https://example.org/exercise.pdf", "text"),
            tui.detail_lines,
        )

    def test_pdf_reader_formats_extracted_text(self) -> None:
        self.assertEqual(
            "Título\nUma linha com espaços\n\nOutro parágrafo",
            format_pdf_text("  Título   \nUma   linha\tcom espaços \n\n\nOutro parágrafo  \n"),
        )

    def test_pdf_reader_displays_original_link(self) -> None:
        url = "https://drive.google.com/file/d/1TmHY5cXXz78pbdzOanDy47_P_pqXrVwz/view"
        item = catalog_item("EX-01", "Com PDF", "available")
        item["pdf_url"] = url
        tui = ExerciseCatalogTui(
            {"username": "student", "points": 1, "items": [item]}
        )
        tui.open_detail(item)
        screen = Mock()
        screen.getmaxyx.return_value = (32, 100)

        with patch.multiple(
            "modules.exercise_catalog_tui.curses",
            create=True,
            ACS_ULCORNER=0,
            ACS_URCORNER=0,
            ACS_LLCORNER=0,
            ACS_LRCORNER=0,
            ACS_HLINE=0,
            ACS_VLINE=0,
        ):
            tui.draw_pdf_page(screen, ["Conteúdo formatado"])

        rendered_text = "\n".join(
            call.args[2]
            for call in screen.addstr.call_args_list
            if len(call.args) > 2
        )
        self.assertIn("PDF:", rendered_text)
        self.assertIn("drive.google.com/file/d/", rendered_text)
        self.assertIn("Conteúdo formatado", rendered_text)

    def test_pdf_reader_extracts_text_from_https_pdf(self) -> None:
        class FakeReader:
            is_encrypted = False
            pages = [
                types.SimpleNamespace(
                    extract_text=lambda **kwargs: (
                        "Texto da página"
                        if kwargs == {"extraction_mode": "layout"}
                        else ""
                    )
                )
            ]

            def __init__(self, _file: io.BytesIO, strict: bool) -> None:
                self.asserted_strict = strict

        pypdf_module = types.ModuleType("pypdf")
        pypdf_module.__path__ = []
        pypdf_module.PdfReader = FakeReader
        errors_module = types.ModuleType("pypdf.errors")
        errors_module.PyPdfError = type("PyPdfError", (Exception,), {})
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = "https://example.org/exercise.pdf"
        response.headers = {}
        response.read.side_effect = [b"%PDF-1.7 sample", b""]
        with (
            patch(
                "modules.exercise_catalog_tui.build_opener",
                return_value=Mock(open=Mock(return_value=response)),
            ),
            patch.dict(
                sys.modules,
                {"pypdf": pypdf_module, "pypdf.errors": errors_module},
            ),
        ):
            self.assertEqual(
                ["Texto da página"],
                ExerciseCatalogTui.fetch_pdf_pages(
                    "https://example.org/exercise.pdf"
                ),
            )

    def test_google_drive_share_link_downloads_pdf_after_confirmation(self) -> None:
        class FakeReader:
            is_encrypted = False
            pages = [
                types.SimpleNamespace(
                    extract_text=lambda **kwargs: (
                        "PDF do Drive"
                        if kwargs == {"extraction_mode": "layout"}
                        else ""
                    )
                )
            ]

            def __init__(self, _file: io.BytesIO, strict: bool) -> None:
                self.asserted_strict = strict

        pypdf_module = types.ModuleType("pypdf")
        pypdf_module.__path__ = []
        pypdf_module.PdfReader = FakeReader
        errors_module = types.ModuleType("pypdf.errors")
        errors_module.PyPdfError = type("PyPdfError", (Exception,), {})
        confirmation = Mock()
        confirmation.__enter__ = Mock(return_value=confirmation)
        confirmation.__exit__ = Mock(return_value=False)
        confirmation.geturl.return_value = "https://drive.google.com/uc?export=download&id=1TmHY5cXXz78pbdzOanDy47_P_pqXrVwz"
        confirmation.headers = {}
        confirmation.read.side_effect = [
            b'<form action="https://drive.usercontent.google.com/download">'
            b'<input type="hidden" name="id" value="1TmHY5cXXz78pbdzOanDy47_P_pqXrVwz">'
            b'<input type="hidden" name="export" value="download">'
            b'<input type="hidden" name="confirm" value="t">'
            b"</form>",
            b"",
        ]
        pdf_response = Mock()
        pdf_response.__enter__ = Mock(return_value=pdf_response)
        pdf_response.__exit__ = Mock(return_value=False)
        pdf_response.geturl.return_value = (
            "https://drive.usercontent.google.com/download?id="
            "1TmHY5cXXz78pbdzOanDy47_P_pqXrVwz&export=download&confirm=t"
        )
        pdf_response.headers = {}
        pdf_response.read.side_effect = [b"%PDF-1.7 sample", b""]
        opener = Mock()
        opener.open.side_effect = [confirmation, pdf_response]
        with (
            patch("modules.exercise_catalog_tui.build_opener", return_value=opener),
            patch.dict(
                sys.modules,
                {"pypdf": pypdf_module, "pypdf.errors": errors_module},
            ),
        ):
            pages = ExerciseCatalogTui.fetch_pdf_pages(
                "https://drive.google.com/file/d/"
                "1TmHY5cXXz78pbdzOanDy47_P_pqXrVwz/view?usp=drive_link"
            )

        self.assertEqual(["PDF do Drive"], pages)
        self.assertIn(
            "https://drive.google.com/uc?export=download&id="
            "1TmHY5cXXz78pbdzOanDy47_P_pqXrVwz",
            opener.open.call_args_list[0].args[0].full_url,
        )
        self.assertIn("confirm=t", opener.open.call_args_list[1].args[0].full_url)

    def test_pdf_reader_rejects_non_https_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            ExerciseCatalogTui.fetch_pdf_pages("http://example.org/exercise.pdf")

    def test_catalog_accepts_broker_response_without_pdf_url(self) -> None:
        config = {
            "username": "student",
            "points": 1,
            "items": [catalog_item("EX-01", "Legado", "available")],
        }
        with patch.dict(
            os.environ,
            {"EXERCISE_CATALOG_CONFIG": json.dumps(config)},
        ):
            loaded = load_config()
        self.assertEqual("", loaded["items"][0]["pdf_url"])

    def test_catalog_entries_stay_inside_the_inner_frame(self) -> None:
        tui = ExerciseCatalogTui(
            {
                "username": "student",
                "points": 1,
                "items": [
                    catalog_item(f"EX-{index:02}", f"Exercício {index}", "available")
                    for index in range(50)
                ],
            }
        )
        tui.selection = len(tui.items) - 1
        tui.selected_code = tui.items[-1]["code"]
        screen = Mock()
        screen.getmaxyx.return_value = (40, 140)

        with patch.multiple(
            "modules.exercise_catalog_tui.curses",
            create=True,
            ACS_ULCORNER=0,
            ACS_URCORNER=0,
            ACS_LLCORNER=0,
            ACS_LRCORNER=0,
            ACS_HLINE=0,
            ACS_VLINE=0,
        ):
            tui.draw(screen)

        frame_top = next(
            call.args[0]
            for call in screen.hline.call_args_list
            if call.args[3] == 104
        )
        list_top = frame_top
        list_bottom = frame_top + 21
        exercise_rows = [
            call.args[0]
            for call in screen.addstr.call_args_list
            if len(call.args) > 2 and "EX-" in call.args[2]
        ]
        self.assertTrue(exercise_rows)
        self.assertGreaterEqual(min(exercise_rows), list_top + 1)
        self.assertLessEqual(max(exercise_rows), list_bottom - 1)

    def test_detail_actions_are_below_the_details_frame(self) -> None:
        tui = ExerciseCatalogTui(
            {
                "username": "student",
                "points": 1,
                "items": [catalog_item("EX-01", "Exercício", "available", "Missão")],
            }
        )
        tui.open_detail(tui.items[0])
        screen = Mock()

        with patch.multiple(
            "modules.exercise_catalog_tui.curses",
            create=True,
            ACS_ULCORNER=0,
            ACS_URCORNER=0,
            ACS_LLCORNER=0,
            ACS_LRCORNER=0,
            ACS_HLINE=0,
            ACS_VLINE=0,
        ):
            tui.height = 32
            tui.width = 100
            tui.draw_detail(screen, 0, 0, 100, 32)

        actions_row = next(
            call.args[0]
            for call in screen.addstr.call_args_list
            if len(call.args) > 2 and "AÇÕES" in call.args[2]
        )
        detail_bottom = max(
            call.args[0]
            for call in screen.hline.call_args_list
            if call.args[3] == 94
        )
        self.assertGreater(actions_row, detail_bottom)

    def test_delivery_fields_accept_text_and_cycle_focus(self) -> None:
        tui = ExerciseCatalogTui({"username": "student", "points": 1, "items": []})
        for character in "https://github.com/user/repo/commit/abcdef0":
            tui.edit_form_field(character)
        tui.move_form_focus(1)
        for character in "ex00":
            tui.edit_form_field(character)
        self.assertEqual("ex00", tui.delivery_directory)
        tui.move_form_focus(-1)
        tui.edit_form_field(curses.KEY_END)
        tui.edit_form_field(curses.KEY_BACKSPACE)
        self.assertEqual("https://github.com/user/repo/commit/abcdef", tui.commit_url)
        tui.move_form_focus(1)
        tui.move_form_focus(1)
        self.assertEqual(2, tui.form_focus)

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

        def drain_output(duration: float = 0.2) -> None:
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master_fd], [], [], 0.03)
                if ready:
                    try:
                        output.extend(os.read(master_fd, 8192))
                    except OSError:
                        break

        try:
            read_until("CATÁLOGO DE EXERCÍCIOS".encode("utf-8"))
            self.assertIn(b"lqq", output)
            os.write(master_fd, b"1")
            drain_output()
            self.assertNotIn(b"DETALHES", output)
            self.assertNotIn(b"1-9 abrir", output)
            os.write(master_fd, b"\r")
            read_until(b"DETALHES")
            drain_output()
            self.assertIn("Descrição do exercício".encode("utf-8"), output)
            os.write(master_fd, b"\r")
            read_until(b"REGISTRAR ENTREGA")
            for char in "https://github.com/user/repo/commit/abcdef0":
                os.write(master_fd, char.encode("utf-8"))
            os.write(master_fd, b"\t")
            for char in "ex00":
                os.write(master_fd, char.encode("utf-8"))
            os.write(master_fd, b"\t\r")
            process.wait(timeout=5)
            while select.select([master_fd], [], [], 0.1)[0]:
                try:
                    output.extend(os.read(master_fd, 8192))
                except OSError:
                    break
            self.assertEqual(0, process.returncode, output.decode(errors="replace"))
            self.assertIn(
                b"submit\tEX-01\tall\t\tEX-01\thttps://github.com/user/repo/commit/abcdef0\tex00\n",
                output,
            )
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            os.close(master_fd)


if __name__ == "__main__":
    unittest.main()
