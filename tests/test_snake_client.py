#!/usr/bin/env python3

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch


class SnakeClientTests(unittest.TestCase):
    def test_styled_render_assigns_red_only_to_food_and_distinct_snake_colors(self) -> None:
        from modules.snake.client.render import (
            FOOD_COLOR,
            SNAKE_COLOR_PALETTE,
            render_world_styled,
        )

        snapshot = {
            "t": "state",
            "player": {"id": "alice", "length": 3, "alive": True, "kills": 0},
            "snakes": [
                {"id": "alice", "body": [[5, 5], [4, 5]], "color": 0},
                {"id": "bob", "body": [[8, 5], [9, 5]], "color": 1},
            ],
            "food": [{"x": 6, "y": 5, "value": 1}],
            "leaderboard": [],
            "minimap": [" " * 20 for _ in range(10)],
            "kill_feed": [],
        }

        _lines, styles = render_world_styled(
            snapshot, width=80, height=20, arena_cols=20, arena_rows=12
        )

        snake_colors = {style[3] for style in styles if style[3] != FOOD_COLOR}
        self.assertIn(FOOD_COLOR, {style[3] for style in styles})
        self.assertEqual(2, len(snake_colors))
        self.assertNotIn(FOOD_COLOR, SNAKE_COLOR_PALETTE)
        self.assertNotEqual(SNAKE_COLOR_PALETTE[0], SNAKE_COLOR_PALETTE[1])

    def test_client_does_not_render_empty_arena_before_first_server_state(self) -> None:
        from modules.snake.client.client import SnakeClient

        class FakeScreen:
            def __init__(self):
                self.lines = []

            def getmaxyx(self):
                return (24, 80)

            def erase(self):
                pass

            def addnstr(self, y, x, text, count, *_attributes):
                self.lines.append((y, x, text[:count]))

            def refresh(self):
                pass

        client = SnakeClient(solo=False, socket_path=None)
        screen = FakeScreen()

        client._draw(screen)

        rendered = "\n".join(line[2] for line in screen.lines)
        self.assertIn("Aguardando", rendered)
        self.assertNotIn("RECORDES PERSISTENTES", rendered)
        self.assertNotIn("**", rendered)

    def test_ten_row_terminal_is_not_rejected_after_status_line_reservation(self) -> None:
        from modules.snake.client.client import SnakeClient
        from modules.snake.client.render import render_world

        lines = render_world(
            {"t": "state", "player": {}, "snakes": [], "food": [], "leaderboard": []},
            width=32,
            height=9,
        )

        self.assertNotIn("Aumente", "\n".join(lines))

        class FakeScreen:
            def __init__(self):
                self.lines = []

            def getmaxyx(self):
                return (10, 32)

            def erase(self):
                pass

            def addnstr(self, y, x, text, count, *_attributes):
                self.lines.append((y, x, text[:count]))

            def refresh(self):
                pass

        client = SnakeClient(solo=True)
        screen = FakeScreen()
        client._draw(screen)
        rendered = "\n".join(line[2] for line in screen.lines)
        self.assertNotIn("Aumente", rendered)

    def test_color_pairs_reserve_red_for_food(self) -> None:
        from modules.snake.client.client import SnakeClient
        from modules.snake.client.render import FOOD_COLOR, SNAKE_COLOR_PALETTE

        client = SnakeClient(solo=True)
        initialized = []
        with ExitStack() as stack:
            stack.enter_context(
                patch("modules.snake.client.client.curses.has_colors", return_value=True)
            )
            stack.enter_context(
                patch("modules.snake.client.client.curses.start_color")
            )
            stack.enter_context(
                patch("modules.snake.client.client.curses.use_default_colors")
            )
            stack.enter_context(
                patch(
                "modules.snake.client.client.curses.init_pair",
                side_effect=lambda pair, color, background: initialized.append(
                    (pair, color, background)
                ),
                )
            )
            client._initialize_colors()

        self.assertEqual(set(SNAKE_COLOR_PALETTE) | {FOOD_COLOR}, set(client.color_pairs))
        self.assertEqual(len(SNAKE_COLOR_PALETTE) + 1, len(initialized))
        self.assertNotIn("red", SNAKE_COLOR_PALETTE)

    def test_curses_draw_applies_distinct_snake_and_red_food_pairs(self) -> None:
        from modules.snake.client.client import SnakeClient
        from modules.snake.client.render import FOOD_COLOR, SNAKE_COLOR_PALETTE

        class FakeScreen:
            def __init__(self):
                self.calls = []

            def getmaxyx(self):
                return (24, 80)

            def erase(self):
                pass

            def addnstr(self, *args):
                self.calls.append(args)

            def refresh(self):
                pass

        client = SnakeClient(solo=False)
        client.snapshot = {
            "t": "state",
            "tick": 1,
            "player": {"id": "alice", "length": 2, "alive": True, "kills": 0},
            "snakes": [
                {"id": "alice", "body": [[10, 10], [9, 10]], "color": 0},
                {"id": "bob", "body": [[12, 10], [13, 10]], "color": 1},
            ],
            "food": [{"x": 11, "y": 10, "value": 1}],
            "leaderboard": [],
            "minimap": [" " * 20 for _ in range(10)],
            "kill_feed": [],
        }
        client.color_pairs = {
            SNAKE_COLOR_PALETTE[0]: 1,
            SNAKE_COLOR_PALETTE[1]: 2,
            FOOD_COLOR: 7,
        }
        screen = FakeScreen()

        with patch("modules.snake.client.client.curses.color_pair", side_effect=int):
            client._draw(screen)

        styled_calls = [call for call in screen.calls if len(call) == 5]
        self.assertEqual({1, 2, 7}, {call[4] & 0xFF for call in styled_calls})

    def test_render_snapshot_maps_game_entities_into_safe_terminal_cells(self) -> None:
        from modules.snake.client.render import render_world

        snapshot = {
            "t": "state",
            "tick": 12,
            "player": {
                "id": "local",
                "length": 5,
                "alive": True,
                "kills": 1,
                "deaths": 0,
                "boosting": False,
            },
            "snakes": [
                {
                    "id": "local",
                    "nick": "Navegante",
                    "body": [[5, 5], [4, 5], [3, 5]],
                    "length": 3,
                    "alive": True,
                    "color": 0,
                    "glyph": "@",
                },
                {
                    "id": "rival",
                    "nick": "Rival\x1b[31m",
                    "body": [[8, 5], [8, 6]],
                    "length": 2,
                    "alive": True,
                    "color": 1,
                    "glyph": "R",
                },
            ],
            "food": [{"x": 6, "y": 6, "value": 1}],
            "minimap": ["@                   "] + [" " * 20 for _ in range(9)],
            "kill_feed": [
                {"killer": "Rival", "victim": "Navegante"},
                {"killer": None, "victim": "Bot 01"},
            ],
            "leaderboard": [{"rank": 1, "nick": "Rival\x1b[31m", "length": 2}],
            "records": [
                {
                    "rank": 1,
                    "username": "Mestre\x1b[31m",
                    "best_length": 42,
                    "kills_total": 3,
                }
            ],
        }

        lines = render_world(snapshot, width=48, height=16, arena_cols=20, arena_rows=12)

        rendered = "\n".join(lines)
        self.assertIn("Navegante", rendered)
        self.assertIn("5", rendered)
        self.assertIn("@@", rendered)
        self.assertIn("oo", rendered)
        self.assertIn("**", rendered)
        self.assertIn("MINIMAPA", rendered)
        self.assertIn("Rival", rendered)
        self.assertIn("Bot 01", rendered)
        self.assertIn("Mestre", rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertTrue(all(len(line) <= 48 for line in lines))

    def test_render_world_handles_small_terminal_without_drawing_out_of_bounds(self) -> None:
        from modules.snake.client.render import render_world

        lines = render_world({}, width=20, height=6)

        self.assertTrue(lines)
        self.assertIn("Aumente", "\n".join(lines))
        self.assertTrue(all(len(line) <= 20 for line in lines))

    def test_client_messages_only_encode_allowed_intentions(self) -> None:
        from modules.snake.client.client import encode_message

        message = encode_message({"t": "dir", "d": "L"})

        self.assertEqual(b'{"t":"dir","d":"L"}\n', message)
        with self.assertRaises(ValueError):
            encode_message({"t": "dir", "d": "L", "x": 10})
        with self.assertRaises(ValueError):
            encode_message({"t": "state", "body": [[1, 2]]})

    def test_server_message_decoder_rejects_unhashable_message_types(self) -> None:
        from modules.snake.client.client import decode_server_message

        with self.assertRaises(ValueError):
            decode_server_message(b'{"t":[]}')

    def test_server_message_decoder_rejects_malformed_optional_hud_fields(self) -> None:
        from modules.snake.client.client import decode_server_message

        message = {
            "t": "state",
            "tick": 1,
            "player": {},
            "snakes": [],
            "food": [],
            "leaderboard": [],
            "minimap": ["row", 1],
            "kill_feed": [],
        }

        with self.assertRaises(ValueError):
            decode_server_message(json.dumps(message).encode("utf-8"))

    def test_server_message_decoder_accepts_legacy_state_without_hud_fields(self) -> None:
        from modules.snake.client.client import decode_server_message

        message = {
            "t": "state",
            "tick": 1,
            "player": {},
            "snakes": [],
            "food": [],
            "leaderboard": [],
        }

        self.assertEqual(
            message,
            decode_server_message(json.dumps(message).encode("utf-8")),
        )

    def test_solo_simulation_moves_player_without_socket_or_account_identity(self) -> None:
        from modules.snake.client.client import SoloGame

        game = SoloGame(seed=17, bot_count=2)
        starting_position = game.world.snakes["local"].body[0]
        game.turn("D")
        game.step()
        player = game.world.snakes["local"]

        self.assertEqual(2, len(game.bot_ids))
        self.assertNotEqual(starting_position, player.body[0])
        self.assertEqual((starting_position[0], starting_position[1] + 1), player.body[0])
        self.assertEqual(1, game.world.tick_count)
        snapshot = game.snapshot()
        self.assertEqual(10, len(snapshot["minimap"]))
        self.assertEqual([], snapshot["kill_feed"])


if __name__ == "__main__":
    unittest.main()
