#!/usr/bin/env python3

from __future__ import annotations

import random
import unittest


class SnakeEngineContractTests(unittest.TestCase):
    def make_world(self):
        from modules.snake.engine.world import World

        return World(width=12, height=8, seed=7)

    def test_config_exposes_the_phase_one_rules(self) -> None:
        from modules.snake.engine import config

        self.assertEqual(10, config.TICK_HZ)
        self.assertEqual((120, 60), (config.ARENA_WIDTH, config.ARENA_HEIGHT))
        self.assertEqual(32, config.MAX_HUMAN_PLAYERS)
        self.assertTrue(config.BOTS_ENABLED)
        self.assertEqual(2, config.BOT_COUNT)
        self.assertFalse(config.WALL_WRAP)
        self.assertFalse(config.LEARNING_POINTS_ENABLED)
        self.assertFalse(config.ANNOUNCE_RECORDS)

    def test_moves_one_cell_and_keeps_body_contiguous(self) -> None:
        world = self.make_world()
        snake = world.add_snake("alice", body=[(3, 3), (2, 3), (1, 3)], direction="R")

        world.tick()

        self.assertEqual([(4, 3), (3, 3), (2, 3)], snake.body)

    def test_rejects_reverse_direction_and_queues_at_most_two_turns(self) -> None:
        world = self.make_world()
        snake = world.add_snake("alice", body=[(3, 3), (2, 3), (1, 3)], direction="R")

        self.assertFalse(world.set_direction("alice", "L"))
        self.assertTrue(world.set_direction("alice", "U"))
        self.assertTrue(world.set_direction("alice", "L"))
        self.assertFalse(world.set_direction("alice", "D"))

        world.tick()
        self.assertEqual("U", snake.direction)
        world.tick()
        self.assertEqual("L", snake.direction)

    def test_eating_food_grows_snake(self) -> None:
        world = self.make_world()
        snake = world.add_snake("alice", body=[(3, 3), (2, 3), (1, 3)], direction="R")
        world.add_food((4, 3), value=1)

        world.tick()

        self.assertEqual(4, len(snake.body))
        self.assertNotIn((4, 3), world.food)

    def test_food_value_two_grows_twice_even_during_pending_growth(self) -> None:
        world = self.make_world()
        snake = world.add_snake(
            "alice", body=[(3, 3), (2, 3), (1, 3)], direction="R"
        )
        snake.growth = 1
        world.add_food((4, 3), value=2)

        world.tick()
        world.tick()
        world.tick()

        self.assertEqual(6, len(snake.body))
        self.assertEqual(0, snake.growth)

    def test_wall_collision_kills_snake(self) -> None:
        world = self.make_world()
        snake = world.add_snake("alice", body=[(11, 3), (10, 3), (9, 3)], direction="R")

        world.tick()

        self.assertFalse(snake.alive)
        dropped_food = [
            value for position, value in world.food.items()
            if position in {(11, 3), (10, 3), (9, 3)}
        ]
        self.assertEqual(1, len(dropped_food))
        self.assertIn(dropped_food[0], (1, 2))

    def test_respawn_uses_free_cells_and_grants_three_ticks_of_protection(self) -> None:
        world = self.make_world()
        snake = world.add_snake(
            "alice", body=[(3, 3), (2, 3), (1, 3)], direction="R"
        )
        snake.alive = False

        respawned = world.respawn("alice", direction="R")

        self.assertTrue(respawned.alive)
        self.assertEqual(4, len(respawned.body))
        self.assertEqual(3, respawned.invulnerable_ticks)
        self.assertTrue(all(world._inside(cell) for cell in respawned.body))
        for _ in range(3):
            world.tick()
        self.assertTrue(respawned.alive)
        self.assertEqual(0, respawned.invulnerable_ticks)

    def test_invulnerability_prevents_snake_body_collision(self) -> None:
        world = self.make_world()
        alice = world.add_snake(
            "alice", body=[(3, 3), (2, 3), (1, 3)], direction="R"
        )
        world.add_snake(
            "bob", body=[(5, 3), (4, 3), (4, 2)], direction="D"
        )
        alice.invulnerable_ticks = 1

        world.tick()

        self.assertTrue(alice.alive)
        self.assertEqual(0, alice.invulnerable_ticks)

    def test_self_collision_does_not_kill_snake(self) -> None:
        world = self.make_world()
        snake = world.add_snake(
            "alice",
            body=[(2, 2), (2, 3), (3, 3), (3, 2), (4, 2)],
            direction="R",
        )

        world.tick()

        self.assertTrue(snake.alive)

    def test_food_replenishment_uses_seed_and_avoids_occupied_cells(self) -> None:
        first = self.make_world()
        second = self.make_world()
        for world in (first, second):
            world.add_snake("alice", body=[(3, 3), (2, 3), (1, 3)], direction="R")
            world.replenish_food()

        self.assertEqual(first.food, second.food)
        self.assertEqual(2, len(first.food))
        self.assertTrue(set(first.food).isdisjoint(first.snakes["alice"].body))

    def test_leaderboard_orders_by_length(self) -> None:
        world = self.make_world()
        world.add_snake("alice", body=[(2, 2), (1, 2), (0, 2)], direction="R")
        world.add_snake("bob", body=[(2, 5), (1, 5)], direction="R")

        leaderboard = world.leaderboard(limit=10)

        self.assertEqual("alice", leaderboard[0]["player_id"])

    def test_identical_seed_and_intents_produce_identical_food(self) -> None:
        first = self.make_world()
        second = self.make_world()
        for world in (first, second):
            world.add_snake("alice", body=[(3, 3), (2, 3), (1, 3)], direction="R")
            world.replenish_food()
            world.tick()

        self.assertEqual(first.food, second.food)

    def test_ten_thousand_seeded_ticks_preserve_world_invariants(self) -> None:
        from modules.snake.engine.world import World

        world = World(width=120, height=60, seed=7)
        random_source = random.Random(2026)
        player_id = "fuzzer"

        def spawn() -> None:
            for y in range(world.height):
                for x in range(3, world.width):
                    body = [(x - offset, y) for offset in range(4)]
                    if not set(body).intersection(world.food):
                        world.add_snake(player_id, body=body, direction="R")
                        return
            self.fail("Não foi encontrada posição livre para respawn.")

        spawn()
        world.add_food((8, 3), 1)
        world.add_food((9, 3), 2)

        for _ in range(10_000):
            snake = world.snakes[player_id]
            if not snake.alive:
                world.remove_snake(player_id)
                spawn()
                snake = world.snakes[player_id]
            direction = random_source.choice(("U", "D", "L", "R"))
            world.set_direction(player_id, direction)
            world.set_boost(player_id, random_source.choice((True, False)))
            world.tick()

            for current in world.snakes.values():
                if not current.alive:
                    continue
                self.assertTrue(all(world._inside(cell) for cell in current.body))
                for head, neck in zip(current.body, current.body[1:]):
                    self.assertEqual(
                        1, abs(head[0] - neck[0]) + abs(head[1] - neck[1])
                    )
                self.assertTrue(set(current.body).isdisjoint(world.food))
            self.assertTrue(all(world._inside(cell) for cell in world.food))

    def test_collision_with_another_snakes_body_kills_only_moving_snake(self) -> None:
        world = self.make_world()
        alice = world.add_snake("alice", body=[(3, 3), (2, 3), (1, 3)], direction="R")
        bob = world.add_snake("bob", body=[(5, 3), (4, 3), (4, 2)], direction="D")

        world.tick()

        self.assertFalse(alice.alive)
        self.assertTrue(bob.alive)

    def test_head_to_head_collision_kills_shorter_snake(self) -> None:
        world = self.make_world()
        alice = world.add_snake("alice", body=[(3, 3), (2, 3), (1, 3)], direction="R")
        bob = world.add_snake("bob", body=[(5, 3), (6, 3)], direction="L")

        world.tick()

        self.assertTrue(alice.alive)
        self.assertFalse(bob.alive)

    def test_boosted_head_collides_with_a_stationary_head_on_second_substep(self) -> None:
        from modules.snake.engine.world import World

        world = World(width=20, height=12, seed=7)
        boosted = world.add_snake(
            "boosted",
            body=[(3, 4), (2, 4), (1, 4), (0, 4), (0, 3), (0, 2)],
            direction="R",
        )
        other = world.add_snake(
            "other", body=[(6, 2), (6, 1), (7, 1)], direction="D"
        )
        world.set_boost("boosted", True)

        world.tick()
        world.tick()

        self.assertTrue(boosted.alive)
        self.assertFalse(other.alive)

    def test_equal_head_to_head_collision_kills_both_snakes(self) -> None:
        world = self.make_world()
        alice = world.add_snake("alice", body=[(3, 3), (2, 3)], direction="R")
        bob = world.add_snake("bob", body=[(5, 3), (6, 3)], direction="L")

        world.tick()

        self.assertFalse(alice.alive)
        self.assertFalse(bob.alive)

    def test_boost_requires_minimum_length_and_consumes_segment_every_eight_ticks(self) -> None:
        from modules.snake.engine.world import World

        world = World(width=30, height=20, seed=7)
        short = world.add_snake(
            "short", body=[(5, 3), (4, 3), (3, 3), (2, 3), (1, 3)], direction="R"
        )
        long = world.add_snake(
            "long",
            body=[(5, 10), (4, 10), (3, 10), (2, 10), (1, 10), (0, 10)],
            direction="R",
        )

        self.assertFalse(world.set_boost("short", True))
        self.assertTrue(world.set_boost("long", True))
        initial_length = len(long.body)
        for _ in range(8):
            world.tick()

        self.assertEqual(initial_length - 1, len(long.body))


if __name__ == "__main__":
    unittest.main()
