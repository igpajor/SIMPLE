from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT_DIR / "deploy" / "provision-participant.py"
SPEC = importlib.util.spec_from_file_location("provision_participant", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
provision_participant = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision_participant)


class ProvisionParticipantTests(unittest.TestCase):
    def test_accepts_safe_linux_and_simple_usernames(self) -> None:
        for username in ("andoid-test", "learner_01"):
            with self.subTest(username=username):
                provision_participant.validate_username(username)

    def test_rejects_unsafe_or_nonmatching_usernames(self) -> None:
        for username in ("Admin", "ab", "bad.name", "../guest", "guest;id"):
            with self.subTest(username=username):
                with self.assertRaises(provision_participant.ProvisionError):
                    provision_participant.validate_username(username)

    def test_validates_public_key_shape_and_type(self) -> None:
        key = "ssh-ed25519 YWJj termux-test"
        self.assertEqual(key, provision_participant.validate_public_key(key))
        with self.assertRaises(provision_participant.ProvisionError):
            provision_participant.validate_public_key("command=id ssh-ed25519 YWJj")
        with self.assertRaises(provision_participant.ProvisionError):
            provision_participant.validate_public_key("ssh-ed25519 !!!")

    def test_verifies_public_key_with_openssh_without_exposing_it_in_arguments(self) -> None:
        key = "ssh-ed25519 YWJj termux-test"
        with patch.object(provision_participant, "run_command") as run_command:
            provision_participant.verify_public_key(key)
        arguments = run_command.call_args.args[0]
        self.assertEqual(["/usr/bin/ssh-keygen", "-lf"], arguments[:2])
        self.assertEqual(3, len(arguments))

    def test_requires_effective_public_key_authentication(self) -> None:
        output = "\n".join(
            f"{setting} {value}"
            for setting, value in provision_participant.SSH_SETTINGS.items()
        )
        result = subprocess.CompletedProcess([], 0, output, "")
        with patch.object(provision_participant, "run_command", return_value=result):
            provision_participant.validate_ssh_configuration("simple-broker")

    def test_help_is_available_without_privileged_operations(self) -> None:
        result = subprocess.run(
            ["/usr/bin/python3", str(SCRIPT_PATH), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertIn("nome da conta SIMPLE e Linux", result.stdout)

    def test_provisions_locked_account_before_binding_and_enabling_access(self) -> None:
        events: list[tuple[str, object]] = []

        def record_command(
            arguments: list[str], failure: str
        ) -> subprocess.CompletedProcess[str]:
            events.append(("command", arguments))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        def record_broker(method: str, **fields: object) -> dict[str, object]:
            events.append((method, fields))
            return {"ok": True}

        with (
            patch.object(sys, "argv", [str(SCRIPT_PATH), "learner_01"]),
            patch.object(provision_participant.os, "geteuid", return_value=0),
            patch.object(provision_participant, "validate_ssh_configuration"),
            patch.object(provision_participant, "validate_services_and_groups"),
            patch.object(
                provision_participant.pwd, "getpwnam", side_effect=KeyError
            ),
            patch("builtins.input", return_value="ssh-ed25519 YWJj test"),
            patch.object(provision_participant, "verify_public_key"),
            patch.object(
                provision_participant, "authenticate_admin", return_value="token"
            ),
            patch.object(
                provision_participant, "find_participant", return_value=12
            ),
            patch.object(
                provision_participant, "write_authorized_key", return_value=1012
            ),
            patch.object(
                provision_participant, "run_command", side_effect=record_command
            ),
            patch.object(
                provision_participant, "broker_request", side_effect=record_broker
            ),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(0, provision_participant.main())

        useradd_index = next(
            index
            for index, event in enumerate(events)
            if event[0] == "command"
            and isinstance(event[1], list)
            and event[1][0] == "/usr/sbin/useradd"
        )
        bind_index = next(
            index
            for index, event in enumerate(events)
            if event[0] == "admin.identity.bind"
        )
        usermod_index = next(
            index
            for index, event in enumerate(events)
            if event[0] == "command"
            and isinstance(event[1], list)
            and event[1][0] == "/usr/sbin/usermod"
        )
        self.assertLess(useradd_index, bind_index)
        self.assertLess(bind_index, usermod_index)
        self.assertIn("--shell", events[useradd_index][1])
        self.assertIn("/usr/sbin/nologin", events[useradd_index][1])
        self.assertIn("simple-users", events[usermod_index][1])


if __name__ == "__main__":
    unittest.main()
