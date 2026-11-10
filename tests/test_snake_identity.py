#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path


class BrokerIdentityResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolver_uses_strict_snake_identity_request(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        socket_path = Path(temporary_directory.name) / "broker.sock"
        received = []

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            received.append(json.loads(await reader.readline()))
            writer.write(
                json.dumps(
                    {
                        "ok": True,
                        "linux_uid": os.geteuid(),
                        "binding_id": 45,
                        "user_id": 73,
                        "username": "tripulante",
                    }
                ).encode("utf-8")
                + b"\n"
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=str(socket_path))
        try:
            from modules.snake.server.identity import BrokerIdentityResolver

            resolver = BrokerIdentityResolver(socket_path)
            identity = await resolver(os.geteuid())
        finally:
            server.close()
            await server.wait_closed()
            temporary_directory.cleanup()

        self.assertEqual(
            [
                {
                    "protocol": "simple-snake-v1",
                    "method": "identity.resolve",
                    "linux_uid": os.geteuid(),
                }
            ],
            received,
        )
        self.assertEqual(73, identity["user_id"])

    async def test_resolver_rejects_mismatched_identity_response(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        socket_path = Path(temporary_directory.name) / "broker.sock"

        async def handle(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            writer.write(
                b'{"ok":true,"linux_uid":999,"binding_id":45,"user_id":73,'
                b'"username":"tripulante"}\n'
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=str(socket_path))
        try:
            from modules.snake.server.identity import BrokerIdentityResolver

            with self.assertRaises(ValueError):
                await BrokerIdentityResolver(socket_path)(os.geteuid())
        finally:
            server.close()
            await server.wait_closed()
            temporary_directory.cleanup()

    async def test_resolver_does_not_misreport_broker_failure_as_inactive_user(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        socket_path = Path(temporary_directory.name) / "broker.sock"

        async def handle(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            writer.write(
                '{"ok":false,"error":"Falha ao processar a operação."}\n'.encode(
                    "utf-8"
                )
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=str(socket_path))
        try:
            from modules.snake.server.identity import BrokerIdentityResolver

            with self.assertRaises(ConnectionError):
                await BrokerIdentityResolver(socket_path)(os.geteuid())
        finally:
            server.close()
            await server.wait_closed()
            temporary_directory.cleanup()

    async def test_score_client_sends_bounded_internal_operations(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        socket_path = Path(temporary_directory.name) / "broker.sock"
        received = []

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            received.append(json.loads(await reader.readline()))
            writer.write(b'{"ok":true,"scores":[]}\n')
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=str(socket_path))
        try:
            from modules.snake.server.persistence import BrokerSnakePersistence

            persistence = BrokerSnakePersistence(socket_path)
            await persistence.record_score(os.geteuid(), 45, 30, 2)
            scores = await persistence.leaderboard(limit=10, offset=0)
        finally:
            server.close()
            await server.wait_closed()
            temporary_directory.cleanup()

        self.assertEqual([], scores)
        self.assertEqual(
            [
                {
                    "protocol": "simple-snake-v1",
                    "method": "score.record",
                    "linux_uid": os.geteuid(),
                    "binding_id": 45,
                    "length": 30,
                    "kills": 2,
                },
                {
                    "protocol": "simple-snake-v1",
                    "method": "leaderboard.get",
                    "limit": 10,
                    "offset": 0,
                },
            ],
            received,
        )


if __name__ == "__main__":
    unittest.main()
