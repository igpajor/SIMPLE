#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT_DIR / "broker"))

from simple_broker import Broker, initialize_database


class PhaseThreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "simple.db"
        initialize_database(self.database_path, ROOT_DIR / "database" / "schema.sql")
        self.openssl_log = self.root / "openssl.jsonl"
        self.shim_directory = self.root / "bin"
        self.shim_directory.mkdir()
        self.real_openssl = shutil.which("openssl")
        if self.real_openssl is None:
            self.fail("OpenSSL necessário para os testes de autenticação")
        shim = self.shim_directory / "openssl"
        shim.write_text(
            """#!/usr/bin/env python3
import json
import os
import subprocess
import sys

arguments = sys.argv[1:]
payload = sys.stdin.buffer.read()
with open(os.environ["SIMPLE_OPENSSL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": arguments, "stdin": payload.decode("utf-8")}) + "\\n")
result = subprocess.run(
    [os.environ["SIMPLE_REAL_OPENSSL"], *arguments],
    input=payload,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
""",
            encoding="utf-8",
        )
        shim.chmod(0o700)
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "PATH": f"{self.shim_directory}{os.pathsep}{os.environ['PATH']}",
                "SIMPLE_OPENSSL_LOG": str(self.openssl_log),
                "SIMPLE_REAL_OPENSSL": self.real_openssl,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_password_is_passed_to_openssl_stdin_not_argv_in_local_and_broker(self) -> None:
        password = "phase3-secret-pass"
        local_script = """
SCRIPT_DIR=$1
DB_PATH=$2
source "$SCRIPT_DIR/core/database.sh"
source "$SCRIPT_DIR/core/auth.sh"
source "$SCRIPT_DIR/modules/users.sh"
db_init
IFS= read -r -s password
auth_register local-shim-user "$password"
auth_login local-shim-user "$password"
"""
        local_result = subprocess.run(
            [
                "bash",
                "-c",
                local_script,
                "phase3-local-auth",
                str(ROOT_DIR),
                str(self.database_path),
            ],
            cwd=ROOT_DIR,
            env=self.environment,
            input=f"{password}\n",
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, local_result.returncode, local_result.stderr)

        with patch.dict(os.environ, self.environment):
            broker = Broker(self.database_path)
            registered = broker.dispatch(
                {"method": "auth.register", "username": "broker-shim-user", "password": password}
            )
            self.assertTrue(registered["ok"], registered)
            login = broker.dispatch(
                {"method": "auth.login", "username": "broker-shim-user", "password": password}
            )
            self.assertTrue(login["ok"], login)

        invocations = [
            json.loads(line) for line in self.openssl_log.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(4, len(invocations), invocations)
        self.assertTrue(all(password not in call["argv"] for call in invocations), invocations)
        self.assertEqual([password] * 4, [call["stdin"] for call in invocations])
        self.assertTrue(all("-stdin" in call["argv"] for call in invocations), invocations)
        with closing(sqlite3.connect(self.database_path)) as connection:
            usernames = {
                row[0]
                for row in connection.execute("SELECT username FROM users")
            }
        self.assertTrue({"local-shim-user", "broker-shim-user"}.issubset(usernames))

    def test_local_login_rejects_inactive_user(self) -> None:
        password = "phase3-inactive-secret"
        script = """
SCRIPT_DIR=$1
DB_PATH=$2
source "$SCRIPT_DIR/core/database.sh"
source "$SCRIPT_DIR/core/auth.sh"
source "$SCRIPT_DIR/modules/users.sh"
db_init
IFS= read -r -s password
auth_register inactive-phase3-user "$password"
db_query "UPDATE users SET is_active = 0 WHERE username = 'inactive-phase3-user';"
auth_login inactive-phase3-user "$password"
"""
        result = subprocess.run(
            [
                "bash",
                "-c",
                script,
                "phase3-inactive-login",
                str(ROOT_DIR),
                str(self.database_path),
            ],
            cwd=ROOT_DIR,
            env=self.environment,
            input=f"{password}\n",
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertIn("Usuário ou senha inválidos.", result.stdout)


if __name__ == "__main__":
    unittest.main()
