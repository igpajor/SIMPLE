#!/usr/bin/env python3

from __future__ import annotations

import unittest


class SnakeProtocolContractTests(unittest.TestCase):
    def parse(self, raw: bytes):
        from modules.snake.server.protocol import parse_client_message

        return parse_client_message(raw)

    def test_accepts_versioned_hello_without_using_client_identity(self) -> None:
        message = self.parse(
            b'{"t":"hello","v":1,"nick":"Navegante","cols":80,"rows":24}\n'
        )

        self.assertEqual("hello", message["t"])
        self.assertNotIn("user_id", message)
        self.assertNotIn("username", message)

    def test_accepts_direction_boost_resize_ping_and_bye_messages(self) -> None:
        messages = (
            b'{"t":"dir","d":"U"}\n',
            b'{"t":"boost","on":true}\n',
            b'{"t":"resize","cols":80,"rows":24}\n',
            b'{"t":"ping"}\n',
            b'{"t":"bye"}\n',
        )

        self.assertEqual(
            ["dir", "boost", "resize", "ping", "bye"],
            [self.parse(message)["t"] for message in messages],
        )

    def test_rejects_malformed_json_and_duplicate_keys(self) -> None:
        for raw in (
            b'{"t":"ping"\n',
            b'{"t":"ping","t":"bye"}\n',
            b'{"t":"ping"}',
            b"\xff\n",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.parse(raw)

    def test_rejects_messages_larger_than_one_kibibyte(self) -> None:
        with self.assertRaises(ValueError):
            self.parse(b'{"t":"hello","v":1,"nick":"' + b"x" * 1024 + b'"}\n')

    def test_rejects_unknown_types_and_invalid_direction(self) -> None:
        for raw in (b'{"t":"teleport"}\n', b'{"t":"dir","d":"X"}\n'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.parse(raw)

    def test_rejects_control_characters_in_nickname(self) -> None:
        for raw in (
            b'{"t":"hello","v":1,"nick":"ana\\u001b[31m"}\n',
            b'{"t":"hello","v":1,"nick":"ana\\u009b"}\n',
            b'{"t":"hello","v":1,"nick":"ana\\u202e"}\n',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.parse(raw)

    def test_rejects_invalid_json_types_and_viewport_ranges(self) -> None:
        for raw in (
            b'{"t":"hello","v":true,"cols":80,"rows":24}\n',
            b'{"t":"hello","v":1,"cols":true,"rows":24}\n',
            b'{"t":"hello","v":1,"cols":9,"rows":24}\n',
            b'{"t":"boost","on":1}\n',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.parse(raw)

    def test_rejects_client_supplied_identity_and_extra_fields(self) -> None:
        for raw in (
            b'{"t":"hello","v":1,"cols":80,"rows":24,"user_id":42}\n',
            b'{"t":"ping","username":"admin"}\n',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.parse(raw)

    def test_rate_limiter_allows_thirty_messages_per_second(self) -> None:
        from modules.snake.server.protocol import MessageRateLimiter

        limiter = MessageRateLimiter(max_messages=30, interval_seconds=1.0)
        self.assertTrue(all(limiter.allow(0.0) for _ in range(30)))
        self.assertFalse(limiter.allow(0.0))
        self.assertTrue(limiter.allow(1.0))


if __name__ == "__main__":
    unittest.main()
