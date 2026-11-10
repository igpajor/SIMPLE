#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional


class SnakeServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temporary_directory.name) / "snake.sock"
        self.resolved_uids = []
        self.identity_active = True
        from modules.snake.server.server import SnakeServer

        self.server = SnakeServer(
            identity_resolver=self.resolve_identity,
            bot_count=0,
            tick_interval=0.01,
            identity_revalidation_interval=0.5,
        )
        await self.server.start_unix(self.socket_path)
        self.connections = []

    async def asyncTearDown(self) -> None:
        for _reader, writer in self.connections:
            writer.close()
            await writer.wait_closed()
        await self.server.close()
        self.temporary_directory.cleanup()

    async def resolve_identity(self, linux_uid: int) -> Optional[Dict[str, object]]:
        self.resolved_uids.append(linux_uid)
        if not self.identity_active:
            return None
        return {
            "linux_uid": linux_uid,
            "binding_id": 45,
            "user_id": 73,
            "username": "tripulante",
        }

    async def connect_and_hello(self, nick: str = "Navegante"):
        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        self.connections.append((reader, writer))
        hello = {
            "t": "hello",
            "v": 1,
            "cols": 80,
            "rows": 24,
            "nick": nick,
        }
        writer.write(json.dumps(hello).encode("utf-8") + b"\n")
        await writer.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), 3))
        return reader, writer, response

    async def test_unix_peer_uid_resolves_identity_and_receives_world_state(self) -> None:
        _reader, _writer, welcome = await self.connect_and_hello()

        self.assertEqual("welcome", welcome["t"])
        self.assertEqual(1, welcome["v"])
        self.assertEqual({"cols": 120, "rows": 60}, welcome["arena"])
        self.assertNotEqual("73", welcome["player_id"])
        self.assertNotIn("user_id", welcome)
        self.assertNotIn("linux_uid", welcome)
        self.assertEqual([os.geteuid()], self.resolved_uids)

        state = json.loads(
            await asyncio.wait_for(_reader.readline(), 1)
        )
        self.assertEqual("state", state["t"])
        self.assertEqual(welcome["player_id"], state["player"]["id"])
        self.assertGreaterEqual(state["snakes"][0]["color"], 0)
        self.assertLess(state["snakes"][0]["color"], 6)
        self.assertEqual(10, len(state["minimap"]))
        self.assertTrue(all(len(row) == 20 for row in state["minimap"]))
        self.assertEqual([], state["kill_feed"])

    async def test_game_socket_file_mode_excludes_world_access(self) -> None:
        socket_mode = stat.S_IMODE(self.socket_path.stat().st_mode)

        self.assertEqual(0o660, socket_mode)

    async def test_kill_feed_is_bounded_and_uses_server_known_nicknames(self) -> None:
        self.server.player_nicks.update(
            {
                "killer": "Rival",
                "victim-1": "Navegante",
                "victim-2": "Bot 01",
                "victim-3": "Bot 02",
                "victim-4": "Bot 03",
            }
        )
        self.server.world.kill_events = [
            ("victim-1", "killer"),
            ("victim-2", None),
            ("victim-3", "killer"),
            ("victim-4", "killer"),
        ]

        self.server._capture_kill_events()

        self.assertEqual(
            [
                {"killer": None, "victim": "Bot 01"},
                {"killer": "Rival", "victim": "Bot 02"},
                {"killer": "Rival", "victim": "Bot 03"},
            ],
            self.server._kill_feed,
        )

    async def test_tick_publishes_death_in_the_shared_arena_kill_feed(self) -> None:
        reader, _writer, welcome = await self.connect_and_hello()
        await reader.readline()
        snake = self.server.world.snakes[welcome["player_id"]]
        snake.body = [(119, 20), (118, 20), (117, 20), (116, 20)]
        snake.direction = "R"

        while True:
            response = json.loads(await asyncio.wait_for(reader.readline(), 1))
            if response["t"] == "state" and response["kill_feed"]:
                break

        self.assertEqual(
            {"killer": None, "victim": "Navegante"},
            response["kill_feed"][-1],
        )

    async def test_unbound_peer_is_rejected_without_creating_a_session(self) -> None:
        async def unbound_identity(_linux_uid: int):
            return None

        await self.server.close()
        from modules.snake.server.server import SnakeServer

        self.server = SnakeServer(
            identity_resolver=unbound_identity,
            bot_count=0,
            tick_interval=0.01,
        )
        await self.server.start_unix(self.socket_path)
        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        self.connections.append((reader, writer))
        writer.write(b'{"t":"hello","v":1,"cols":80,"rows":24}\n')
        await writer.drain()

        response = json.loads(await asyncio.wait_for(reader.readline(), 1))
        self.assertEqual("error", response["t"])
        self.assertEqual("unauthorized", response["code"])
        self.assertEqual({}, self.server.sessions)

    async def test_new_session_replaces_the_existing_session_for_same_account(self) -> None:
        old_reader, _old_writer, first = await self.connect_and_hello("Primeiro")
        _new_reader, _new_writer, second = await self.connect_and_hello("Segundo")

        self.assertEqual("welcome", first["t"], first)
        self.assertEqual("welcome", second["t"])
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertEqual(1, len(self.server.sessions))
        while True:
            line = await asyncio.wait_for(old_reader.readline(), 1)
            if not line:
                break

    async def test_invalid_messages_before_hello_close_after_three_failures(self) -> None:
        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        self.connections.append((reader, writer))

        for _ in range(3):
            writer.write(b'{"t":"ping"}\n')
            await writer.drain()
            response = json.loads(await asyncio.wait_for(reader.readline(), 1))
            self.assertEqual("invalid_request", response["code"])

        self.assertEqual(b"", await asyncio.wait_for(reader.readline(), 1))
        self.assertEqual({}, self.server.sessions)

    async def test_protocol_version_mismatch_is_reported_as_unsupported(self) -> None:
        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        self.connections.append((reader, writer))
        writer.write(b'{"t":"hello","v":2,"cols":80,"rows":24}\n')
        await writer.drain()

        response = json.loads(await asyncio.wait_for(reader.readline(), 1))
        self.assertEqual("version_unsupported", response["code"])
        self.assertEqual(b"", await asyncio.wait_for(reader.readline(), 1))

    async def test_revoked_identity_ends_an_active_session(self) -> None:
        reader, _writer, welcome = await self.connect_and_hello()
        self.assertEqual("welcome", welcome["t"])
        await asyncio.wait_for(reader.readline(), 1)
        self.identity_active = False

        while True:
            response = json.loads(await asyncio.wait_for(reader.readline(), 1))
            if response["t"] == "error":
                self.assertEqual("unauthorized", response["code"])
                break

        self.assertEqual(b"", await asyncio.wait_for(reader.readline(), 1))
        self.assertEqual({}, self.server.sessions)

    async def test_shutdown_preserves_a_replacement_at_the_socket_path(self) -> None:
        self.socket_path.unlink()
        self.socket_path.write_text("arquivo substituto", encoding="utf-8")

        await self.server.close()

        self.assertEqual(
            "arquivo substituto", self.socket_path.read_text(encoding="utf-8")
        )

    async def test_server_persists_death_and_includes_persisted_ranking(self) -> None:
        from modules.snake.server.server import SnakeServer

        class FakePersistence:
            def __init__(self):
                self.recorded = []

            async def record_score(self, linux_uid, binding_id, length, kills):
                self.recorded.append((linux_uid, binding_id, length, kills))

            async def leaderboard(self, limit, offset):
                return [
                    {
                        "rank": 1,
                        "username": "tripulante",
                        "best_length": 42,
                        "kills_total": 3,
                        "deaths_total": 2,
                        "games": 2,
                    }
                ]

        persistence = FakePersistence()
        await self.server.close()
        self.server = SnakeServer(
            identity_resolver=self.resolve_identity,
            score_persister=persistence,
            bot_count=0,
            tick_interval=0.01,
        )
        await self.server.start_unix(self.socket_path)
        reader, _writer, welcome = await self.connect_and_hello()
        session = self.server.sessions[73]
        snake = self.server.world.snakes[session.player_id]
        snake.kills = 3
        snake.alive = False
        self.server._broadcast_state_changes()
        self.server.world.remove_snake(session.player_id)
        await asyncio.sleep(0)
        snapshot = self.server._snapshot(session, snake)

        await asyncio.sleep(0.01)
        self.assertEqual([(os.geteuid(), 45, 4, 3)], persistence.recorded)
        self.assertEqual("tripulante", snapshot["records"][0]["username"])
        self.assertEqual("welcome", welcome["t"])
        await reader.readline()

    async def test_full_snapshot_queue_discards_old_state_without_blocking_tick(self) -> None:
        from modules.snake.server.server import ClientSession

        class SlowWriter:
            closed = False

            def close(self) -> None:
                self.closed = True

        session = ClientSession(
            linux_uid=1000,
            binding_id=45,
            user_id=73,
            session_id="session",
            player_id="player",
            nick="Navegante",
            cols=80,
            rows=24,
            writer=SlowWriter(),
            identity_checked_at=0.0,
        )
        session.outgoing.put_nowait((b"estado antigo 1\n", "state"))
        session.outgoing.put_nowait((b"estado antigo 2\n", "state"))
        previous_tick = self.server.world.tick_count

        self.server._enqueue(session, {"t": "state", "tick": 3})
        await asyncio.sleep(0.04)

        queued = [session.outgoing.get_nowait() for _ in range(session.outgoing.qsize())]
        self.assertEqual(2, len(queued))
        self.assertEqual(b"estado antigo 2\n", queued[0][0])
        self.assertIn(b'"tick":3', queued[1][0])
        self.assertGreater(self.server.world.tick_count, previous_tick)


if __name__ == "__main__":
    unittest.main()
