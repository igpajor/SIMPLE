"""Small deterministic bot policy for headless and multiplayer simulation."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .world import (
    DIRECTION_DELTAS,
    OPPOSITE_DIRECTIONS,
    Position,
    Snake,
    World,
)

DIRECTION_ORDER = ("U", "D", "L", "R")


def _next_cell(snake: Snake, direction: str) -> Position:
    dx, dy = DIRECTION_DELTAS[direction]
    return snake.body[0][0] + dx, snake.body[0][1] + dy


def _safe_directions(world: World, player_id: str) -> List[str]:
    snake = world.snakes[player_id]
    previous = snake.turns[-1] if snake.turns else snake.direction
    occupied: Dict[Position, str] = {}
    for other_id, other in world.snakes.items():
        if not other.alive:
            continue
        body = other.body[:]
        if (
            other_id == player_id
            and body
            and not other.growth
            and not other.boosting
        ):
            body.pop()
        for position in body:
            occupied[position] = other_id

    candidates = [
        direction
        for direction in DIRECTION_ORDER
        if direction != OPPOSITE_DIRECTIONS[previous]
    ]
    safe = []
    for direction in candidates:
        target = _next_cell(snake, direction)
        if not world._inside(target):
            continue
        if target in occupied:
            continue
        safe.append(direction)
    return safe


def choose_direction(world: World, player_id: str) -> str:
    """Choose a safe turn toward nearby food, using stable tie-breaking."""
    snake = world.snakes.get(player_id)
    if snake is None or not snake.alive:
        raise ValueError("Não existe cobra ativa para o bot.")
    safe = _safe_directions(world, player_id)
    if not safe:
        return snake.turns[-1] if snake.turns else snake.direction
    if not world.food:
        preferred = snake.turns[-1] if snake.turns else snake.direction
        return preferred if preferred in safe else safe[0]

    food_positions = tuple(world.food)
    head_x, head_y = snake.body[0]
    target_food = min(
        food_positions,
        key=lambda position: (
            abs(head_x - position[0]) + abs(head_y - position[1]),
            position[1],
            position[0],
        ),
    )

    def score(direction: str) -> Tuple[int, int]:
        target = _next_cell(snake, direction)
        distance = abs(target[0] - target_food[0]) + abs(target[1] - target_food[1])
        return distance, DIRECTION_ORDER.index(direction)

    return min(safe, key=score)


def step_bots(world: World, player_ids: List[str]) -> None:
    for player_id in player_ids:
        snake = world.snakes.get(player_id)
        if snake is None or not snake.alive:
            continue
        direction = choose_direction(world, player_id)
        if direction != (snake.turns[-1] if snake.turns else snake.direction):
            world.set_direction(player_id, direction)
