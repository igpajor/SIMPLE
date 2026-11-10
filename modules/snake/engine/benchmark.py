#!/usr/bin/env python3
"""Run the standard headless Snake benchmark without external dependencies."""

from __future__ import annotations

import argparse
import json

from .simulation import run_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark da engine headless do Snake")
    parser.add_argument("--snakes", type=int, default=40)
    parser.add_argument("--ticks", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=2026)
    arguments = parser.parse_args()
    result = run_benchmark(
        snake_count=arguments.snakes,
        ticks=arguments.ticks,
        seed=arguments.seed,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if arguments.snakes == 40 and arguments.ticks == 10_000:
        return 0 if result["p99_tick_ms"] < 20.0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
