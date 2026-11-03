#!/usr/bin/env python3
"""Provision one Linux SSH identity for an existing SIMPLE participant."""

from __future__ import annotations

import argparse
import base64
import getpass
import grp
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


BROKER_SOCKET = Path("/run/simple-private/broker.sock")
USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
SSH_KEY_TYPES = {
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
}
MAX_RESPONSE_BYTES = 131072
MAX_PUBLIC_KEY_LENGTH = 16384
SSH_SETTINGS = {
    "authenticationmethods": "publickey",
    "passwordauthentication": "no",
    "kbdinteractiveauthentication": "no",
    "pubkeyauthentication": "yes",
    "forcecommand": "/opt/simple/deploy/simple-ssh-entrypoint.sh",
    "permittty": "yes",
    "disableforwarding": "yes",
    "allowtcpforwarding": "no",
    "allowagentforwarding": "no",
    "allowstreamlocalforwarding": "no",
    "x11forwarding": "no",
    "permittunnel": "no",
    "permituserrc": "no",
    "permituserenvironment": "no",
}


class ProvisionError(Exception):
    pass


def validate_username(username: str) -> None:
    if not USERNAME_PATTERN.fullmatch(username):
        raise ProvisionError(
            "Use um nome igual ao usuário SIMPLE, em minúsculas, com 3 a 32 "
            "caracteres: letras, números, hífen ou sublinhado."
        )


def validate_public_key(public_key: str) -> str:
    if len(public_key) > MAX_PUBLIC_KEY_LENGTH:
        raise ProvisionError("A chave pública excede o tamanho permitido.")
    fields = public_key.strip().split()
    if len(fields) not in (2, 3) or fields[0] not in SSH_KEY_TYPES:
        raise ProvisionError("Chave pública OpenSSH inválida ou tipo não permitido.")
    try:
        decoded = base64.b64decode(fields[1], validate=True)
    except ValueError as error:
        raise ProvisionError("Conteúdo Base64 inválido na chave pública.") from error
    if not decoded:
        raise ProvisionError("Chave pública vazia.")
    return " ".join(fields)


def run_command(arguments: list[str], failure: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProvisionError(f"{failure}: {error}") from error
    if result.returncode:
        details = result.stderr.strip()
        raise ProvisionError(f"{failure}{': ' + details if details else '.'}")
    return result


def verify_public_key(public_key: str) -> None:
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as key_file:
            key_file.write(public_key + "\n")
            key_file.flush()
            run_command(
                ["/usr/bin/ssh-keygen", "-lf", key_file.name],
                "ssh-keygen não reconheceu a chave pública",
            )
    except OSError as error:
        raise ProvisionError(f"Não foi possível validar a chave pública: {error}") from error


def broker_request(method: str, **fields: Any) -> dict[str, Any]:
    request = {"method": method, **fields}
    payload = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(str(BROKER_SOCKET))
            connection.sendall(payload)
            response = bytearray()
            while not response.endswith(b"\n"):
                chunk = connection.recv(4096)
                if not chunk:
                    raise ProvisionError("O Broker encerrou a conexão sem resposta.")
                response.extend(chunk)
                if len(response) > MAX_RESPONSE_BYTES:
                    raise ProvisionError("Resposta do Broker excede o limite permitido.")
    except OSError as error:
        raise ProvisionError(f"Não foi possível acessar o Broker: {error}") from error

    try:
        decoded = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvisionError("O Broker retornou uma resposta inválida.") from error
    if not isinstance(decoded, dict):
        raise ProvisionError("O Broker retornou uma resposta inválida.")
    return decoded


def require_ok(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not True:
        message = response.get("error")
        raise ProvisionError(
            message if isinstance(message, str) else "Operação recusada pelo Broker."
        )
    return response


def validate_ssh_configuration(username: str) -> None:
    result = run_command(
        [
            "/usr/sbin/sshd",
            "-T",
            "-C",
            f"user={username},host=localhost,addr=127.0.0.1",
        ],
        "Não foi possível verificar a configuração efetiva do SSH",
    )
    effective = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(" ")
        if separator:
            effective[key.lower()] = value.strip()
    for key, expected in SSH_SETTINGS.items():
        if effective.get(key) != expected:
            raise ProvisionError(
                f"Configuração SSH insegura para participantes: "
                f"{key} deveria ser '{expected}'. Nenhuma conta foi habilitada."
            )


def validate_services_and_groups() -> None:
    for service in ("simple-broker.service", "simple-proxy.service"):
        run_command(
            ["/usr/bin/systemctl", "is-active", "--quiet", service],
            f"O serviço {service} precisa estar ativo",
        )
    try:
        grp.getgrnam("simple-users")
    except KeyError as error:
        raise ProvisionError("O grupo simple-users não existe.") from error
    try:
        service_groups = grp.getgrall()
    except OSError as error:
        raise ProvisionError(f"Não foi possível verificar os grupos: {error}") from error
    if not any(
        group.gr_name == "simple-users" and "simple-broker" in group.gr_mem
        for group in service_groups
    ):
        raise ProvisionError("simple-broker precisa pertencer ao grupo simple-users.")


def authenticate_admin() -> str:
    admin_username = input("Usuário administrador SIMPLE: ").strip()
    password = getpass.getpass("Senha do administrador SIMPLE: ")
    try:
        response = broker_request(
            "auth.login", username=admin_username, password=password
        )
    finally:
        del password
    response = require_ok(response)
    if response.get("is_admin") is not True:
        token = response.get("token")
        if isinstance(token, str):
            broker_request("auth.logout", token=token)
        raise ProvisionError("A conta SIMPLE informada não é administradora.")
    token = response.get("token")
    if not isinstance(token, str) or not token:
        raise ProvisionError("O Broker não retornou uma sessão administrativa válida.")
    return token


def find_participant(token: str, username: str) -> int:
    response = require_ok(broker_request("admin.users.list", token=token))
    users = response.get("users")
    if not isinstance(users, list):
        raise ProvisionError("Lista de contas inesperada retornada pelo Broker.")
    matches = [
        user
        for user in users
        if isinstance(user, dict) and user.get("username") == username
    ]
    if len(matches) != 1:
        raise ProvisionError(
            f"A conta SIMPLE '{username}' não foi encontrada de forma única."
        )
    user = matches[0]
    if user.get("is_admin") or not user.get("is_active"):
        raise ProvisionError("A conta precisa ser participante ativa, não administradora.")
    user_id = user.get("id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise ProvisionError("ID inválido retornado para a conta SIMPLE.")

    identities_response = require_ok(
        broker_request("admin.identity.list", token=token)
    )
    identities = identities_response.get("identities")
    if not isinstance(identities, list):
        raise ProvisionError("Lista de vínculos inesperada retornada pelo Broker.")
    if any(
        isinstance(identity, dict)
        and identity.get("is_active")
        and identity.get("user_id") == user_id
        for identity in identities
    ):
        raise ProvisionError(f"A conta SIMPLE '{username}' já possui vínculo UID ativo.")
    return user_id


def write_authorized_key(username: str, public_key: str) -> int:
    try:
        account = pwd.getpwnam(username)
    except KeyError as error:
        raise ProvisionError(f"A conta Linux '{username}' não foi criada.") from error

    home = Path(account.pw_dir)
    try:
        if home != Path("/home") / username:
            raise ProvisionError("Diretório home inesperado após criar usuário Linux.")
        home_stat = home.lstat()
        if not stat.S_ISDIR(home_stat.st_mode) or home_stat.st_uid != account.pw_uid:
            raise ProvisionError("Diretório home inválido após criar usuário Linux.")
        os.chmod(home, 0o700)

        ssh_directory = home / ".ssh"
        ssh_directory.mkdir(mode=0o700)
        os.chown(ssh_directory, account.pw_uid, account.pw_gid)
        authorized_keys = ssh_directory / "authorized_keys"
        descriptor = os.open(
            authorized_keys,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as key_file:
            key_file.write(public_key + "\n")
            key_file.flush()
            os.fsync(key_file.fileno())
        os.chown(authorized_keys, account.pw_uid, account.pw_gid)
        os.chmod(authorized_keys, 0o600)
    except OSError as error:
        raise ProvisionError(
            f"Não foi possível instalar a chave em /home/{username}/.ssh: {error}. "
            "A conta Linux continua bloqueada e sem grupo de acesso."
        ) from error
    return account.pw_uid


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cria uma conta SSH restrita e vincula ao usuário SIMPLE existente."
        )
    )
    parser.add_argument("username", help="nome da conta SIMPLE e Linux (minúsculas)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise ProvisionError("Execute com sudo/root.")
    validate_username(args.username)
    validate_ssh_configuration("simple-broker")
    validate_services_and_groups()
    try:
        pwd.getpwnam(args.username)
    except KeyError:
        pass
    else:
        raise ProvisionError(f"A conta Linux '{args.username}' já existe.")

    public_key = validate_public_key(input("Cole a chave pública SSH: "))
    verify_public_key(public_key)
    token = authenticate_admin()
    user_id = find_participant(token, args.username)

    run_command(
        [
            "/usr/sbin/useradd",
            "--create-home",
            "--user-group",
            "--shell",
            "/usr/sbin/nologin",
            args.username,
        ],
        f"Não foi possível criar a conta Linux '{args.username}'",
    )
    run_command(
        ["/usr/bin/passwd", "--lock", args.username],
        f"Não foi possível bloquear senha Linux de '{args.username}'",
    )
    linux_uid = write_authorized_key(args.username, public_key)

    try:
        require_ok(
            broker_request(
                "admin.identity.bind",
                token=token,
                linux_uid=linux_uid,
                user_id=user_id,
            )
        )
    except ProvisionError as error:
        raise ProvisionError(
            f"{error} A conta Linux foi mantida em /usr/sbin/nologin, sem grupo de acesso; "
            "verifique antes de removê-la ou tentar novamente."
        ) from error

    try:
        run_command(
            [
                "/usr/sbin/usermod",
                "--shell",
                "/bin/bash",
                "--append",
                "--groups",
                "simple-users",
                args.username,
            ],
            f"Vínculo criado, mas não foi possível habilitar '{args.username}'",
        )
        validate_ssh_configuration(args.username)
    except ProvisionError as error:
        run_command(
            [
                "/usr/sbin/usermod",
                "--shell",
                "/usr/sbin/nologin",
                "--groups",
                "",
                args.username,
            ],
            f"{error} Não foi possível bloquear novamente '{args.username}'",
        )
        try:
            require_ok(
                broker_request(
                    "admin.identity.revoke", token=token, linux_uid=linux_uid
                )
            )
        except ProvisionError as revoke_error:
            raise ProvisionError(
                f"{error} A conta Linux foi bloqueada, mas não foi possível revogar "
                f"o vínculo: {revoke_error}"
            ) from error
        raise ProvisionError(
            f"{error} O vínculo foi revogado e a conta voltou ao estado bloqueado."
        ) from error

    require_ok(broker_request("auth.logout", token=token))
    print(
        f"Participante '{args.username}' provisionado. UID Linux: {linux_uid}; "
        f"ID SIMPLE: {user_id}. A chave está instalada e o acesso SSH restrito ativo."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisionError as error:
        print(f"Erro: {error}", file=sys.stderr)
        raise SystemExit(1) from error
