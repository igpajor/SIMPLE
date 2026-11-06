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
        with (
            patch.object(
                AccountDashboardTui, "broker_request", return_value={"ok": True}
            ) as request,
            patch.object(AccountDashboardTui, "refresh_notifications"),
        ):
            _tui, result = self.run_tui(config, ["a", "s", "\x1b", "\x1b"])
        self.assertEqual({"action": "exit"}, result)
        request.assert_called_once_with(
            "evaluations.request.respond",
            request_id="42",
            decision="accept",
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

    def test_owner_and_reviewer_receive_role_specific_acceptance_notifications(self) -> None:
        owner_notifications = build_notifications(
            [],
            [{**submission(), "id": 7}],
            [
                {
                    "request_id": 42,
                    "slot_id": 10,
                    "status": "accepted",
                    "reviewer": "reviewer",
                    "responded_at": "today",
                }
            ],
            [],
            [],
        )
        reviewer_notifications = build_notifications(
            [],
            [],
            [],
            [
                {
                    "request_id": 42,
                    "slot_id": 10,
                    "submitter": "owner",
                    "accepted_at": "today",
                }
            ],
            [],
        )
        self.assertEqual(
            "evaluation_request_accepted",
            owner_notifications[0]["category"],
        )
        self.assertEqual(7, owner_notifications[0]["submission_id"])
        self.assertEqual(
            "evaluation_accepted",
            reviewer_notifications[0]["category"],
        )

    def test_owner_acceptance_notification_opens_own_submission(self) -> None:
        config = dashboard_config()
        config["notifications"] = [
            {
                "type": "evaluation_request_accepted",
                "category": "evaluation_request_accepted",
                "request_id": 42,
                "submission_id": 7,
                "exercise_code": "EX-01",
                "other_user": "reviewer",
                "created_at": "today",
            }
        ]
        with patch.object(
            AccountDashboardTui,
            "broker_request",
            return_value={
                "ok": True,
                "submission": {**submission(7), "exercise": {}},
            },
        ) as request:
            _tui, result = self.run_tui(
                config, [curses.KEY_F2, "\n", "\x1b", "\x1b"]
            )
        self.assertEqual({"action": "exit"}, result)
        request.assert_called_once_with("submissions.get", submission_id="7")

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
        with patch.object(AccountDashboardTui, "start_evaluation_form") as start_form:
            _tui, result = self.run_tui(
                config, [curses.KEY_F2, "2", "\x1b", "\x1b"]
            )
        self.assertEqual({"action": "exit"}, result)
        start_form.assert_called_once()

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
        with (
            patch.object(
                AccountDashboardTui, "broker_request", return_value={"ok": True}
            ) as request,
            patch.object(AccountDashboardTui, "refresh_notifications"),
        ):
            _tui, result = self.run_tui(
                config, [curses.KEY_F2, "3", "s", "\x1b", "\x1b"]
            )
        self.assertEqual({"action": "exit"}, result)
        request.assert_called_once_with("evaluations.cancel", slot_id="10")

    def test_f2_reservation_release_confirmation_can_be_cancelled(self) -> None:
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
        with patch.object(AccountDashboardTui, "start_evaluation_form") as start_form:
            _tui, result = self.run_tui(
                config,
                [curses.KEY_F2, "3", "n", "2", "\x1b", "\x1b"],
            )
        self.assertEqual({"action": "exit"}, result)
        start_form.assert_called_once()

    def test_f2_reservation_release_confirmation_is_shown_in_curses(self) -> None:
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
        tui.request_confirmation(
            "evaluations.cancel",
            {"slot_id": "10"},
            "Liberar sua reserva desta avaliação?",
            "dashboard",
        )
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
        rendered = [call.args[2] for call in screen.addnstr.call_args_list]
        self.assertTrue(
            any("Liberar sua reserva desta avaliação?" in line for line in rendered)
        )
        self.assertTrue(any("S confirmar" in line for line in rendered))

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
                side_effect=lambda method, **_fields: responses[method],
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
        screen.get_wch.side_effect = [
            curses.error,
            "a",
            "n",
            "\x1b",
            "\x1b",
        ]
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
                side_effect=lambda method, **_fields: responses[method],
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
        self.assertEqual({"action": "exit"}, result)
        self.assertEqual(
            "evaluation_request",
            tui.notifications[0]["category"],
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

    def test_broker_request_encodes_operation_fields(self) -> None:
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
            tui.broker_request(
                "evaluations.request.respond",
                request_id="42",
                decision="accept",
            )
        self.assertEqual(
            [
                sys.executable,
                "/opt/simple/core/broker_client.py",
                "/run/simple/broker.sock",
                "evaluations.request.respond",
                "token",
                "request_id",
                "decision",
            ],
            run.call_args.args[0],
        )
        self.assertEqual(
            b"session-token\0" + b"42\0accept\0",
            run.call_args.kwargs["input"],
        )

    def test_evaluator_can_request_slot_inside_curses(self) -> None:
        config = dashboard_config()
        config["open_evaluations"] = True
        tui = AccountDashboardTui(config)
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.get_wch.side_effect = ["r", "s", "\x1b"]
        refresh_count = 0

        def refresh(_tui: AccountDashboardTui) -> None:
            nonlocal refresh_count
            refresh_count += 1
            _tui.evaluation_slots = [
                {
                    "id": 10,
                    "username": "owner",
                    "exercise_code": "EX-01",
                    "exercise_name": "Exercício",
                    "request_status": "",
                }
            ]

        with (
            patch.object(
                AccountDashboardTui,
                "broker_request",
                return_value={"ok": True},
            ) as request,
            patch.object(AccountDashboardTui, "refresh_notifications", refresh),
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
        self.assertEqual({"action": "exit"}, result)
        request.assert_called_once_with("evaluations.accept", slot_id="10")
        self.assertGreaterEqual(refresh_count, 2)

    def test_reviewer_submits_score_and_feedback_inside_curses(self) -> None:
        config = dashboard_config()
        config["open_evaluations"] = True
        tui = AccountDashboardTui(config)
        tui.accepted_evaluation_requests = [
            {
                "request_id": 42,
                "slot_id": 10,
                "submitter": "owner",
                "exercise_code": "EX-01",
            }
        ]
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.get_wch.side_effect = [
            "2",
            "\t",
            "x",
            "\x7f",
            "\t",
            "5",
            "\n",
            "B",
            "o",
            "a",
            "\x13",
            "\x1b",
        ]
        responses = {
            "evaluations.slot.get": {
                "ok": True,
                "slot": {
                    "exercise_code": "EX-01",
                    "exercise_name": "Exercício",
                    "username": "owner",
                    "description": "Descrição",
                    "statement": "Enunciado",
                    "content_kind": "github_commit",
                    "commit_url": "https://github.com/user/repo/commit/abcdef0",
                    "delivery_directory": "src",
                },
            },
            "evaluations.review.submit": {
                "ok": True,
                "status": "approved",
                "points": 5,
            },
        }
        with (
            patch.object(
                AccountDashboardTui,
                "broker_request",
                side_effect=lambda method, **_fields: responses[method],
            ) as request,
            patch.object(AccountDashboardTui, "refresh_notifications"),
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
        self.assertEqual({"action": "exit"}, result)
        self.assertEqual(
            [
                (("evaluations.slot.get",), {"slot_id": "10"}),
                (
                    ("evaluations.review.submit",),
                    {
                        "slot_id": "10",
                        "score": "5",
                        "feedback": "Boa",
                    },
                ),
            ],
            [(call.args, call.kwargs) for call in request.call_args_list],
        )

    def test_evaluation_form_draws_separate_review_and_input_frames(self) -> None:
        tui = AccountDashboardTui(dashboard_config())
        tui.screen = "evaluation_form"
        tui.evaluation_form = {
            "mode": "review",
            "title": "AVALIAR · EX-01",
            "exercise": "EX-01 — Exercício",
            "reviewee": "student",
            "description": "Descrição detalhada",
            "statement": "Enunciado do exercício",
            "commit_url": "https://github.com/user/repo/commit/abcdef0",
            "delivery_directory": "src",
        }
        tui.height, tui.width = 31, 105
        tui.colors = {
            "active_frame": 123,
            "input": 456,
            "muted": 789,
            "title": 321,
        }
        screen = Mock()
        screen.getmaxyx.return_value = (31, 105)

        with (
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
            patch.object(tui, "draw_frame", wraps=tui.draw_frame) as draw_frame,
        ):
            tui.draw(screen)

        frame_titles = {
            call.args[5]: call.args[4]
            for call in draw_frame.call_args_list
        }
        rendered = [call.args[2] for call in screen.addnstr.call_args_list]
        self.assertIn("EXERCÍCIO", frame_titles)
        self.assertIn("AVALIADO", frame_titles)
        self.assertIn("DESCRIÇÃO E ENUNCIADO", frame_titles)
        self.assertIn("COMMIT", frame_titles)
        self.assertIn("DIRETÓRIO", frame_titles)
        self.assertIn("NOTA (0-5) · ATIVA", frame_titles)
        self.assertIn("FEEDBACK DETALHADO", frame_titles)
        self.assertTrue(any("Descrição detalhada" in text for text in rendered))
        self.assertTrue(any("Enunciado do exercício" in text for text in rendered))
        self.assertGreater(
            frame_titles["FEEDBACK DETALHADO"],
            frame_titles["NOTA (0-5) · ATIVA"],
        )
        frame_calls = {
            call.args[5]: call for call in draw_frame.call_args_list
        }
        self.assertEqual(
            "active_frame",
            frame_calls["NOTA (0-5) · ATIVA"].kwargs["border_color"],
        )
        self.assertEqual(
            "muted", frame_calls["FEEDBACK DETALHADO"].kwargs["border_color"]
        )
        self.assertTrue(
            any(
                call.args[2] == "Digite uma nota de 0 a 5"
                and call.args[4] == 456
                for call in screen.addnstr.call_args_list
            )
        )

        feedback_frame = frame_calls["FEEDBACK DETALHADO"].args
        feedback_bottom = feedback_frame[1] + feedback_frame[4] - 1
        outer_frame = frame_calls["AVALIAR · EX-01"].args
        outer_bottom = outer_frame[1] + outer_frame[4] - 1
        self.assertLess(feedback_bottom, outer_bottom)
        self.assertLess(feedback_bottom, tui.height - 1)
        self.assertTrue(
            any(
                call.args[:2] == (feedback_bottom, feedback_frame[2] + 1)
                and call.args[-1] == 789
                for call in screen.hline.call_args_list
            )
        )

        tui.evaluation_form_field = "feedback"
        feedback_screen = Mock()
        feedback_screen.getmaxyx.return_value = (31, 105)
        with (
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
            patch.object(tui, "draw_frame", wraps=tui.draw_frame) as draw_frame,
        ):
            tui.draw(feedback_screen)
        active_borders = {
            call.args[5]: call.kwargs.get("border_color")
            for call in draw_frame.call_args_list
        }
        self.assertEqual("muted", active_borders["NOTA (0-5)"])
        self.assertEqual(
            "active_frame", active_borders["FEEDBACK DETALHADO · ATIVO"]
        )

    def test_author_replies_to_review_inside_curses(self) -> None:
        config = dashboard_config()
        config["open_progress"] = True
        config["pending_review_responses"] = [
            {
                "review_id": 27,
                "exercise_code": "EX-01",
                "reviewer": "reviewer",
            }
        ]
        tui = AccountDashboardTui(config)
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.get_wch.side_effect = [
            "r",
            "4",
            "\n",
            "O",
            "b",
            "r",
            "i",
            "g",
            "a",
            "d",
            "o",
            "\x13",
            "\x1b",
            "\x1b",
        ]
        responses = {
            "evaluations.review.get": {
                "ok": True,
                "review": {
                    "review_id": 27,
                    "exercise_code": "EX-01",
                    "reviewer": "reviewer",
                    "score": 5,
                    "feedback": "Boa solução.",
                },
            },
            "evaluations.review.respond": {"ok": True},
            "notifications.mark_read": {"ok": True},
        }
        with (
            patch.object(
                AccountDashboardTui,
                "broker_request",
                side_effect=lambda method, **_fields: responses[method],
            ) as request,
            patch.object(AccountDashboardTui, "refresh_notifications"),
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
        self.assertEqual({"action": "exit"}, result)
        self.assertEqual(
            [
                (("evaluations.review.get",), {"review_id": "27"}),
                (
                    ("evaluations.review.respond",),
                    {
                        "review_id": "27",
                        "score": "4",
                        "feedback": "Obrigado",
                    },
                ),
                (("notifications.mark_read",), {}),
            ],
            [(call.args, call.kwargs) for call in request.call_args_list],
        )

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

    def test_selected_submission_detail_is_loaded_inside_dashboard(self) -> None:
        config = dashboard_config()
        config["submissions"] = [submission(1), submission(2)]
        config["selected_submission_id"] = 2
        tui = AccountDashboardTui(config)
        with patch.object(
            tui,
            "broker_request",
            return_value={
                "ok": True,
                "submission": {**submission(2), "exercise": {}},
            },
        ) as request:
            tui.open_submission(2)
        self.assertEqual("detail", tui.screen)
        self.assertEqual(2, tui.detail["id"])
        request.assert_called_once_with("submissions.get", submission_id="2")

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
        tui = AccountDashboardTui(config)
        with (
            patch.object(
                tui, "broker_request", return_value={"ok": True}
            ) as request,
            patch.object(tui, "refresh_notifications"),
        ):
            tui.request_confirmation(
                "evaluations.request.respond",
                {"request_id": "42", "decision": "accept"},
                "Aceitar este avaliador?",
                "detail",
            )
            tui.complete_confirmation(True)
        self.assertEqual("detail", tui.screen)
        request.assert_called_once_with(
            "evaluations.request.respond",
            request_id="42",
            decision="accept",
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
        tui, result = self.run_tui(config, ["p", "\x1b", "\x1b"])
        self.assertEqual({"action": "exit"}, result)
        lines = "\n".join(line for line, _ in tui.progress_lines())
        self.assertIn("Avaliação recebida", lines)
        self.assertIn("HISTÓRICO DE AVALIAÇÕES", lines)
        self.assertIn("RESPONDER A UMA AVALIAÇÃO", lines)

        self.assertEqual("dashboard", tui.screen)

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
