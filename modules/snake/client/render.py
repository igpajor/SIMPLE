"""Pure helpers for rendering bounded Snake snapshots in a terminal."""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from ..engine import config

SNAKE_COLOR_PALETTE = ("cyan", "green", "yellow", "blue", "magenta", "white")
FOOD_COLOR = "red"


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


def _valid_minimap(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == config.MINIMAP_HEIGHT
        and all(
            isinstance(row, str)
            and len(row) == config.MINIMAP_WIDTH
            and all(character in " .#o@" for character in row)
            for row in value
        )
    )


def _compress_minimap(rows: List[str], target_rows: int) -> List[str]:
    if target_rows >= len(rows):
        return rows
    priorities = {" ": 0, ".": 1, "#": 2, "o": 3, "@": 4}
    compacted = []
    for target_y in range(target_rows):
        start = target_y * len(rows) // target_rows
        end = max(start + 1, (target_y + 1) * len(rows) // target_rows)
        compacted.append(
            "".join(
                max(
                    (row[x] for row in rows[start:end]),
                    key=priorities.__getitem__,
                )
                for x in range(config.MINIMAP_WIDTH)
            )
        )
    return compacted


def _kill_feed_text(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    victim = safe_text(event.get("victim", "Tripulante"))
    killer = event.get("killer")
    if isinstance(killer, str) and killer:
        return "{0} > {1}".format(safe_text(killer), victim)
    return "{0} eliminado".format(victim)


def render_world_styled(
    snapshot: Dict[str, Any],
    width: int,
    height: int,
    arena_cols: int = 120,
    arena_rows: int = 60,
) -> Tuple[List[str], List[Tuple[int, int, str, str]]]:
    """Render plain lines plus bounded colored cell overlays."""
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        raise ValueError("As dimensões do terminal devem ser inteiros positivos.")
    if (
        type(arena_cols) is not int
        or type(arena_rows) is not int
        or arena_cols < 1
        or arena_rows < 1
    ):
        raise ValueError("As dimensões da arena devem ser inteiros positivos.")
    if width < 32 or height < 9:
        return [_clip("Aumente o terminal para continuar.", width)], []

    minimap = snapshot.get("minimap")
    kill_feed = snapshot.get("kill_feed")
    has_minimap = _valid_minimap(minimap)
    has_kill_feed = isinstance(kill_feed, list)
    show_side_panel = width >= 48 and has_minimap
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

    if show_side_panel:
        panel_separator = " | "
        panel_width = config.MINIMAP_WIDTH
        view_cols = min(
            arena_cols,
            max(1, (width - 1 - len(panel_separator) - panel_width) // 2),
        )
        view_rows = min(arena_rows, max(1, height - 9))
    else:
        panel_separator = ""
        panel_width = 0
        view_cols = min(arena_cols, max(1, (width - 2) // 2))
        reserved_rows = 13 if has_kill_feed else 9
        view_rows = min(arena_rows, max(1, height - reserved_rows))
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
    styled_cells: Dict[Tuple[int, int], Tuple[str, str]] = {}

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
                styled_cells[(1 + y - top, (x - left) * 2)] = ("**", FOOD_COLOR)

    for snake_index, snake in enumerate(snakes):
        if not isinstance(snake, dict):
            continue
        body = snake.get("body")
        if not isinstance(body, list):
            continue
        own = snake.get("id") == player_id
        color_index = snake.get("color")
        if type(color_index) is not int or not 0 <= color_index < len(SNAKE_COLOR_PALETTE):
            color_index = snake_index % len(SNAKE_COLOR_PALETTE)
        snake_color = SNAKE_COLOR_PALETTE[color_index]
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
                styled_cells[(1 + y - top, (x - left) * 2)] = (
                    glyph,
                    snake_color,
                )

    map_rows = minimap if has_minimap else []
    feed_items = (
        [_kill_feed_text(event) for event in kill_feed[-config.KILL_FEED_SIZE :]]
        if has_kill_feed
        else []
    )
    if show_side_panel:
        panel_feed = feed_items or ["Nenhuma eliminação recente."]
        target_map_rows = max(
            1,
            min(len(map_rows), view_rows - 2 - len(panel_feed)),
        )
        panel_map_rows = _compress_minimap(map_rows, target_map_rows)
        for row_index, row in enumerate(grid):
            if row_index == 0:
                panel = "MINIMAPA"
            elif row_index <= len(panel_map_rows):
                panel = panel_map_rows[row_index - 1]
            elif row_index == len(panel_map_rows) + 1:
                panel = "ELIMINAÇÕES"
            else:
                event_index = row_index - len(panel_map_rows) - 2
                panel = (
                    panel_feed[event_index]
                    if event_index < len(panel_feed)
                    else ""
                )
            lines.append(
                "".join(row).ljust(view_cols * 2)
                + panel_separator
                + _clip(panel, panel_width)
            )
    else:
        lines.extend("".join(row) for row in grid)
        if has_kill_feed:
            lines.append("ELIMINAÇÕES RECENTES")
            if feed_items:
                lines.extend(_clip(item, width) for item in feed_items)
            else:
                lines.append("Nenhuma eliminação recente.")

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
    clipped_lines = [_clip(line, width) for line in lines[:height]]
    styles = [
        (line, column, text, color)
        for (line, column), (text, color) in styled_cells.items()
        if line < len(clipped_lines) and column < width - 1
    ]
    return clipped_lines, styles


def render_world(
    snapshot: Dict[str, Any],
    width: int,
    height: int,
    arena_cols: int = 120,
    arena_rows: int = 60,
) -> List[str]:
    """Render a server state or local-game state to plain, control-free lines."""
    lines, _styles = render_world_styled(
        snapshot, width, height, arena_cols=arena_cols, arena_rows=arena_rows
    )
    return lines
