#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "broker"))

from simple_broker import Broker, BrokerServer, initialize_database


COMMIT_URL = "https://github.com/student/project/commit/abcdef0123456"


class PhaseTwoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "simple.db"
        self.socket_path = self.root / "broker.sock"
        initialize_database(self.database_path, ROOT_DIR / "database" / "schema.sql")
        self.broker = Broker(self.database_path)
        self.server = BrokerServer(self.socket_path, self.broker)
        os.chmod(self.socket_path, 0o600)
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.socket_path.unlink(missing_ok=True)
        self.temporary_directory.cleanup()

    def broker_call(self, request: dict[str, object]) -> dict[str, object]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(str(self.socket_path))
            connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
            return json.loads(connection.makefile("rb").readline())

    def create_user(self, username: str) -> dict[str, object]:
        return self.broker_call(
            {
                "method": "auth.register",
                "username": username,
                "password": "secure-pass-123",
            }
        )

    def create_exercise(self, code: str) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO exercises (code, name, description) VALUES (?, ?, ?)",
                (code, f"Exercise {code}", "Phase two test"),
            )

    def insert_local_user(self, username: str) -> int:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, "unused-test-hash"),
            )
            return int(cursor.lastrowid)

    def local_submit(
        self, user_id: int, code: str, directory: str = "."
    ) -> subprocess.CompletedProcess[str]:
        script = """
SCRIPT_DIR=$1
DB_PATH=$2
BROKER_MODE=0
CURRENT_USER_ID=$3
source "$SCRIPT_DIR/core/database.sh"
source "$SCRIPT_DIR/modules/exercises.sh"
exercise_submit "$4" "$5" "$6"
"""
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "phase2-local-submit",
                str(ROOT_DIR),
                str(self.database_path),
                str(user_id),
                code,
                COMMIT_URL,
                directory,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def local_review(
        self, reviewer_id: int, slot_id: int
    ) -> subprocess.CompletedProcess[str]:
        script = """
SCRIPT_DIR=$1
DB_PATH=$2
BROKER_MODE=0
CURRENT_USER_ID=$3
source "$SCRIPT_DIR/core/database.sh"
source "$SCRIPT_DIR/modules/evaluations.sh"
evaluation_record_review "$4" 4 "phase two review"
"""
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "phase2-local-review",
                str(ROOT_DIR),
                str(self.database_path),
                str(reviewer_id),
                str(slot_id),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def point_balance(self, user_id: int) -> int:
        with closing(sqlite3.connect(self.database_path)) as connection:
            return int(
                connection.execute(
                    """
                    SELECT COALESCE((SELECT SUM(amount) FROM points_ledger WHERE user_id = ?), 0)
                         + COALESCE((SELECT SUM(amount) FROM admin_point_adjustments WHERE user_id = ?), 0)
                    """,
                    (user_id, user_id),
                ).fetchone()[0]
            )

    def test_concurrent_submissions_cannot_overdraw_or_duplicate_active_attempt(self) -> None:
        for iteration in range(20):
            with self.subTest(mode="broker", iteration=iteration):
                user = self.create_user(f"broker-race-{iteration:02d}")
                self.assertTrue(user["ok"], user)
                code = f"BROKER-RACE-{iteration:02d}"
                self.create_exercise(code)
                request = {
                    "method": "submissions.create",
                    "token": user["token"],
                    "exercise_code": code,
                    "commit_url": COMMIT_URL,
                    "delivery_directory": ".",
                }
                barrier = threading.Barrier(2)

                def submit_broker() -> dict[str, object]:
                    barrier.wait(timeout=5)
                    return self.broker_call(request)

                with ThreadPoolExecutor(max_workers=2) as executor:
                    responses = list(executor.map(lambda _index: submit_broker(), range(2)))

                self.assertEqual(1, sum(bool(response["ok"]) for response in responses), responses)
                user_id = int(user["user_id"])
                with closing(sqlite3.connect(self.database_path)) as connection:
                    active_submissions, open_slots, debits = connection.execute(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM submissions
                             WHERE user_id = ? AND status IN ('pending', 'approved')),
                            (SELECT COUNT(*) FROM evaluation_slots slot
                             JOIN submissions s ON s.id = slot.submission_id
                             WHERE s.user_id = ? AND slot.status = 'open'),
                            (SELECT COUNT(*) FROM points_ledger
                             WHERE user_id = ? AND entry_type = 'slot_opened')
                        """,
                        (user_id, user_id, user_id),
                    ).fetchone()
                self.assertGreaterEqual(self.point_balance(user_id), 0)
                self.assertLessEqual(active_submissions, 1)
                self.assertLessEqual(open_slots, 1)
                self.assertLessEqual(debits, 1)

            with self.subTest(mode="local", iteration=iteration):
                user_id = self.insert_local_user(f"local-race-{iteration:02d}")
                code = f"LOCAL-RACE-{iteration:02d}"
                self.create_exercise(code)
                barrier = threading.Barrier(2)

                def submit_local() -> subprocess.CompletedProcess[str]:
                    barrier.wait(timeout=5)
                    return self.local_submit(user_id, code)

                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(lambda _index: submit_local(), range(2)))

                self.assertEqual(
                    1,
                    sum(result.returncode == 0 for result in results),
                    [(result.returncode, result.stdout, result.stderr) for result in results],
                )
                with closing(sqlite3.connect(self.database_path)) as connection:
                    active_submissions, open_slots, debits = connection.execute(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM submissions
                             WHERE user_id = ? AND status IN ('pending', 'approved')),
                            (SELECT COUNT(*) FROM evaluation_slots slot
                             JOIN submissions s ON s.id = slot.submission_id
                             WHERE s.user_id = ? AND slot.status = 'open'),
                            (SELECT COUNT(*) FROM points_ledger
                             WHERE user_id = ? AND entry_type = 'slot_opened')
                        """,
                        (user_id, user_id, user_id),
                    ).fetchone()
                self.assertGreaterEqual(self.point_balance(user_id), 0)
                self.assertLessEqual(active_submissions, 1)
                self.assertLessEqual(open_slots, 1)
                self.assertLessEqual(debits, 1)

    def test_failed_submission_or_review_rolls_back_all_side_effects(self) -> None:
        broker_owner = self.create_user("rollback-broker-owner")
        self.assertTrue(broker_owner["ok"], broker_owner)
        self.create_exercise("ROLLBACK-BROKER-SUBMIT")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TRIGGER test_abort_broker_slot
                BEFORE INSERT ON evaluation_slots
                BEGIN
                    SELECT RAISE(ABORT, 'test slot insertion failure');
                END
                """
            )
        failed_broker_submission = self.broker_call(
            {
                "method": "submissions.create",
                "token": broker_owner["token"],
                "exercise_code": "ROLLBACK-BROKER-SUBMIT",
                "commit_url": COMMIT_URL,
                "delivery_directory": ".",
            }
        )
        self.assertFalse(failed_broker_submission["ok"])
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute("DROP TRIGGER test_abort_broker_slot")
            self.assertEqual(
                (0, 0, 0),
                connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM submissions WHERE user_id = ?),
                        (SELECT COUNT(*) FROM evaluation_slots),
                        (SELECT COUNT(*) FROM points_ledger
                         WHERE user_id = ? AND entry_type = 'slot_opened')
                    """,
                    (broker_owner["user_id"], broker_owner["user_id"]),
                ).fetchone(),
            )
        self.assertEqual(1, self.point_balance(int(broker_owner["user_id"])))

        local_owner_id = self.insert_local_user("rollback-local-owner")
        self.create_exercise("ROLLBACK-LOCAL-SUBMIT")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TRIGGER test_abort_local_slot
                BEFORE INSERT ON evaluation_slots
                BEGIN
                    SELECT RAISE(ABORT, 'test slot insertion failure');
                END
                """
            )
        failed_local_submission = self.local_submit(
            local_owner_id, "ROLLBACK-LOCAL-SUBMIT"
        )
        self.assertNotEqual(0, failed_local_submission.returncode)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute("DROP TRIGGER test_abort_local_slot")
            self.assertEqual(
                (0, 0, 0),
                connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM submissions WHERE user_id = ?),
                        (SELECT COUNT(*) FROM evaluation_slots),
                        (SELECT COUNT(*) FROM points_ledger
                         WHERE user_id = ? AND entry_type = 'slot_opened')
                    """,
                    (local_owner_id, local_owner_id),
                ).fetchone(),
            )
        self.assertEqual(1, self.point_balance(local_owner_id))

        broker_review_owner = self.create_user("rollback-review-owner")
        broker_reviewer = self.create_user("rollback-review-reviewer")
        self.assertTrue(broker_review_owner["ok"], broker_review_owner)
        self.assertTrue(broker_reviewer["ok"], broker_reviewer)
        self.create_exercise("ROLLBACK-BROKER-REVIEW")
        broker_submission = self.broker_call(
            {
                "method": "submissions.create",
                "token": broker_review_owner["token"],
                "exercise_code": "ROLLBACK-BROKER-REVIEW",
                "commit_url": COMMIT_URL,
                "delivery_directory": ".",
            }
        )
        self.assertTrue(broker_submission["ok"], broker_submission)
        self.assertTrue(
            self.broker_call(
                {
                    "method": "evaluations.accept",
                    "token": broker_reviewer["token"],
                    "slot_id": str(broker_submission["slot_id"]),
                }
            )["ok"]
        )
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TRIGGER test_abort_broker_review_reward
                BEFORE INSERT ON points_ledger
                WHEN NEW.entry_type = 'review_completed'
                BEGIN
                    SELECT RAISE(ABORT, 'test review reward failure');
                END
                """
            )
        failed_broker_review = self.broker_call(
            {
                "method": "evaluations.review.submit",
                "token": broker_reviewer["token"],
                "slot_id": str(broker_submission["slot_id"]),
                "score": "4",
                "feedback": "rollback test",
            }
        )
        self.assertFalse(failed_broker_review["ok"])
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute("DROP TRIGGER test_abort_broker_review_reward")
            self.assertEqual(
                (0, "pending", "open", None, 0),
                connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM reviews),
                        s.status,
                        slot.status,
                        slot.reviewer_id,
                        (SELECT COUNT(*) FROM points_ledger
                         WHERE user_id = ? AND entry_type = 'review_completed')
                    FROM evaluation_slots slot
                    JOIN submissions s ON s.id = slot.submission_id
                    WHERE slot.id = ?
                    """,
                    (broker_reviewer["user_id"], broker_submission["slot_id"]),
                ).fetchone(),
            )
        self.assertEqual(1, self.point_balance(int(broker_reviewer["user_id"])))

        local_review_owner_id = self.insert_local_user("rollback-local-review-owner")
        local_reviewer_id = self.insert_local_user("rollback-local-review-reviewer")
        self.create_exercise("ROLLBACK-LOCAL-REVIEW")
        local_submission = self.local_submit(
            local_review_owner_id, "ROLLBACK-LOCAL-REVIEW"
        )
        self.assertEqual(0, local_submission.returncode, local_submission.stderr)
        with closing(sqlite3.connect(self.database_path)) as connection:
            local_slot_id = int(
                connection.execute(
                    """
                    SELECT slot.id FROM evaluation_slots slot
                    JOIN submissions s ON s.id = slot.submission_id
                    WHERE s.user_id = ?
                    """,
                    (local_review_owner_id,),
                ).fetchone()[0]
            )
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TRIGGER test_abort_local_review_reward
                BEFORE INSERT ON points_ledger
                WHEN NEW.entry_type = 'review_completed'
                BEGIN
                    SELECT RAISE(ABORT, 'test review reward failure');
                END
                """
            )
        failed_local_review = self.local_review(local_reviewer_id, local_slot_id)
        self.assertNotEqual(0, failed_local_review.returncode)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute("DROP TRIGGER test_abort_local_review_reward")
            self.assertEqual(
                (0, "pending", "open", None, 0),
                connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM reviews),
                        s.status,
                        slot.status,
                        slot.reviewer_id,
                        (SELECT COUNT(*) FROM points_ledger
                         WHERE user_id = ? AND entry_type = 'review_completed')
                    FROM evaluation_slots slot
                    JOIN submissions s ON s.id = slot.submission_id
                    WHERE slot.id = ?
                    """,
                    (local_reviewer_id, local_slot_id),
                ).fetchone(),
            )
        self.assertEqual(1, self.point_balance(local_reviewer_id))

    def test_uid_binding_tui_does_not_claim_proxy_peer_credentials_are_unimplemented(self) -> None:
        admin = self.create_user("phase2-tui-admin")
        self.assertTrue(admin["ok"], admin)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?", (admin["user_id"],)
            )
        environment = os.environ.copy()
        environment.update(
            {
                "SIMPLE_MODE": "broker",
                "BROKER_SOCKET": str(self.socket_path),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        process = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input=(
                "2\nphase2-tui-admin\nsecure-pass-123\n"
                "5\n6\n0\n0\n0\n"
            ),
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("VÍNCULOS DE IDENTIDADE LINUX", process.stdout)
        normalized_output = " ".join(process.stdout.split())
        self.assertNotIn(
            "o proxy SO_PEERCRED não está implementado", normalized_output
        )
        self.assertIn(
            "O proxy identifica o UID real com SO_PEERCRED e exige vínculo ativo",
            normalized_output,
        )


if __name__ == "__main__":
    unittest.main()
