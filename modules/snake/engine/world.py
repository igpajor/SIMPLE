"""Deterministic, I/O-free Snake world simulation."""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Set, Tuple

from . import config

Position = Tuple[int, int]
Direction = str

DIRECTION_DELTAS: Dict[Direction, Position] = {
    "U": (0, -1),
    "D": (0, 1),
    "L": (-1, 0),
    "R": (1, 0),
}
OPPOSITE_DIRECTIONS = {"U": "D", "D": "U", "L": "R", "R": "L"}


@dataclass
class Snake:
    player_id: str
    body: List[Position]
    direction: Direction
    alive: bool = True
    turns: Deque[Direction] = field(default_factory=deque)
    boosting: bool = False
    boost_accumulator: int = 0
    boost_ticks: int = 0
    growth: int = 0
    kills: int = 0
    deaths: int = 0
    last_killer: Optional[str] = None
    invulnerable_ticks: int = 0


class World:
    def __init__(
        self,
        width: int = config.ARENA_WIDTH,
        height: int = config.ARENA_HEIGHT,
        seed: Optional[int] = None,
    ) -> None:
        if type(width) is not int or width < 3:
            raise ValueError("A largura da arena deve ser um inteiro maior que 2.")
        if type(height) is not int or height < 3:
            raise ValueError("A altura da arena deve ser um inteiro maior que 2.")
        self.width = width
        self.height = height
        self.random = random.Random(seed)
        self.snakes: Dict[str, Snake] = {}
        self.food: Dict[Position, int] = {}
        self.tick_count = 0

    def _inside(self, position: Position) -> bool:
        x, y = position
        return 0 <= x < self.width and 0 <= y < self.height

    @staticmethod
    def _position(value: Position) -> Position:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or type(value[0]) is not int
            or type(value[1]) is not int
        ):
            raise ValueError("A posição deve conter duas coordenadas inteiras.")
        return value

    def add_snake(
        self,
        player_id: str,
        body: Iterable[Position],
        direction: Direction,
    ) -> Snake:
        if not isinstance(player_id, str) or not player_id:
            raise ValueError("O identificador do jogador é obrigatório.")
        if player_id in self.snakes:
            raise ValueError("O jogador já tem uma cobra neste mundo.")
        if not isinstance(direction, str) or direction not in DIRECTION_DELTAS:
            raise ValueError("Direção inválida.")
        positions = [self._position(position) for position in body]
        if not positions:
            raise ValueError("A cobra precisa ter pelo menos um segmento.")
        if len(set(positions)) != len(positions):
            raise ValueError("O corpo da cobra não pode ocupar células repetidas.")
        if any(not self._inside(position) for position in positions):
            raise ValueError("O corpo da cobra deve estar dentro da arena.")
        occupied = {
            position
            for snake in self.snakes.values()
            if snake.alive
            for position in snake.body
        }
        if occupied.intersection(positions):
            raise ValueError("A posição inicial colide com outra cobra.")
        if self.food.keys() & set(positions):
            raise ValueError("A posição inicial colide com comida.")
        for head, neck in zip(positions, positions[1:]):
            if abs(head[0] - neck[0]) + abs(head[1] - neck[1]) != 1:
                raise ValueError("Os segmentos da cobra devem ser contíguos.")
        snake = Snake(player_id=player_id, body=positions, direction=direction)
        self.snakes[player_id] = snake
        return snake

    def add_food(self, position: Position, value: int = 1) -> None:
        position = self._position(position)
        if not self._inside(position):
            raise ValueError("A comida deve estar dentro da arena.")
        if type(value) is not int or value < 1:
            raise ValueError("O valor da comida deve ser um inteiro positivo.")
        if position in self.food:
            raise ValueError("Já existe comida nessa posição.")
        if any(
            snake.alive and position in snake.body
            for snake in self.snakes.values()
        ):
            raise ValueError("A comida não pode ocupar uma célula de cobra.")
        self.food[position] = value

    def remove_snake(self, player_id: str) -> Optional[Snake]:
        """Remove a disconnected player without changing the world score."""
        return self.snakes.pop(player_id, None)

    def respawn(
        self, player_id: str, direction: Optional[Direction] = None
    ) -> Snake:
        snake = self.snakes.get(player_id)
        if snake is None or snake.alive:
            raise ValueError("Só é possível reaparecer uma cobra eliminada.")
        directions = [direction] if direction is not None else list(DIRECTION_DELTAS)
        if any(
            not isinstance(item, str) or item not in DIRECTION_DELTAS
            for item in directions
        ):
            raise ValueError("Direção inválida para respawn.")
        length = min(
            config.INITIAL_SNAKE_LENGTH,
            self.width - 2,
            self.height - 2,
        )
        margin = min(
            config.RESPAWN_INVULNERABILITY_TICKS + 1,
            (self.width - 1) // 2,
            (self.height - 1) // 2,
        )
        occupied = {
            position
            for other in self.snakes.values()
            if other.alive
            for position in other.body
        }
        selected_direction = None
        body = None
        food_cells = set(self.food)
        x_min, x_max = margin, self.width - margin
        y_min, y_max = margin, self.height - margin

        def candidate_for(
            direction: str, x: int, y: int
        ) -> Optional[List[Position]]:
            dx, dy = DIRECTION_DELTAS[direction]
            candidate = [
                (x - dx * offset, y - dy * offset)
                for offset in range(length)
            ]
            if any(
                not (x_min <= cell_x < x_max and y_min <= cell_y < y_max)
                for cell_x, cell_y in candidate
            ):
                return None
            if occupied.intersection(candidate) or food_cells.intersection(candidate):
                return None
            return candidate

        for _ in range(64):
            candidate_direction = self.random.choice(directions)
            x = self.random.randrange(x_min, x_max)
            y = self.random.randrange(y_min, y_max)
            candidate = candidate_for(candidate_direction, x, y)
            if candidate is not None:
                selected_direction = candidate_direction
                body = candidate
                break

        if body is None:
            for candidate_direction in directions:
                for y in range(y_min, y_max):
                    for x in range(x_min, x_max):
                        candidate = candidate_for(candidate_direction, x, y)
                        if candidate is not None:
                            selected_direction = candidate_direction
                            body = candidate
                            break
                    if body is not None:
                        break
                if body is not None:
                    break
        if body is None or selected_direction is None:
            raise ValueError("Não há espaço livre para reaparecer.")
        snake.body = body
        snake.direction = selected_direction
        snake.alive = True
        snake.turns.clear()
        snake.boosting = False
        snake.boost_accumulator = 0
        snake.boost_ticks = 0
        snake.growth = 0
        snake.last_killer = None
        snake.invulnerable_ticks = config.RESPAWN_INVULNERABILITY_TICKS
        return snake

    def set_direction(self, player_id: str, direction: Direction) -> bool:
        snake = self.snakes.get(player_id)
        if (
            snake is None
            or not snake.alive
            or not isinstance(direction, str)
            or direction not in DIRECTION_DELTAS
        ):
            return False
        previous = snake.turns[-1] if snake.turns else snake.direction
        if direction == previous or OPPOSITE_DIRECTIONS[previous] == direction:
            return False
        if len(snake.turns) >= config.TURN_QUEUE_MAX:
            return False
        snake.turns.append(direction)
        return True

    def set_boost(self, player_id: str, enabled: bool) -> bool:
        snake = self.snakes.get(player_id)
        if snake is None or not snake.alive or type(enabled) is not bool:
            return False
        if enabled and len(snake.body) < config.MIN_BOOST_LENGTH:
            return False
        snake.boosting = enabled
        if not enabled:
            snake.boost_accumulator = 0
            snake.boost_ticks = 0
        return True

    def _move_counts(self) -> Tuple[Dict[str, int], Dict[str, bool]]:
        counts: Dict[str, int] = {}
        boost_drops: Dict[str, bool] = {}
        for player_id, snake in self.snakes.items():
            if not snake.alive:
                continue
            count = 1
            drop = False
            if snake.boosting and len(snake.body) >= config.MIN_BOOST_LENGTH:
                snake.boost_ticks += 1
                snake.boost_accumulator += (
                    config.BOOST_SPEED_NUMERATOR - config.BOOST_SPEED_DENOMINATOR
                )
                if snake.boost_accumulator >= config.BOOST_SPEED_DENOMINATOR:
                    snake.boost_accumulator -= config.BOOST_SPEED_DENOMINATOR
                    count += 1
                drop = snake.boost_ticks % config.BOOST_DROP_INTERVAL_TICKS == 0
            counts[player_id] = count
            boost_drops[player_id] = drop
        return counts, boost_drops

    @staticmethod
    def _remaining_body(
        snake: Snake, tail_segments_to_remove: int
    ) -> List[Position]:
        if tail_segments_to_remove <= 0:
            return snake.body[:]
        if tail_segments_to_remove >= len(snake.body):
            return []
        return snake.body[:-tail_segments_to_remove]

    def _drop_food(self, positions: Iterable[Position]) -> None:
        available = []
        occupied = {
            position
            for snake in self.snakes.values()
            if snake.alive
            for position in snake.body
        }
        for position in positions:
            if (
                self._inside(position)
                and position not in occupied
                and position not in self.food
                and position not in available
            ):
                available.append(position)
        amount = len(available) * config.DEATH_FOOD_FRACTION_NUMERATOR
        amount //= config.DEATH_FOOD_FRACTION_DENOMINATOR
        if not available or amount <= 0:
            return
        self.random.shuffle(available)
        for position in available[:amount]:
            self.food[position] = self.random.randint(
                config.DEATH_FOOD_MIN_VALUE, config.DEATH_FOOD_MAX_VALUE
            )

    def _kill(self, player_id: str, killer_id: Optional[str] = None) -> None:
        snake = self.snakes[player_id]
        if not snake.alive:
            return
        snake.alive = False
        snake.deaths += 1
        snake.last_killer = killer_id
        if killer_id is not None and killer_id in self.snakes:
            self.snakes[killer_id].kills += 1
        self._drop_food(snake.body)

    def _resolve_substep(
        self,
        movers: List[str],
        boost_drops: Dict[str, bool],
        protected: Set[str],
    ) -> None:
        targets: Dict[str, Position] = {}
        for player_id in movers:
            snake = self.snakes[player_id]
            dx, dy = DIRECTION_DELTAS[snake.direction]
            targets[player_id] = (snake.body[0][0] + dx, snake.body[0][1] + dy)

        deaths: Dict[str, Optional[str]] = {}
        for player_id, target in targets.items():
            if not self._inside(target):
                deaths[player_id] = None

        # A collision into another snake's body is evaluated against the body
        # that remains after its tail moves on this substep.
        for player_id, target in targets.items():
            if player_id in deaths or player_id in protected:
                continue
            for other_id, other in self.snakes.items():
                if (
                    other_id == player_id
                    or not other.alive
                    or other_id in protected
                ):
                    continue
                if other_id not in targets and target == other.body[0]:
                    player_length = len(self.snakes[player_id].body)
                    other_length = len(other.body)
                    if player_length == other_length:
                        deaths[player_id] = None
                        deaths[other_id] = None
                    elif player_length > other_length:
                        deaths[other_id] = player_id
                    else:
                        deaths[player_id] = other_id
                    break
                vacated = 0
                other_target = targets.get(other_id)
                ate_food = other_target in self.food if other_target is not None else False
                if (
                    other_id in targets
                    and not other.growth
                    and not ate_food
                ):
                    vacated = 1
                if boost_drops.get(other_id, False):
                    vacated += 1
                remaining = self._remaining_body(other, vacated)
                if target in remaining[1:]:
                    deaths[player_id] = other_id
                    break

        # Resolve heads moving to the same cell; the larger snake survives
        # only when it is the unique largest one in that collision group.
        groups: Dict[Position, List[str]] = {}
        for player_id, target in targets.items():
            if player_id not in deaths and player_id not in protected:
                groups.setdefault(target, []).append(player_id)
        for player_ids in groups.values():
            if len(player_ids) < 2:
                continue
            lengths = {
                player_id: len(self.snakes[player_id].body)
                for player_id in player_ids
            }
            largest = max(lengths.values())
            winners = [player_id for player_id in player_ids if lengths[player_id] == largest]
            if len(winners) != 1:
                for player_id in player_ids:
                    deaths[player_id] = None
            else:
                winner = winners[0]
                for player_id in player_ids:
                    if player_id != winner:
                        deaths[player_id] = winner

        # Swapping heads is also a head-to-head collision.
        checked_pairs = set()
        for player_id, target in targets.items():
            for other_id, other_target in targets.items():
                pair = frozenset((player_id, other_id))
                if (
                    player_id == other_id
                    or len(pair) < 2
                    or pair in checked_pairs
                    or player_id in deaths
                    or other_id in deaths
                    or player_id in protected
                    or other_id in protected
                ):
                    continue
                checked_pairs.add(pair)
                if (
                    target != self.snakes[other_id].body[0]
                    or other_target != self.snakes[player_id].body[0]
                ):
                    continue
                player_length = len(self.snakes[player_id].body)
                other_length = len(self.snakes[other_id].body)
                if player_length == other_length:
                    deaths[player_id] = None
                    deaths[other_id] = None
                elif player_length > other_length:
                    deaths[other_id] = player_id
                else:
                    deaths[player_id] = other_id

        for player_id, killer_id in deaths.items():
            self._kill(player_id, killer_id)

        for player_id, target in targets.items():
            snake = self.snakes[player_id]
            if not snake.alive:
                continue
            food_value = self.food.pop(target, 0)
            snake.body.insert(0, target)
            snake.growth += food_value
            if snake.growth:
                snake.growth -= 1
            else:
                snake.body.pop()

            if boost_drops.get(player_id, False) and len(snake.body) > 1:
                dropped = snake.body.pop()
                self._drop_food((dropped,))

    def tick(self) -> None:
        self.tick_count += 1
        protected = {
            player_id
            for player_id, snake in self.snakes.items()
            if snake.alive and snake.invulnerable_ticks > 0
        }
        counts, boost_drops = self._move_counts()
        for snake in self.snakes.values():
            if snake.alive and snake.turns:
                snake.direction = snake.turns.popleft()
        max_moves = max(counts.values(), default=0)
        for substep in range(max_moves):
            movers = [
                player_id
                for player_id, count in counts.items()
                if count > substep and self.snakes[player_id].alive
            ]
            if movers:
                drops_this_substep = {
                    player_id: boost_drops[player_id]
                    and counts[player_id] == substep + 1
                    for player_id in movers
                }
                self._resolve_substep(movers, drops_this_substep, protected)
        for player_id in protected:
            snake = self.snakes[player_id]
            snake.invulnerable_ticks -= 1

    def replenish_food(self) -> None:
        target_count = (
            self.width * self.height // config.FOOD_CELLS_PER_ITEM
        )
        if len(self.food) >= target_count:
            return
        occupied = {
            position
            for snake in self.snakes.values()
            if snake.alive
            for position in snake.body
        }
        available = [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if (x, y) not in occupied and (x, y) not in self.food
        ]
        missing = min(target_count - len(self.food), len(available))
        for position in self.random.sample(available, missing):
            self.food[position] = 1

    def leaderboard(self, limit: int = config.LEADERBOARD_SIZE) -> List[dict]:
        if type(limit) is not int or limit < 0:
            raise ValueError("O limite do placar deve ser um inteiro não negativo.")
        entries = [
            {
                "player_id": snake.player_id,
                "length": len(snake.body),
                "kills": snake.kills,
                "deaths": snake.deaths,
            }
            for snake in self.snakes.values()
            if snake.alive
        ]
        entries.sort(key=lambda entry: (-entry["length"], entry["player_id"]))
        return entries[:limit]
