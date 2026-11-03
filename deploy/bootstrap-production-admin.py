#!/usr/bin/env python3
"""Create the first SIMPLE administrator in a fresh production database."""

from __future__ import annotations

import getpass
import os
import pwd
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
DATABASE = Path("/var/lib/simple/simple.db")
SERVICE_NAME = "simple-broker"
SERVICES = ("simple-broker.service", "simple-proxy.service")
sys.path.insert(0, str(APP_ROOT / "broker"))

import simple_broker


def fail(message: str) -> None:
    raise SystemExit(f"Erro: {message}")


def require_services_stopped() -> None:
    for service in SERVICES:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            fail(f"pare {service} antes de criar a conta administrativa.")
        if result.returncode != 3:
            fail(f"não foi possível confirmar que {service} está parado.")


def main() -> None:
    service_uid = pwd.getpwnam(SERVICE_NAME).pw_uid
    if os.geteuid() != service_uid:
        fail(f"execute como {SERVICE_NAME}, sem sudo/root.")

    require_services_stopped()
    simple_broker.validate_database(DATABASE)
    simple_broker.check_database_integrity(DATABASE)

    username = input("Usuário do administrador inicial: ").strip().lower()
    if not simple_broker.USERNAME_PATTERN.fullmatch(username):
        fail("nome de usuário inválido.")

    password = getpass.getpass("Senha do administrador (mínimo 8 caracteres): ")
    if len(password) < 8:
        fail("a senha deve conter pelo menos 8 caracteres.")
    confirmation = getpass.getpass("Confirme a senha: ")
    if password != confirmation:
        fail("as senhas não coincidem.")
    password_hash = simple_broker._password_hash(password)
    del password, confirmation

    with closing(sqlite3.connect(DATABASE)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count:
            fail("o banco já contém usuários; nenhum administrador foi criado.")
        connection.execute(
            """
            INSERT INTO users (username, password_hash, is_admin)
            VALUES (?, ?, 1)
            """,
            (username, password_hash),
        )

    simple_broker.validate_database(DATABASE)
    simple_broker.check_database_integrity(DATABASE)
    print(f"Administrador inicial '{username}' criado e banco validado.")


if __name__ == "__main__":
    main()
