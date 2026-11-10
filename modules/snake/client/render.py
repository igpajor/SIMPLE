"""Pure helpers for rendering bounded Snake snapshots in a terminal."""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional, Tuple


def safe_text(value: Any) -> str:
    return "".join(
        character
        for character in str(value)
        if unicodedata.category(character) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}
    )


def _integer(value: Any) -> Optional[int]:
    return value if type(value) is int else None


def _clip(text: str, width: int) -> str:
    return safe_text(text)[: max(0, width)]


def render_world(
    snapshot: Dict[str, Any],
    width: int,
    height: int,
    arena_cols: int = 120,
    arena_rows: int = 60,
) -> List[str]:
    """Render a server state or local-game state to plain, control-free lines."""
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        raise ValueError("As dimensões do terminal devem ser inteiros positivos.")
    if (
        type(arena_cols) is not int
        or type(arena_rows) is not int
        or arena_cols < 1
        or arena_rows < 1
    ):
        raise ValueError("As dimensões da arena devem ser inteiros positivos.")
    if width < 32 or height < 10:
        return [_clip("Aumente o terminal para continuar.", width)]

    player = snapshot.get("player", {})
    player_id = player.get("id") if isinstance(player, dict) else None
    length = _integer(player.get("length")) if isinstance(player, dict) else None
    kills = _integer(player.get("kills")) if isinstance(player, dict) else None
    alive = player.get("alive") is not False if isinstance(player, dict) else True
    snakes = snapshot.get("snakes", [])
    if not isinstance(snakes, list):
        snakes = []
    own_nick = "Você"
    if isinstance(snakes, list):
        for snake in snakes:
            if isinstance(snake, dict) and snake.get("id") == player_id:
                own_nick = safe_text(snake.get("nick", "Você"))
                break
    title = "SNAKE · comprimento {0} · eliminações {1}".format(
        length if length is not None else 0,
        kills if kills is not None else 0,
    )
    title = "{0} · {1}".format(own_nick, title)
    lines = [_clip(title, width)]

    view_cols = min(arena_cols, max(1, (width - 2) // 2))
    view_rows = min(arena_rows, max(1, height - 9))
    player_head: Optional[Tuple[int, int]] = None
    for snake in snakes:
        if not isinstance(snake, dict) or snake.get("id") != player_id:
            continue
        body = snake.get("body")
        if isinstance(body, list) and body:
            head = body[0]
            if (
                isinstance(head, list)
                and len(head) == 2
                and type(head[0]) is int
                and type(head[1]) is int
            ):
                player_head = (head[0], head[1])
        break
    head_x, head_y = player_head or (arena_cols // 2, arena_rows // 2)
    left = max(0, min(arena_cols - view_cols, head_x - view_cols // 2))
    top = max(0, min(arena_rows - view_rows, head_y - view_rows // 2))
    grid = [["  " for _ in range(view_cols)] for _ in range(view_rows)]

    food = snapshot.get("food", [])
    if isinstance(food, list):
        for item in food:
            if not isinstance(item, dict):
                continue
            x = _integer(item.get("x"))
            y = _integer(item.get("y"))
            if x is None or y is None:
                continue
            if left <= x < left + view_cols and top <= y < top + view_rows:
                grid[y - top][x - left] = "**"

    for snake in snakes:
        if not isinstance(snake, dict):
            continue
        body = snake.get("body")
        if not isinstance(body, list):
            continue
        own = snake.get("id") == player_id
        for index, position in enumerate(body):
            if (
                not isinstance(position, list)
                or len(position) != 2
                or type(position[0]) is not int
                or type(position[1]) is not int
            ):
                continue
            x, y = position
            if left <= x < left + view_cols and top <= y < top + view_rows:
                glyph = "@@" if own and index == 0 else ("##" if own else "oo")
                grid[y - top][x - left] = glyph

    lines.extend("".join(row) for row in grid)
    records = snapshot.get("records", [])
    if isinstance(records, list) and records:
        lines.append("RECORDES PERSISTENTES")
        for record in records[:3]:
            if not isinstance(record, dict):
                continue
            rank = _integer(record.get("rank"))
            best_length = _integer(record.get("best_length"))
            username = safe_text(record.get("username", "Tripulante"))
            if rank is None or best_length is None:
                continue
            lines.append(
                _clip(
                    "{0}. {1}: {2}".format(rank, username, best_length),
                    width,
                )
            )
    else:
        lines.append("Recordes persistentes indisponíveis.")
    if not alive:
        lines.append("Você foi eliminado. Aguardando respawn...")
    else:
        lines.append("Setas/WASD/HJKL mover · Espaço boost · Q sair")
    return [_clip(line, width) for line in lines[:height]]
