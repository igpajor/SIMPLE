#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "broker"))

from simple_broker import Broker, BrokerServer, initialize_database
from simple_proxy import ProxyServer, ProxyService, _prepare_socket_path


class ProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "simple.db"
        self.broker_socket_path = self.root / "broker.sock"
        self.public_directory_temporary = tempfile.TemporaryDirectory()
        self.public_directory = Path(self.public_directory_temporary.name)
        os.chmod(self.public_directory, 0o710)
        self.public_socket_path = self.public_directory / "proxy.sock"
        initialize_database(self.database_path, ROOT_DIR / "database" / "schema.sql")
        self.broker = Broker(self.database_path)
        self.broker_server = BrokerServer(self.broker_socket_path, self.broker)
        os.chmod(self.broker_socket_path, 0o600)
        self.broker_thread = threading.Thread(target=self.broker_server.serve_forever)
        self.broker_thread.start()
        self.proxy_service = ProxyService(self.broker_socket_path)
        self.proxy_server = ProxyServer(self.public_socket_path, self.proxy_service)
        os.chmod(self.public_socket_path, 0o660)
        os.chown(self.public_socket_path, os.geteuid(), os.getegid())
        self.proxy_thread = threading.Thread(target=self.proxy_server.serve_forever)
        self.proxy_thread.start()

    def tearDown(self) -> None:
        self.proxy_server.shutdown()
        self.proxy_server.server_close()
        self.proxy_thread.join(timeout=5)
        self.broker_server.shutdown()
        self.broker_server.server_close()
        self.broker_thread.join(timeout=5)
        self.public_socket_path.unlink(missing_ok=True)
        self.broker_socket_path.unlink(missing_ok=True)
        self.public_directory_temporary.cleanup()
        self.temporary_directory.cleanup()

    def broker_call(self, request: dict[str, object]) -> dict[str, object]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(self.broker_socket_path))
            connection.sendall(json.dumps(request).encode() + b"\n")
            return json.loads(connection.makefile("rb").readline())

    def proxy_call(self, request: dict[str, object]) -> dict[str, object]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(self.public_socket_path))
            connection.sendall(json.dumps(request).encode() + b"\n")
            return json.loads(connection.makefile("rb").readline())

    def create_user(self, username: str) -> dict[str, object]:
        return self.broker_call(
            {
                "method": "auth.register",
                "username": username,
                "password": "secure-pass-123",
            }
        )

    def bind_for_test(self, linux_uid: int, user_id: int) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO identity_bindings (linux_uid, user_id) VALUES (?, ?)",
                (linux_uid, user_id),
            )

    @unittest.skipIf(os.geteuid() == 0, "use the root-only multi-UID integration test")
    def test_proxy_uses_kernel_uid_and_preserves_password_authentication(self) -> None:
        uid = os.geteuid()
        user = self.create_user("proxy-student")
        self.bind_for_test(uid, int(user["user_id"]))

        rejected_other_account = self.proxy_call(
            {
                "method": "auth.login",
                "username": "different-account",
                "password": "secure-pass-123",
            }
        )
        self.assertIn("Usuário ou senha", rejected_other_account["error"])
        rejected_registration = self.proxy_call(
            {
                "method": "auth.register",
                "username": "new-user",
                "password": "secure-pass-123",
            }
        )
        self.assertFalse(rejected_registration["ok"])
        login = self.proxy_call(
            {
                "method": "auth.login",
                "username": "proxy-student",
                "password": "secure-pass-123",
            }
        )
        self.assertTrue(login["ok"], login)
        self.assertEqual(user["user_id"], login["user_id"])

        profile = self.proxy_call({"method": "profile.get", "token": login["token"]})
        self.assertEqual("proxy-student", profile["username"])
        chat_status = self.proxy_call(
            {"method": "chat.status", "token": login["token"]}
        )
        self.assertTrue(chat_status["ok"], chat_status)
        self.assertIn("proxy-student", chat_status["online_users"])
        chat_sent = self.proxy_call(
            {
                "method": "chat.send",
                "token": login["token"],
                "message": "Mensagem via proxy",
            }
        )
        self.assertTrue(chat_sent["ok"], chat_sent)
        chat_history = self.proxy_call(
            {
                "method": "chat.messages",
                "token": login["token"],
                "after_id": "0",
            }
        )
        self.assertTrue(chat_history["ok"], chat_history)
        self.assertEqual("Mensagem via proxy", chat_history["messages"][0]["body"])
        denied_admin = self.proxy_call(
            {"method": "admin.users.list", "token": login["token"]}
        )
        self.assertIn("Acesso administrativo", denied_admin["error"])
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?", (user["user_id"],)
            )
        admin_login = self.proxy_call(
            {
                "method": "auth.login",
                "username": "proxy-student",
                "password": "secure-pass-123",
            }
        )
        self.assertTrue(admin_login["ok"], admin_login)
        allowed_admin = self.proxy_call(
            {"method": "admin.users.list", "token": admin_login["token"]}
        )
        self.assertTrue(allowed_admin["ok"], allowed_admin)
        injected_uid = self.proxy_call(
            {
                "method": "profile.get",
                "token": login["token"],
                "uid": str(uid + 1),
            }
        )
        self.assertIn("Campos inválidos", injected_uid["error"])
        injected_account = self.proxy_call(
            {
                "method": "profile.get",
                "token": login["token"],
                "account_id": str(user["user_id"] + 1),
            }
        )
        self.assertIn("Campos inválidos", injected_account["error"])
        internal_operation = self.proxy_call(
            {
                "protocol": "simple-proxy-v1",
                "method": "identity.resolve",
                "linux_uid": uid,
            }
        )
        self.assertFalse(internal_operation["ok"])
        changed = self.proxy_call(
            {
                "method": "auth.change_password",
                "token": login["token"],
                "password": "replacement-pass",
            }
        )
        self.assertTrue(changed["ok"], changed)
        self.assertNotEqual(login["token"], changed["token"])
        self.assertFalse(
            self.proxy_call({"method": "profile.get", "token": login["token"]})["ok"]
        )
        self.assertTrue(
            self.proxy_call(
                {"method": "profile.get", "token": changed["token"]}
            )["ok"]
        )

        environment = os.environ.copy()
        environment.update(
            {
                "SIMPLE_MODE": "proxy",
                "PROXY_SOCKET": str(self.public_socket_path),
            }
        )
        tui = subprocess.run(
            ["bash", str(ROOT_DIR / "simple.sh")],
            input="2\nproxy-student\nreplacement-pass\n0\n0\n0\n",
            text=True,
            capture_output=True,
            cwd=ROOT_DIR,
            env=environment,
            check=False,
            timeout=15,
        )
        self.assertEqual(0, tui.returncode, tui.stderr)
        self.assertIn("Acesso autorizado.", tui.stdout)
        self.assertIn("[1] Catálogo de exercícios", tui.stdout)
        self.assertNotIn("Acesso SQLite local bloqueado", tui.stderr)

    @unittest.skipIf(os.geteuid() == 0, "use the root-only multi-UID integration test")
    def test_proxy_rejects_unbound_revoked_and_disabled_identities(self) -> None:
        uid = os.geteuid()
        unbound = self.proxy_call(
            {
                "method": "auth.login",
                "username": "unknown",
                "password": "secure-pass-123",
            }
        )
        self.assertIn("sem vínculo ativo", unbound["error"])

        user = self.create_user("revocation-student")
        self.bind_for_test(uid, int(user["user_id"]))
        login = self.proxy_call(
            {
                "method": "auth.login",
                "username": "revocation-student",
                "password": "secure-pass-123",
            }
        )
        self.assertTrue(login["ok"], login)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE identity_bindings SET is_active = 0 WHERE linux_uid = ?",
                (uid,),
            )
        revoked = self.proxy_call(
            {"method": "profile.get", "token": login["token"]}
        )
        self.assertIn("sem vínculo ativo", revoked["error"])

        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO identity_bindings (linux_uid, user_id) VALUES (?, ?)",
                (uid, user["user_id"]),
            )
        rebound = self.proxy_call(
            {"method": "profile.get", "token": login["token"]}
        )
        self.assertIn("Sessão inválida para esta identidade Linux", rebound["error"])
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_active = 0 WHERE id = ?", (user["user_id"],)
            )
        disabled = self.proxy_call(
            {
                "method": "auth.login",
                "username": "revocation-student",
                "password": "secure-pass-123",
            }
        )
        self.assertIn("desativada", disabled["error"])

    @unittest.skipIf(os.geteuid() == 0, "use the root-only multi-UID integration test")
    def test_proxy_does_not_accept_private_identity_operation_as_public_request(self) -> None:
        user = self.create_user("internal-op-user")
        self.bind_for_test(os.geteuid(), int(user["user_id"]))
        response = self.broker_call(
            {
                "method": "proxy.identity.resolve",
                "linux_uid": os.geteuid(),
            }
        )
        self.assertFalse(response["ok"])
        self.assertEqual("Operação não suportada.", response["error"])

    @unittest.skipIf(os.geteuid() == 0, "use the root-only multi-UID integration test")
    def test_proxy_limits_protocol_and_handles_disconnects_and_concurrency(self) -> None:
        uid = os.geteuid()
        user = self.create_user("parallel-proxy-user")
        self.bind_for_test(uid, int(user["user_id"]))
        login = self.proxy_call(
            {
                "method": "auth.login",
                "username": "parallel-proxy-user",
                "password": "secure-pass-123",
            }
        )
        self.assertTrue(login["ok"], login)
        request = {"method": "profile.get", "token": login["token"]}

        def concurrent_call(_index: int) -> dict[str, object]:
            return self.proxy_call(request)

        with ThreadPoolExecutor(max_workers=64) as executor:
            responses = list(executor.map(concurrent_call, range(64)))
        self.assertTrue(all(response["ok"] for response in responses), responses)

        malformed_requests = (
            b'{"method":"auth.logout","token":"first","token":"second"}\n',
            b'{"method":"catalog.list","token":"token","value":NaN}\n',
            b"x" * 65537 + b"\n",
        )
        for raw_request in malformed_requests:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(3)
                connection.connect(str(self.public_socket_path))
                connection.sendall(raw_request)
                response = json.loads(connection.makefile("rb").readline())
            self.assertFalse(response["ok"])

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(self.public_socket_path))
        self.assertTrue(
            self.proxy_call({"method": "profile.get", "token": login["token"]})["ok"]
        )

    @unittest.skipIf(os.geteuid() == 0, "use the root-only multi-UID integration test")
    def test_proxy_recovers_after_broker_unavailability(self) -> None:
        uid = os.geteuid()
        user = self.create_user("broker-recovery-user")
        self.bind_for_test(uid, int(user["user_id"]))
        login = self.proxy_call(
            {
                "method": "auth.login",
                "username": "broker-recovery-user",
                "password": "secure-pass-123",
            }
        )
        self.assertTrue(login["ok"], login)

        self.broker_server.shutdown()
        self.broker_server.server_close()
        self.broker_thread.join(timeout=5)
        self.assertFalse(self.broker_thread.is_alive())
        self.broker_socket_path.unlink(missing_ok=True)

        unavailable = self.proxy_call(
            {"method": "profile.get", "token": login["token"]}
        )
        self.assertFalse(unavailable["ok"])

        self.broker_server = BrokerServer(self.broker_socket_path, self.broker)
        os.chmod(self.broker_socket_path, 0o600)
        self.broker_thread = threading.Thread(target=self.broker_server.serve_forever)
        self.broker_thread.start()
        recovered = self.proxy_call(
            {"method": "profile.get", "token": login["token"]}
        )
        self.assertTrue(recovered["ok"], recovered)

    def test_proxy_socket_permissions_and_stale_socket_handling(self) -> None:
        self.assertEqual(0o660, self.public_socket_path.stat().st_mode & 0o777)
        self.assertEqual(0o600, self.broker_socket_path.stat().st_mode & 0o777)

        os.chmod(self.broker_socket_path, 0o660)
        with self.assertRaisesRegex(RuntimeError, "modo 0600"):
            ProxyService(self.broker_socket_path)
        os.chmod(self.broker_socket_path, 0o600)

        os.chmod(self.public_directory, 0o770)
        with self.assertRaisesRegex(RuntimeError, "modo 0710"):
            _prepare_socket_path(
                self.public_directory / "unsafe.sock", os.getegid()
            )
        os.chmod(self.public_directory, 0o710)

        stale_path = self.public_directory / "stale.sock"
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(stale_path))
        stale.close()
        _prepare_socket_path(stale_path, os.getegid())
        self.assertFalse(stale_path.exists())

        non_socket_path = self.public_directory / "not-a-socket"
        non_socket_path.write_text("protected", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "não é um socket"):
            _prepare_socket_path(non_socket_path, os.getegid())

    def test_proxy_service_entrypoint_applies_and_cleans_socket_permissions(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for shutdown_signal, socket_name in (
            (signal.SIGINT, "entrypoint-int.sock"),
            (signal.SIGTERM, "entrypoint-term.sock"),
        ):
            socket_path = self.public_directory / socket_name
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT_DIR / "broker" / "simple_proxy.py"),
                    "--socket",
                    str(socket_path),
                    "--broker-socket",
                    str(self.broker_socket_path),
                    "--socket-group",
                    str(os.getegid()),
                ],
                cwd=ROOT_DIR,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate(timeout=1)
                        self.fail(f"proxy saiu na inicialização: {stdout}{stderr}")
                    if socket_path.exists():
                        socket_stat = socket_path.stat()
                        if (
                            (socket_stat.st_mode & 0o777) == 0o660
                            and socket_stat.st_gid == os.getegid()
                        ):
                            try:
                                with socket.socket(
                                    socket.AF_UNIX, socket.SOCK_STREAM
                                ) as probe:
                                    probe.connect(str(socket_path))
                                break
                            except OSError:
                                pass
                    time.sleep(0.05)
                else:
                    self.fail("socket do proxy não ficou pronto")

                process.send_signal(shutdown_signal)
                _stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(0, process.returncode, stderr)
                self.assertFalse(socket_path.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    def test_deployment_preflight_waits_for_a_live_broker_socket(self) -> None:
        completed = subprocess.run(
            [
                "bash",
                str(ROOT_DIR / "deploy" / "wait-for-broker.sh"),
                str(self.broker_socket_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    @unittest.skipUnless(os.geteuid() == 0, "requer root para assumir UIDs distintos")
    def test_real_distinct_uids_share_proxy_without_cross_identity_access(self) -> None:
        import pwd

        try:
            passwd_uids = {entry.pw_uid for entry in pwd.getpwall()}
        except OSError as error:
            self.fail(f"não foi possível consultar os UIDs do sistema: {error}")
        available_uids = [
            uid
            for uid in range(1000, 65534)
            if uid != os.geteuid() and uid not in passwd_uids
        ]
        if len(available_uids) < 2:
            self.skipTest(
                "requer dois UIDs livres entre 1000 e 65533, não cadastrados em /etc/passwd"
            )
        first_uid, second_uid = available_uids[:2]
        self.assertNotEqual(first_uid, second_uid)
        shared_gid = 65534
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            first = connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("uid-one", "unused"),
            ).lastrowid
            second = connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("uid-two", "unused"),
            ).lastrowid
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id IN (?, ?)",
                (
                    subprocess.check_output(
                        ["openssl", "passwd", "-6", "-salt", "test-salt", "-stdin"],
                        input=b"secure-pass-123",
                    ).decode().strip(),
                    first,
                    second,
                ),
            )
            connection.execute(
                "INSERT INTO identity_bindings (linux_uid, user_id) VALUES (?, ?)",
                (first_uid, first),
            )
            connection.execute(
                "INSERT INTO identity_bindings (linux_uid, user_id) VALUES (?, ?)",
                (second_uid, second),
            )
        os.chown(self.public_directory, 0, shared_gid)
        self.proxy_server.shutdown()
        self.proxy_server.server_close()
        self.proxy_thread.join(timeout=5)
        self.public_socket_path.unlink(missing_ok=True)
        self.proxy_server = ProxyServer(self.public_socket_path, self.proxy_service)
        os.chown(self.public_socket_path, 0, shared_gid)
        os.chmod(self.public_socket_path, 0o660)
        self.proxy_thread = threading.Thread(target=self.proxy_server.serve_forever)
        self.proxy_thread.start()

        client_script = """
import json, socket, sys
sock, username, password = sys.argv[1:4]
def call(request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(sock)
        connection.sendall(json.dumps(request).encode() + b"\\n")
        return json.loads(connection.makefile("rb").readline())
login = call({"method":"auth.login","username":username,"password":password})
if not login.get("ok"):
    raise SystemExit(json.dumps(login))
profile = call({"method":"profile.get","token":login["token"]})
spoof = call({"method":"profile.get","token":login["token"],"uid":sys.argv[6]})
wrong_account = call({"method":"auth.login","username":"uid-one","password":password})
private_socket_denied = False
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(sys.argv[4])
except OSError:
    private_socket_denied = True
database_denied = False
try:
    with open(sys.argv[5], "rb"):
        pass
except OSError:
    database_denied = True
print(json.dumps({
    "login":login,
    "profile":profile,
    "spoof":spoof,
    "wrong_account":wrong_account,
    "private_socket_denied":private_socket_denied,
    "database_denied":database_denied
}))
"""

        def run_as_uid(
            uid: int, username: str, spoof_uid: int
        ) -> dict[str, object]:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    client_script,
                    str(self.public_socket_path),
                    username,
                    "secure-pass-123",
                    str(self.broker_socket_path),
                    str(self.database_path),
                    str(spoof_uid),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                user=uid,
                group=shared_gid,
                extra_groups=(),
            )
            self.assertEqual(
                0, completed.returncode, completed.stderr + completed.stdout
            )
            return json.loads(completed.stdout)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_result, second_result = executor.map(
                lambda args: run_as_uid(*args),
                (
                    (first_uid, "uid-one", second_uid),
                    (second_uid, "uid-two", first_uid),
                ),
            )
        self.assertEqual("uid-one", first_result["profile"]["username"])
        self.assertEqual("uid-two", second_result["profile"]["username"])
        self.assertNotEqual(
            first_result["login"]["token"], second_result["login"]["token"]
        )
        self.assertIn("Campos inválidos", first_result["spoof"]["error"])
        self.assertIn("Usuário ou senha", second_result["wrong_account"]["error"])
        self.assertTrue(first_result["private_socket_denied"])
        self.assertTrue(second_result["private_socket_denied"])
        self.assertTrue(first_result["database_denied"])
        self.assertTrue(second_result["database_denied"])

        cross_identity_script = """
import json, socket, sys
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(5)
    connection.connect(sys.argv[1])
    connection.sendall(json.dumps({"method":"profile.get","token":sys.argv[2]}).encode() + b"\\n")
    print(connection.makefile("rb").readline().decode(), end="")
"""

        def use_token(uid: int, token: str) -> dict[str, object]:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    cross_identity_script,
                    str(self.public_socket_path),
                    token,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                user=uid,
                group=shared_gid,
                extra_groups=(),
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            return json.loads(completed.stdout)

        self.assertIn(
            "Sessão inválida",
            use_token(second_uid, first_result["login"]["token"])["error"],
        )
        self.assertTrue(
            use_token(first_uid, first_result["login"]["token"])["ok"]
        )


if __name__ == "__main__":
    unittest.main()
