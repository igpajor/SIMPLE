#!/usr/bin/env python3

from __future__ import annotations

import unittest


class SnakeBotTests(unittest.TestCase):
    def test_bot_turns_away_from_an_immediate_wall(self) -> None:
        from modules.snake.engine.bots import choose_direction
        from modules.snake.engine.world import World

        world = World(width=12, height=8, seed=3)
        world.add_snake(
            "bot", body=[(11, 3), (10, 3), (9, 3)], direction="R"
        )

        direction = choose_direction(world, "bot")

        self.assertIn(direction, ("U", "D"))

    def test_bot_moves_toward_nearest_food_without_reversing(self) -> None:
        from modules.snake.engine.bots import choose_direction
        from modules.snake.engine.world import World

        world = World(width=16, height=10, seed=3)
        world.add_snake(
            "bot", body=[(3, 4), (2, 4), (1, 4)], direction="R"
        )
        world.add_food((5, 4))

        self.assertEqual("R", choose_direction(world, "bot"))

    def test_bot_avoids_food_goal_requiring_an_unsafe_first_step(self) -> None:
        from modules.snake.engine.bots import choose_direction
        from modules.snake.engine.world import World

        world = World(width=12, height=8, seed=3)
        world.add_snake(
            "bot", body=[(5, 3), (4, 3), (3, 3)], direction="R"
        )
        world.add_food((11, 3))
        world.add_food((5, 5))

        self.assertEqual("D", choose_direction(world, "bot"))

    def test_headless_simulation_has_forty_snakes_and_deterministic_state(self) -> None:
        from modules.snake.engine.simulation import HeadlessSimulation

        first = HeadlessSimulation(snake_count=40, seed=19)
        second = HeadlessSimulation(snake_count=40, seed=19)

        self.assertEqual(40, len(first.world.snakes))
        first.run(200)
        second.run(200)

        self.assertEqual(first.world.tick_count, second.world.tick_count)
        self.assertEqual(
            first.world.leaderboard(40), second.world.leaderboard(40)
        )
        self.assertEqual(first.world.food, second.world.food)

    def test_simulation_rejects_invalid_size_and_tick_count(self) -> None:
        from modules.snake.engine.simulation import HeadlessSimulation

        with self.assertRaises(ValueError):
            HeadlessSimulation(snake_count=0)
        with self.assertRaises(ValueError):
            HeadlessSimulation(snake_count=41)
        with self.assertRaises(ValueError):
            HeadlessSimulation().run(-1)

    def test_benchmark_reports_per_tick_metrics(self) -> None:
        from modules.snake.engine.simulation import run_benchmark

        result = run_benchmark(snake_count=40, ticks=100, seed=23)

        self.assertEqual(40, result["snake_count"])
        self.assertEqual(100, result["ticks"])
        self.assertGreaterEqual(result["p99_tick_ms"], 0)
        self.assertGreaterEqual(result["mean_tick_ms"], 0)
        self.assertGreaterEqual(result["max_tick_ms"], result["p99_tick_ms"])


if __name__ == "__main__":
    unittest.main()
