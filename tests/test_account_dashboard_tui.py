from __future__ import annotations

import curses
import json
import unittest
from unittest.mock import Mock, patch

from modules.account_dashboard_tui import AccountDashboardTui, load_config


def submission(
    submission_id: int = 1,
    status: str = "pending",
    slot_id: int = 10,
) -> dict[str, object]:
    return {
        "id": submission_id,
        "exercise_code": f"EX-{submission_id:02}",
        "exercise_name": f"Exercício {submission_id}",
        "status": status,
        "slot_id": slot_id,
        "score": None,
        "feedback": "",
    }


def dashboard_config() -> dict[str, object]:
    return {
        "username": "student",
        "display_name": "Ana Silva",
        "github_username": "ana",
        "bio": "Estudante SIMPLE",
        "points": 12,
        "counts": {"pending": 1, "approved": 1, "rejected": 0},
        "submissions": [submission()],
        "requests": [],
        "points_history": [
            {
                "amount": 1,
                "entry_type": "exercise_approved",
                "details": "EX-01",
                "created_at": "2026-11-05",
            }
        ],
    }


class AccountDashboardTuiTests(unittest.TestCase):
    def run_tui(
        self, config: dict[str, object], keys: list[object]
    ) -> tuple[AccountDashboardTui, dict[str, object]]:
        tui = AccountDashboardTui(config)
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.get_wch.side_effect = keys
        with (
            patch(
                "modules.account_dashboard_tui.curses.has_colors",
                return_value=False,
            ),
            patch("modules.account_dashboard_tui.curses.cbreak"),
            patch("modules.account_dashboard_tui.curses.noecho"),
            patch.multiple(
                "modules.account_dashboard_tui.curses",
                create=True,
                ACS_ULCORNER=0,
                ACS_URCORNER=0,
                ACS_LLCORNER=0,
                ACS_LRCORNER=0,
                ACS_HLINE=0,
                ACS_VLINE=0,
            ),
        ):
            result = tui.run(screen)
        return tui, result

    def test_selected_submission_is_preserved_and_opened(self) -> None:
        config = dashboard_config()
        config["submissions"] = [submission(1), submission(2)]
        config["selected_submission_id"] = 2
        _tui, result = self.run_tui(config, ["\n"])
        self.assertEqual(
            {"action": "open_submission", "submission_id": 2},
            result,
        )

    def test_pending_request_can_be_accepted_from_submission_detail(self) -> None:
        config = dashboard_config()
        config["detail"] = submission()
        config["requests"] = [
            {
                "request_id": 42,
                "slot_id": 10,
                "reviewer": "reviewer",
                "status": "pending",
            }
        ]
        tui, result = self.run_tui(config, ["a"])
        self.assertEqual(
            {"action": "respond_request", "request_id": 42, "decision": "accept"},
            result,
        )
        self.assertIn(
            "A: aceitar pedido",
            "\n".join(line for line, _ in tui.detail_lines()),
        )

    def test_accepted_request_opens_evaluation_room_from_detail(self) -> None:
        config = dashboard_config()
        config["detail"] = submission()
        config["requests"] = [
            {
                "request_id": 42,
                "slot_id": 10,
                "reviewer": "reviewer",
                "status": "accepted",
            }
        ]
        _tui, result = self.run_tui(config, ["m"])
        self.assertEqual({"action": "open_room", "slot_id": 10}, result)

    def test_profile_editor_returns_all_profile_fields(self) -> None:
        _tui, result = self.run_tui(
            dashboard_config(),
            ["e", "\n", *["\x7f"] * 9, "N", "o", "v", "o", "\n", "s"],
        )
        self.assertEqual(
            {
                "action": "update_profile",
                "display_name": "Novo",
                "github_username": "ana",
                "bio": "Estudante SIMPLE",
            },
            result,
        )

    def test_f3_history_can_scroll_and_return_without_losing_dashboard(self) -> None:
        config = dashboard_config()
        config["points_history"] = [
            {
                "amount": index,
                "entry_type": "test",
                "details": f"entry {index}",
                "created_at": "today",
            }
            for index in range(20)
        ]
        tui = AccountDashboardTui(config)
        screen = Mock()
        screen.getmaxyx.return_value = (12, 72)
        screen.get_wch.side_effect = [
            curses.KEY_F3,
            curses.KEY_DOWN,
            curses.KEY_F3,
            "\x1b",
        ]
        with (
            patch(
                "modules.account_dashboard_tui.curses.has_colors",
                return_value=False,
            ),
            patch("modules.account_dashboard_tui.curses.cbreak"),
            patch("modules.account_dashboard_tui.curses.noecho"),
            patch.multiple(
                "modules.account_dashboard_tui.curses",
                create=True,
                ACS_ULCORNER=0,
                ACS_URCORNER=0,
                ACS_LLCORNER=0,
                ACS_LRCORNER=0,
                ACS_HLINE=0,
                ACS_VLINE=0,
            ),
        ):
            result = tui.run(screen)
        self.assertEqual("exit", result["action"])
        self.assertEqual(1, tui.points_scroll)

    def test_progress_overlay_preserves_notifications_and_review_reply_action(
        self,
    ) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "review_received",
                "exercise_code": "EX-01",
                "other_user": "reviewer",
                "score": 5,
                "feedback": "Ótimo",
                "created_at": "today",
            }
        ]
        config["review_history"] = [
            {
                "exercise_code": "EX-01",
                "relation": "received",
                "reviewer": "reviewer",
                "score": 5,
                "feedback": "Ótimo",
                "created_at": "today",
            }
        ]
        config["pending_review_responses"] = [
            {"review_id": 27, "exercise_code": "EX-01", "reviewer": "reviewer"}
        ]
        tui, result = self.run_tui(config, ["p"])
        self.assertEqual({"action": "show_progress"}, result)
        lines = "\n".join(line for line, _ in tui.progress_lines())
        self.assertIn("Nova avaliação", lines)
        self.assertIn("HISTÓRICO DE AVALIAÇÕES", lines)
        self.assertIn("RESPONDER A UMA AVALIAÇÃO", lines)

        config["open_progress"] = True
        _tui, result = self.run_tui(config, ["r"])
        self.assertEqual({"action": "respond_review", "review_id": 27}, result)

    def test_too_narrow_terminal_shows_resize_guidance(self) -> None:
        tui = AccountDashboardTui(dashboard_config())
        screen = Mock()
        screen.getmaxyx.return_value = (24, 35)
        tui.draw(screen)
        self.assertTrue(
            any(
                "Aumente o terminal" in call.args[2]
                for call in screen.addnstr.call_args_list
            )
        )

    def test_submission_detail_includes_reviewer_score_and_feedback(self) -> None:
        config = dashboard_config()
        config["detail"] = {
            **submission(status="approved"),
            "reviewer": "reviewer",
            "score": 5,
            "feedback": "Muito bom",
            "exercise": {"description": "Resumo"},
        }
        tui = AccountDashboardTui(config)
        lines = "\n".join(line for line, _ in tui.detail_lines())
        self.assertIn("Avaliador: reviewer", lines)
        self.assertIn("Nota: 5/5", lines)
        self.assertIn("Feedback: Muito bom", lines)
        self.assertIn("Resumo: Resumo", lines)

    def test_config_rejects_unknown_submission_status(self) -> None:
        config = dashboard_config()
        config["submissions"] = [submission(status="other")]
        with self.assertRaisesRegex(ValueError, "Entrega inválida"):
            with patch.dict(
                "os.environ",
                {"ACCOUNT_DASHBOARD_CONFIG": json.dumps(config)},
            ):
                load_config()


if __name__ == "__main__":
    unittest.main()
