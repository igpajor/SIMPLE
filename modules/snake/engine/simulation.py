"""Reproducible headless simulation and per-tick benchmark helpers."""

from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from . import config
from .bots import step_bots
from .world import World


class HeadlessSimulation:
    def __init__(
        self,
        snake_count: int = config.BOT_COUNT,
        seed: Optional[int] = None,
    ) -> None:
        if type(snake_count) is not int or not 1 <= snake_count <= 40:
            raise ValueError("A simulação aceita de 1 a 40 cobras.")
        if config.ARENA_WIDTH < 120 or config.ARENA_HEIGHT < 60:
            raise ValueError("A arena padrão precisa comportar a simulação.")
        self.world = World(seed=seed)
        self.bot_ids: List[str] = []
        columns = 10
        row_spacing = 12
        column_spacing = 11
        for index in range(snake_count):
            row, column = divmod(index, columns)
            x = 5 + column * column_spacing
            y = 5 + row * row_spacing
            player_id = "bot-{0:02d}".format(index + 1)
            self.world.add_snake(
                player_id,
                body=[(x - offset, y) for offset in range(config.INITIAL_SNAKE_LENGTH)],
                direction="R",
            )
            self.bot_ids.append(player_id)
        self.world.replenish_food()

    def step(self) -> None:
        for player_id in self.bot_ids:
            snake = self.world.snakes[player_id]
            if not snake.alive:
                self.world.respawn(player_id)
        step_bots(self.world, self.bot_ids)
        self.world.tick()
        self.world.replenish_food()

    def run(self, ticks: int) -> World:
        if type(ticks) is not int or ticks < 0:
            raise ValueError("A quantidade de ticks deve ser um inteiro não negativo.")
        for _ in range(ticks):
            self.step()
        return self.world


def _percentile_99(samples: List[int]) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, math.ceil(len(ordered) * 0.99) - 1)
    return ordered[index] / 1_000_000.0


def run_benchmark(
    snake_count: int = 40,
    ticks: int = 10_000,
    seed: Optional[int] = 2026,
) -> Dict[str, float]:
    if type(ticks) is not int or ticks < 1:
        raise ValueError("O benchmark exige pelo menos um tick.")
    simulation = HeadlessSimulation(snake_count=snake_count, seed=seed)
    samples: List[int] = []
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    for _ in range(ticks):
        started = time.perf_counter_ns()
        simulation.step()
        samples.append(time.perf_counter_ns() - started)
    elapsed = time.perf_counter() - wall_start
    cpu_seconds = time.process_time() - cpu_start
    return {
        "snake_count": float(snake_count),
        "ticks": float(ticks),
        "elapsed_seconds": elapsed,
        "cpu_seconds": cpu_seconds,
        "mean_tick_ms": (elapsed * 1000.0 / ticks),
        "p99_tick_ms": _percentile_99(samples),
        "max_tick_ms": max(samples) / 1_000_000.0,
    }
