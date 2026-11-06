from __future__ import annotations

import curses
import json
import sys
import unittest
from unittest.mock import Mock, patch

from modules.account_dashboard_tui import (
    AccountDashboardTui,
    build_notifications,
    load_config,
)


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
    def draw_dashboard_at(
        self,
        width: int,
        config: dict[str, object] | None = None,
        height: int = 24,
    ) -> tuple[AccountDashboardTui, Mock]:
        tui = AccountDashboardTui(config or dashboard_config())
        screen = Mock()
        screen.getmaxyx.return_value = (height, width)
        with patch.multiple(
            "modules.account_dashboard_tui.curses",
            create=True,
            ACS_ULCORNER=0,
            ACS_URCORNER=0,
            ACS_LLCORNER=0,
            ACS_LRCORNER=0,
            ACS_HLINE=0,
            ACS_VLINE=0,
        ):
            tui.draw(screen)
        return tui, screen

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

    def test_dashboard_draws_block_initials_profile_metrics_and_notifications(
        self,
    ) -> None:
        config = dashboard_config()
        config["display_name"] = "Ána Silva"
        config["notifications"] = [
            {
                "type": "review_received",
                "exercise_code": "EX-01",
                "other_user": "reviewer",
            }
        ]
        tui, screen = self.draw_dashboard_at(110, config)
        rendered = [call.args[2] for call in screen.addnstr.call_args_list]
        self.assertEqual("AS", tui.profile_initials())
        self.assertTrue(any("█" in text for text in rendered))
        self.assertTrue(any("MEU CADASTRO" in text for text in rendered))
        self.assertIn("Aprovadas: 1", rendered)
        self.assertIn("Reprovadas: 0", rendered)
        self.assertIn("Em avaliação: 1", rendered)
        self.assertTrue(any("Avaliação recebida" in text for text in rendered))

    def test_dashboard_places_notifications_beside_deliveries_when_wide(self) -> None:
        _tui, screen = self.draw_dashboard_at(110)
        panel_titles = {
            call.args[2].strip(): call.args[0]
            for call in screen.addnstr.call_args_list
            if "MINHAS ENTREGAS" in call.args[2] or "NOTIFICAÇÕES" in call.args[2]
        }
        self.assertEqual(
            panel_titles["MINHAS ENTREGAS"],
            panel_titles["NOTIFICAÇÕES · 0"],
        )

    def test_dashboard_stacks_notifications_below_deliveries_when_narrow(self) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "review_received",
                "exercise_code": "EX-01",
                "other_user": "reviewer",
            }
        ]
        _tui, screen = self.draw_dashboard_at(50, config)
        panel_titles = {
            call.args[2].strip(): call.args[0]
            for call in screen.addnstr.call_args_list
            if "MINHAS ENTREGAS" in call.args[2] or "NOTIFICAÇÕES" in call.args[2]
        }
        self.assertGreater(
            panel_titles["NOTIFICAÇÕES · 1"],
            panel_titles["MINHAS ENTREGAS"],
        )
        self.assertTrue(
            any(
                "EX-01 · Avaliação recebida" in call.args[2]
                for call in screen.addnstr.call_args_list
            )
        )

    def test_dashboard_requests_more_height_before_drawing_panels(self) -> None:
        _tui, screen = self.draw_dashboard_at(50, height=18)
        rendered = [call.args[2] for call in screen.addnstr.call_args_list]
        self.assertTrue(any("Aumente a altura" in text for text in rendered))
        self.assertFalse(any("PERFIL" in text for text in rendered))

    def test_f2_opens_and_closes_latest_notification_popup(self) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "review_received",
                "category": "review_received",
                "exercise_code": "EX-01",
                "created_at": "today",
                "score": 4,
                "feedback": "Bom trabalho",
                "submission_id": 1,
            }
        ]
        tui, result = self.run_tui(config, [curses.KEY_F2, "\x1b", "\x1b"])
        self.assertEqual("exit", result["action"])
        self.assertFalse(tui.notification_popup)
        tui.notification_popup = True
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        with patch.multiple(
            "modules.account_dashboard_tui.curses",
            create=True,
            ACS_ULCORNER=0,
            ACS_URCORNER=0,
            ACS_LLCORNER=0,
            ACS_LRCORNER=0,
            ACS_HLINE=0,
            ACS_VLINE=0,
        ):
            tui.draw(screen)
        fill_calls = [
            call
            for call in screen.addnstr.call_args_list
            if call.args[2].strip() == ""
        ]
        self.assertTrue(
            any(
                "ÚLTIMA NOTIFICAÇÃO" in call.args[2]
                for call in screen.addnstr.call_args_list
            )
        )
        self.assertTrue(fill_calls)
        self.assertTrue(all(call.args[4] == curses.A_REVERSE for call in fill_calls))

    def test_f2_notification_accepts_pending_evaluation_request(self) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "evaluation_request",
                "category": "evaluation_request",
                "request_id": 42,
                "slot_id": 10,
                "submission_id": 1,
                "exercise_code": "EX-01",
                "created_at": "today",
            }
        ]
        _tui, result = self.run_tui(config, ["a"])
        self.assertEqual(
            {
                "action": "respond_request",
                "request_id": 42,
                "decision": "accept",
            },
            result,
        )

    def test_f2_notification_opens_direct_message(self) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "chat_message",
                "category": "chat_message",
                "channel": "dm:1:2",
                "room_name": "DM · reviewer",
                "created_at": "today",
                "feedback": "Oi!",
            }
        ]
        _tui, result = self.run_tui(config, [curses.KEY_F2, "\n"])
        self.assertEqual(
            {
                "action": "open_notification_room",
                "channel": "dm:1:2",
                "room_name": "DM · reviewer",
            },
            result,
        )

    def test_f2_notification_opens_accepted_evaluation_room(self) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "evaluation_accepted",
                "category": "evaluation_accepted",
                "slot_id": 10,
                "exercise_code": "EX-01",
                "created_at": "today",
            }
        ]
        _tui, result = self.run_tui(config, [curses.KEY_F2, "1"])
        self.assertEqual({"action": "open_room", "slot_id": 10}, result)

    def test_f2_accepted_evaluation_can_open_review_submission(self) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "evaluation_accepted",
                "category": "evaluation_accepted",
                "slot_id": 10,
                "exercise_code": "EX-01",
                "created_at": "today",
            }
        ]
        tui = AccountDashboardTui(config)
        lines = "\n".join(line for line, _ in tui.notification_lines(
            tui.notifications[0]
        ))
        self.assertIn("[1] Abrir sala de áudio e vídeo", lines)
        self.assertIn("[2] Avaliar submissão", lines)
        self.assertIn("[3] Liberar minha reserva", lines)
        self.assertIn("[0] Voltar", lines)
        _tui, result = self.run_tui(config, [curses.KEY_F2, "2"])
        self.assertEqual({"action": "review_submission", "slot_id": 10}, result)

    def test_f2_accepted_evaluation_can_release_reservation(self) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "evaluation_accepted",
                "category": "evaluation_accepted",
                "slot_id": 10,
                "exercise_code": "EX-01",
                "created_at": "today",
            }
        ]
        _tui, result = self.run_tui(config, [curses.KEY_F2, "3"])
        self.assertEqual(
            {"action": "release_reservation", "slot_id": 10},
            result,
        )

    def test_pending_evaluation_request_opens_popup_on_entry(self) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "evaluation_request",
                "request_id": 42,
                "slot_id": 10,
                "exercise_code": "EX-01",
                "created_at": "today",
            }
        ]
        tui = AccountDashboardTui(config)
        self.assertTrue(tui.notification_popup)
        self.assertEqual(42, tui.active_notification()["request_id"])

    def test_live_poll_adds_new_request_and_opens_popup(self) -> None:
        config = dashboard_config()
        tui = AccountDashboardTui(config)
        responses = {
            "progress.get": {
                "notifications": [],
                "accepted_evaluation_requests": [],
            },
            "submissions.list": {
                "submissions": [{**submission(), "id": 7}],
            },
            "evaluations.requests.list": {
                "requests": [
                    {
                        "request_id": 42,
                        "slot_id": 10,
                        "reviewer": "reviewer",
                        "exercise_code": "EX-01",
                        "status": "pending",
                        "requested_at": "now",
                    }
                ]
            },
            "chat.dashboard": {"unread_rooms": []},
        }
        with (
            patch.object(
                tui,
                "broker_request",
                side_effect=lambda method: responses[method],
            ),
            patch(
                "modules.account_dashboard_tui.time.monotonic",
                return_value=tui.last_notification_poll + 3,
            ),
        ):
            tui.poll_notifications()
        self.assertTrue(tui.notification_popup)
        self.assertEqual(
            "evaluation_request",
            tui.active_notification()["category"],
        )

    def test_dashboard_timeout_polls_and_surfaces_new_request(self) -> None:
        tui = AccountDashboardTui(dashboard_config())
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.get_wch.side_effect = [curses.error, "a"]
        responses = {
            "progress.get": {
                "notifications": [],
                "accepted_evaluation_requests": [],
            },
            "submissions.list": {
                "submissions": [{**submission(), "id": 7}],
            },
            "evaluations.requests.list": {
                "requests": [
                    {
                        "request_id": 42,
                        "slot_id": 10,
                        "reviewer": "reviewer",
                        "exercise_code": "EX-01",
                        "status": "pending",
                        "requested_at": "now",
                    }
                ]
            },
            "chat.dashboard": {"unread_rooms": []},
        }
        with (
            patch.object(
                tui,
                "broker_request",
                side_effect=lambda method: responses[method],
            ),
            patch(
                "modules.account_dashboard_tui.time.monotonic",
                return_value=tui.last_notification_poll + 3,
            ),
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
        self.assertEqual(
            {
                "action": "respond_request",
                "request_id": 42,
                "decision": "accept",
            },
            result,
        )

    def test_broker_poll_uses_connection_config_from_dashboard_payload(self) -> None:
        config = dashboard_config()
        config.update(
            {
                "broker_socket": "/run/simple/broker.sock",
                "broker_client": "/opt/simple/core/broker_client.py",
                "broker_token": "session-token",
            }
        )
        tui = AccountDashboardTui(config)
        completed = Mock(returncode=0, stdout=b'{"ok": true}\n', stderr=b"")
        with patch(
            "modules.account_dashboard_tui.subprocess.run",
            return_value=completed,
        ) as run:
            response = tui.broker_request("progress.get")
        self.assertEqual({"ok": True}, response)
        self.assertEqual(
            [
                sys.executable,
                "/opt/simple/core/broker_client.py",
                "/run/simple/broker.sock",
                "progress.get",
                "token",
            ],
            run.call_args.args[0],
        )
        self.assertEqual(b"session-token\0", run.call_args.kwargs["input"])

    def test_notification_aggregation_includes_directed_chat_and_evaluations(self) -> None:
        notifications = build_notifications(
            [
                {
                    "type": "review_received",
                    "review_id": 8,
                    "submission_id": 7,
                    "created_at": "2026-11-05 10:00",
                }
            ],
            [{**submission(), "id": 7}],
            [
                {
                    "request_id": 42,
                    "slot_id": 10,
                    "status": "pending",
                    "requested_at": "2026-11-05 11:00",
                }
            ],
            [],
            [
                {
                    "kind": "direct",
                    "channel": "dm:1:2",
                    "name": "DM · reviewer",
                    "unread_count": 1,
                    "preview": {"body": "Olá", "created_at": "2026-11-05 12:00"},
                },
                {
                    "kind": "room",
                    "channel": "global",
                    "unread_count": 3,
                    "mention_count": 0,
                    "preview": {},
                },
            ],
        )
        self.assertEqual(
            ["chat_message", "evaluation_request", "review_received"],
            [item["category"] for item in notifications],
        )

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
        self.assertIn("Avaliação recebida", lines)
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
