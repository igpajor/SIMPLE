from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def unit_settings(path: Path) -> dict[str, list[str]]:
    settings: dict[str, list[str]] = {}
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line
        elif "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            settings.setdefault(f"{section}:{key}", []).append(value)
    return settings


class DeploymentTests(unittest.TestCase):
    def test_systemd_units_use_expected_users_paths_and_ordering(self) -> None:
        broker = unit_settings(
            ROOT_DIR / "deploy" / "systemd" / "simple-broker.service"
        )
        proxy = unit_settings(
            ROOT_DIR / "deploy" / "systemd" / "simple-proxy.service"
        )

        self.assertEqual(["simple-broker"], broker["[Service]:User"])
        self.assertEqual(["simple-broker"], broker["[Service]:Group"])
        self.assertEqual(["simple-broker"], proxy["[Service]:User"])
        self.assertEqual(["simple-users"], proxy["[Service]:Group"])
        self.assertIn("simple-broker.service", proxy["[Unit]:Requires"])
        self.assertIn("simple-broker.service", proxy["[Unit]:After"])
        self.assertIn("simple-broker.service", proxy["[Unit]:BindsTo"])
        self.assertIn("simple-broker.service", proxy["[Unit]:PartOf"])
        self.assertEqual(
            ["/usr/bin/bash /opt/simple/deploy/wait-for-broker.sh"],
            proxy["[Service]:ExecStartPre"],
        )
        self.assertEqual(["on-failure"], broker["[Service]:Restart"])
        self.assertEqual(["on-failure"], proxy["[Service]:Restart"])
        self.assertEqual(["0700"], broker["[Service]:StateDirectoryMode"])
        self.assertEqual(["simple-private"], broker["[Service]:RuntimeDirectory"])
        self.assertEqual(["0700"], broker["[Service]:RuntimeDirectoryMode"])
        self.assertEqual(["simple-public"], proxy["[Service]:RuntimeDirectory"])
        self.assertEqual(["0710"], proxy["[Service]:RuntimeDirectoryMode"])
        self.assertEqual(["yes"], broker["[Service]:NoNewPrivileges"])
        self.assertEqual(["yes"], proxy["[Service]:NoNewPrivileges"])
        self.assertEqual(["strict"], broker["[Service]:ProtectSystem"])
        self.assertEqual(["strict"], proxy["[Service]:ProtectSystem"])
        self.assertEqual(["yes"], broker["[Service]:ProtectHome"])
        self.assertEqual(["yes"], proxy["[Service]:ProtectHome"])
        self.assertEqual(["AF_UNIX"], broker["[Service]:RestrictAddressFamilies"])
        self.assertEqual(["AF_UNIX"], proxy["[Service]:RestrictAddressFamilies"])
        self.assertNotIn("--initialize-database", broker["[Service]:ExecStart"][0])
        self.assertIn("/var/lib/simple/simple.db", broker["[Service]:ExecStart"][0])
        self.assertIn("/run/simple-private/broker.sock", proxy["[Service]:ExecStart"][0])
        self.assertIn("/run/simple-public/proxy.sock", proxy["[Service]:ExecStart"][0])

    def test_production_initializer_uses_only_broker_one_shot_operations(self) -> None:
        script = (
            ROOT_DIR / "deploy" / "init-production-database.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("EUID != 0", script)
        self.assertIn("simple-broker:700", script)
        self.assertIn("o banco já existe", script)
        self.assertIn("--initialize-database-only", script)
        self.assertIn("--validate-database-only", script)
        self.assertNotIn("sqlite3 ", script)
        self.assertNotIn("cp ", script)


if __name__ == "__main__":
    unittest.main()
