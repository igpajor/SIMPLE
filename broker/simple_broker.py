#!/usr/bin/env python3
"""Restricted local broker for the first SIMPLE remote-session vertical slice."""

from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import re
import secrets
import signal
import socket
import socketserver
import sqlite3
import stat
import struct
import subprocess
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol


MAX_REQUEST_BYTES = 65536
BROKER_MAX_CONNECTIONS = 64
PROXY_INTERNAL_PROTOCOL = "simple-proxy-v1"
SESSION_TTL_SECONDS = 8 * 60 * 60
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
GITHUB_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,39}$")
EXERCISE_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,63}$")
COMMIT_URL_PATTERN = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/commit/[a-fA-F0-9]{7,64}$"
)
MIGRATIONS = (
    ("exercises", "statement_path", "TEXT"),
    ("exercises", "statement", "TEXT NOT NULL DEFAULT ''"),
    ("exercises", "example", "TEXT NOT NULL DEFAULT ''"),
    ("exercises", "terminal_model", "TEXT NOT NULL DEFAULT ''"),
    ("exercises", "hints", "TEXT NOT NULL DEFAULT ''"),
    ("users", "is_admin", "INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1))"),
    ("users", "must_change_password", "INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1))"),
    ("users", "display_name", "TEXT NOT NULL DEFAULT ''"),
    ("users", "github_username", "TEXT NOT NULL DEFAULT ''"),
    ("users", "bio", "TEXT NOT NULL DEFAULT ''"),
    ("users", "is_active", "INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))"),
    ("evaluation_slots", "accepted_by", "INTEGER REFERENCES users(id)"),
    ("evaluation_slots", "accepted_at", "DATETIME"),
    ("reviews", "recipient_score", "INTEGER CHECK (recipient_score IS NULL OR recipient_score BETWEEN 0 AND 5)"),
    ("reviews", "recipient_feedback", "TEXT NOT NULL DEFAULT ''"),
    ("reviews", "recipient_responded_at", "DATETIME"),
    ("submissions", "content_kind", "TEXT NOT NULL DEFAULT 'legacy' CHECK (content_kind IN ('legacy', 'github_commit'))"),
    ("submissions", "commit_url", "TEXT"),
    ("submissions", "delivery_directory", "TEXT"),
)
METHOD_FIELDS = {
    "auth.register": ("username", "password"),
    "auth.login": ("username", "password"),
    "auth.change_password": ("token", "password"),
    "auth.logout": ("token",),
    "profile.get": ("token",),
    "profile.update": ("token", "display_name", "github_username", "bio"),
    "catalog.list": ("token",),
    "catalog.get": ("token", "exercise_code"),
    "submissions.requirements": ("token", "exercise_code"),
    "submissions.create": ("token", "exercise_code", "commit_url", "delivery_directory"),
    "submissions.list": ("token",),
    "submissions.get": ("token", "submission_id"),
    "evaluations.slots.list": ("token",),
    "evaluations.requests.list": ("token",),
    "evaluations.request.respond": ("token", "request_id", "decision"),
    "evaluations.slot.get": ("token", "slot_id"),
    "evaluations.review.get": ("token", "review_id"),
    "evaluations.accept": ("token", "slot_id"),
    "evaluations.cancel": ("token", "slot_id"),
    "evaluations.review.submit": ("token", "slot_id", "score", "feedback"),
    "evaluations.review.respond": ("token", "review_id", "score", "feedback"),
    "evaluations.history": ("token",),
    "notifications.count": ("token",),
    "notifications.mark_read": ("token",),
    "points.balance": ("token",),
    "points.history": ("token",),
    "progress.get": ("token",),
    "admin.users.list": ("token",),
    "admin.user.get": ("token", "user_id"),
    "admin.user.state": ("token", "user_id", "is_active"),
    "admin.password.reset": ("token", "user_id", "password"),
    "admin.points.adjust": ("token", "user_id", "amount", "reason"),
    "admin.exercises.list": ("token",),
    "admin.exercise.create": (
        "token",
        "code",
        "name",
        "description",
        "statement",
        "example",
        "delivery_directory",
        "delivery_files",
        "allowed_functions",
        "terminal_model",
        "hints",
    ),
    "admin.exercise.update": ("token", "exercise_code", "field", "value"),
    "admin.exercise.archive": ("token", "exercise_code"),
    "admin.exercise.reactivate": ("token", "exercise_code"),
    "admin.submissions.list": ("token",),
    "admin.evaluations.list": ("token",),
    "admin.points.history": ("token", "user_id"),
    "admin.report": ("token",),
    "admin.identity.list": ("token",),
    "admin.identity.get": ("token", "linux_uid"),
    "admin.identity.bind": ("token", "linux_uid", "user_id"),
    "admin.identity.update": ("token", "linux_uid", "new_linux_uid"),
    "admin.identity.revoke": ("token", "linux_uid"),
}


SQLITE_AUXILIARY_SUFFIXES = ("-wal", "-shm", "-journal")


def _validate_private_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise RuntimeError("O diretório do banco deve ser um caminho absoluto.")
    normalized = Path(os.path.abspath(path))
    components = [Path("/")]
    current = Path("/")
    for part in normalized.parts[1:]:
        current = current / part
        components.append(current)

    for directory in components:
        try:
            directory_stat = directory.lstat()
        except FileNotFoundError as error:
            raise RuntimeError(
                "O diretório do banco deve existir previamente."
            ) from error
        except OSError as error:
            raise RuntimeError("Não foi possível validar o caminho do banco.") from error
        if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
            raise RuntimeError("O caminho do banco contém um link ou componente não diretório.")
        if directory != normalized and directory_stat.st_mode & 0o022:
            sticky_root_directory = (
                directory_stat.st_uid == 0
                and directory_stat.st_mode & stat.S_ISVTX
            )
            if not sticky_root_directory:
                raise RuntimeError(
                    "Um diretório ancestral do banco permite escrita não confiável."
                )

    parent_stat = normalized.lstat()
    if parent_stat.st_uid != os.geteuid():
        raise RuntimeError("O diretório do banco deve pertencer ao processo do Broker.")
    if parent_stat.st_mode & 0o077:
        raise RuntimeError(
            "O diretório do banco deve ser privado (sem permissões para grupo/outros)."
        )
    return normalized


def _validate_regular_owned_file(path: Path, label: str) -> os.stat_result:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as error:
        raise RuntimeError(f"Não foi possível validar {label}.") from error
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"{label} deve ser um arquivo regular, não um link.")
    if file_stat.st_uid != os.geteuid():
        raise RuntimeError(f"{label} deve pertencer ao processo do Broker.")
    if file_stat.st_mode & 0o077:
        raise RuntimeError(f"{label} deve ter permissões privadas (0600).")
    return file_stat


def _validate_database_files(database_path: Path, require_main: bool = True) -> Path:
    if not database_path.is_absolute():
        raise RuntimeError("O caminho do banco deve ser absoluto.")
    path = _validate_private_directory(database_path.parent) / database_path.name
    if path.name in {"", ".", ".."}:
        raise RuntimeError("Nome de banco SQLite inválido.")
    try:
        _validate_regular_owned_file(path, "O banco SQLite")
    except FileNotFoundError as error:
        if require_main:
            raise RuntimeError(
                "Banco SQLite inexistente; inicialize-o explicitamente antes do broker."
            ) from error
    for suffix in SQLITE_AUXILIARY_SUFFIXES:
        auxiliary = Path(f"{path}{suffix}")
        try:
            _validate_regular_owned_file(auxiliary, f"O arquivo auxiliar {suffix}")
        except FileNotFoundError:
            continue
    return path


def _transactional_schema_statements(schema_sql: str) -> list[str]:
    statements: list[str] = []
    pending: list[str] = []
    for character in schema_sql:
        pending.append(character)
        if character != ";":
            continue
        candidate = "".join(pending)
        if not sqlite3.complete_statement(candidate):
            continue
        statement = candidate.strip()
        pending.clear()
        normalized = re.sub(
            r"/\*.*?\*/|--[^\r\n]*", " ", statement, flags=re.DOTALL
        ).strip()
        if not normalized:
            continue
        if re.match(
            r"^(?:BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE|VACUUM|ATTACH|DETACH)\b",
            normalized,
            flags=re.IGNORECASE,
        ):
            raise RuntimeError(
                "schema.sql contém comando incompatível com a transação explícita."
            )
        if re.match(r"^PRAGMA\b", normalized, flags=re.IGNORECASE):
            if not re.fullmatch(
                r"PRAGMA\s+foreign_keys\s*=\s*(?:ON|1)\s*;?",
                normalized,
                flags=re.IGNORECASE,
            ):
                raise RuntimeError(
                    "schema.sql contém PRAGMA incompatível com a transação explícita."
                )
            continue
        statements.append(statement)

    remainder = "".join(pending)
    if re.sub(r"/\*.*?\*/|--[^\r\n]*", "", remainder, flags=re.DOTALL).strip():
        raise RuntimeError("schema.sql contém uma instrução SQL incompleta.")
    return statements


def _validate_database_connection(
    connection: sqlite3.Connection, *, allow_pre_migration_schema: bool = False
) -> None:
    required_columns = {
        "users": {
            "id", "username", "password_hash", "is_admin", "must_change_password",
            "display_name", "github_username", "bio", "is_active", "created_at",
        },
        "exercises": {
            "id", "code", "name", "description", "status", "statement_path",
            "statement", "example", "delivery_directory", "delivery_files",
            "allowed_functions", "terminal_model", "hints", "created_at",
        },
        "submissions": {
            "id", "user_id", "exercise_id", "content", "status", "content_kind",
            "commit_url", "delivery_directory", "submitted_at",
        },
        "evaluation_slots": {
            "id", "submission_id", "status", "reviewer_id", "accepted_by",
            "accepted_at", "created_at", "completed_at",
        },
        "evaluation_requests": {
            "id", "slot_id", "reviewer_id", "status", "requested_at", "responded_at",
        },
        "reviews": {
            "id", "slot_id", "reviewer_id", "score", "feedback", "recipient_score",
            "recipient_feedback", "recipient_responded_at", "created_at",
        },
        "notifications": {
            "id", "user_id", "type", "review_id", "read_at", "created_at",
        },
        "points_ledger": {
            "id", "user_id", "amount", "entry_type", "submission_id", "review_id",
            "details", "created_at",
        },
        "admin_point_adjustments": {
            "id", "user_id", "admin_id", "amount", "reason", "created_at",
        },
        "identity_bindings": {
            "id", "linux_uid", "user_id", "is_active", "created_at", "updated_at",
        },
    }
    if allow_pre_migration_schema:
        for table, column, _definition in MIGRATIONS:
            required_columns.get(table, set()).discard(column)
        required_columns.pop("notifications", None)
        required_columns.pop("evaluation_requests", None)

    for table, expected_columns in required_columns.items():
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not expected_columns.issubset(columns):
            raise RuntimeError(
                f"Schema do broker incompatível: tabela {table} requer migração explícita."
            )
    violations = connection.execute("PRAGMA foreign_key_check").fetchmany(1)
    if violations:
        raise RuntimeError("Banco SQLite contém violações de integridade referencial.")


def _check_database_integrity_connection(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise RuntimeError("Banco SQLite falhou na verificação de integridade.")
    if connection.execute("PRAGMA foreign_key_check").fetchmany(1):
        raise RuntimeError("Banco SQLite contém violações de integridade referencial.")


def initialize_database(
    database_path: Path, schema_path: Path, *, allow_existing: bool = True
) -> Path | None:
    if not database_path.is_absolute():
        raise RuntimeError("O caminho do banco deve ser absoluto.")
    database_path = _validate_private_directory(database_path.parent) / database_path.name
    schema_sql = schema_path.read_text(encoding="utf-8")
    schema_statements = _transactional_schema_statements(schema_sql)
    catalog_path = schema_path.with_name("exercises.sql")
    default_schema_path = (
        Path(__file__).resolve().parents[1] / "database" / "schema.sql"
    )
    if (
        schema_path.resolve() == default_schema_path.resolve()
        and not catalog_path.is_file()
    ):
        raise RuntimeError(f"Catálogo de exercícios não encontrado: {catalog_path}")
    catalog_statements = (
        _transactional_schema_statements(catalog_path.read_text(encoding="utf-8"))
        if catalog_path.is_file()
        else []
    )
    try:
        database_path.lstat()
    except FileNotFoundError:
        created = True
    else:
        if not allow_existing:
            raise RuntimeError("O banco já existe; a inicialização foi cancelada.")
        created = False
        _validate_database_files(database_path)
    backup_path = None
    if not created:
        for _ in range(10):
            candidate = database_path.with_name(
                f"{database_path.name}.backup-{time.time_ns()}-{secrets.token_hex(4)}.sqlite"
            )
            backup_identity: tuple[int, int] | None = None
            try:
                descriptor = os.open(
                    candidate,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except FileExistsError:
                continue
            backup_path = candidate
            try:
                try:
                    backup_stat = os.fstat(descriptor)
                    backup_identity = (backup_stat.st_dev, backup_stat.st_ino)
                    if not stat.S_ISREG(backup_stat.st_mode) or backup_stat.st_uid != os.geteuid():
                        raise RuntimeError("Não foi possível criar o backup SQLite com segurança.")
                    os.fchmod(descriptor, 0o600)
                finally:
                    os.close(descriptor)
            except BaseException:
                _remove_incomplete_backup(candidate, backup_identity)
                raise
            break
        if backup_path is None:
            raise RuntimeError("Não foi possível reservar um nome seguro para o backup.")
        try:
            _validate_private_directory(backup_path.parent)
            with closing(sqlite3.connect(database_path)) as source, closing(
                sqlite3.connect(backup_path)
            ) as backup:
                source.backup(backup)
            _validate_database_files(database_path)
            _validate_regular_owned_file(backup_path, "O backup SQLite")
            validate_database(backup_path, allow_pre_migration_schema=True)
            check_database_integrity(backup_path)
        except BaseException:
            _remove_incomplete_backup(backup_path, backup_identity)
            raise
    if created:
        try:
            descriptor = os.open(
                database_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError as error:
            raise RuntimeError("O caminho do banco foi ocupado durante a criação.") from error
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        _validate_regular_owned_file(database_path, "O banco SQLite")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        for statement in schema_statements:
            connection.execute(statement)
        for table, column, definition in MIGRATIONS:
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if column not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        connection.execute(
            """
            INSERT INTO evaluation_requests (
                slot_id, reviewer_id, status, requested_at, responded_at
            )
            SELECT slot.id, slot.accepted_by,
                   CASE WHEN slot.status = 'completed' THEN 'completed' ELSE 'accepted' END,
                   COALESCE(slot.accepted_at, CURRENT_TIMESTAMP),
                   COALESCE(slot.completed_at, slot.accepted_at, CURRENT_TIMESTAMP)
            FROM evaluation_slots slot
            WHERE slot.accepted_by IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM evaluation_requests request
                  WHERE request.slot_id = slot.id
              )
            """
        )
        for statement in catalog_statements:
            connection.execute(statement)
        _validate_database_connection(connection)
        _check_database_integrity_connection(connection)
        _validate_database_files(database_path)
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
    return backup_path


def _remove_incomplete_backup(
    backup_path: Path, backup_identity: tuple[int, int] | None
) -> None:
    if backup_identity is None:
        return
    try:
        backup_stat = backup_path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISREG(backup_stat.st_mode)
        and backup_stat.st_uid == os.geteuid()
        and (backup_stat.st_dev, backup_stat.st_ino) == backup_identity
    ):
        backup_path.unlink()


def check_database_integrity(database_path: Path) -> None:
    database_path = _validate_database_files(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        _check_database_integrity_connection(connection)
    _validate_database_files(database_path)


def validate_database(
    database_path: Path, *, allow_pre_migration_schema: bool = False
) -> None:
    database_path = _validate_database_files(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        _validate_database_connection(
            connection, allow_pre_migration_schema=allow_pre_migration_schema
        )
    _validate_database_files(database_path)


def backup_database(database_path: Path, backup_path: Path) -> Path:
    if not backup_path.is_absolute():
        raise RuntimeError("O caminho do backup deve ser absoluto.")
    source = _validate_database_files(database_path)
    destination = _validate_private_directory(backup_path.parent) / backup_path.name
    if destination == source or destination.name in {"", ".", ".."}:
        raise RuntimeError("O caminho de destino do backup é inválido.")
    validate_database(source)
    check_database_integrity(source)
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise RuntimeError("O arquivo de backup já existe; nenhum arquivo foi alterado.")
    try:
        descriptor = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError as error:
        raise RuntimeError("O arquivo de backup já existe; nenhum arquivo foi alterado.") from error
    backup_identity: tuple[int, int] | None = None
    try:
        backup_stat = os.fstat(descriptor)
        backup_identity = (backup_stat.st_dev, backup_stat.st_ino)
        if not stat.S_ISREG(backup_stat.st_mode) or backup_stat.st_uid != os.geteuid():
            raise RuntimeError("Não foi possível criar o backup SQLite com segurança.")
        os.fchmod(descriptor, 0o600)
        with closing(sqlite3.connect(source)) as database, closing(
            sqlite3.connect(destination)
        ) as backup:
            database.backup(backup)
        validate_database(destination)
        check_database_integrity(destination)
        return destination
    except (OSError, sqlite3.Error, RuntimeError):
        try:
            current_stat = destination.lstat()
            if (
                stat.S_ISREG(current_stat.st_mode)
                and current_stat.st_uid == os.geteuid()
                and backup_identity is not None
                and (current_stat.st_dev, current_stat.st_ino) == backup_identity
            ):
                destination.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)


class _ServerWithReadiness(Protocol):
    serve_ready: threading.Event

    def serve_forever(self) -> None: ...

    def shutdown(self) -> None: ...

    def server_close(self) -> None: ...


def _serve_until_stopped(
    server: _ServerWithReadiness, socket_path: Path, service_name: str
) -> None:
    socket_stat = socket_path.lstat()
    if not stat.S_ISSOCK(socket_stat.st_mode) or socket_stat.st_uid != os.geteuid():
        raise RuntimeError("O socket criado não pertence ao serviço.")
    socket_identity = (socket_stat.st_dev, socket_stat.st_ino)
    previous_handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }

    stop_requested = threading.Event()
    received_signal: list[int] = []

    def stop_service(signum: int, _frame: Any) -> None:
        received_signal.append(signum)
        stop_requested.set()

    try:
        for signum in previous_handlers:
            signal.signal(signum, stop_service)
        server_thread = threading.Thread(
            target=server.serve_forever, name=f"{service_name}-server"
        )
        server_thread.start()
        shutdown_started = False
        while server_thread.is_alive():
            if (
                stop_requested.is_set()
                and server.serve_ready.is_set()
                and not shutdown_started
            ):
                signum = received_signal[-1]
                logging.info(
                    "%s recebeu sinal %s",
                    service_name,
                    signal.Signals(signum).name,
                )
                server.shutdown()
                shutdown_started = True
            server_thread.join(timeout=0.1)
        server_thread.join()
    finally:
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)
        server.server_close()
        try:
            current_stat = socket_path.lstat()
            if (
                stat.S_ISSOCK(current_stat.st_mode)
                and current_stat.st_uid == os.geteuid()
                and (current_stat.st_dev, current_stat.st_ino) == socket_identity
            ):
                socket_path.unlink()
        except FileNotFoundError:
            pass


def _password_hash(password: str) -> str:
    try:
        result = subprocess.run(
            ["openssl", "passwd", "-6", "-stdin"],
            input=password.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Não foi possível gerar o hash da senha.") from error
    if result.returncode != 0:
        raise RuntimeError("Não foi possível gerar o hash da senha.")
    return result.stdout.decode("ascii").strip()


def _verify_password(password: str, stored_hash: str) -> bool:
    parts = stored_hash.split("$")
    if len(parts) < 4 or parts[1] != "6" or not parts[2]:
        return False
    try:
        result = subprocess.run(
            ["openssl", "passwd", "-6", "-salt", parts[2], "-stdin"],
            input=password.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Não foi possível verificar a senha.") from error
    if result.returncode != 0:
        raise RuntimeError("Não foi possível verificar a senha.")
    return secrets.compare_digest(result.stdout.decode("ascii").strip(), stored_hash)


class Broker:
    def __init__(self, database_path: Path, exercise_storage_dir: Path | None = None):
        if not database_path.is_absolute():
            raise ValueError("O caminho do banco deve ser absoluto.")
        self.database_path = database_path
        self.exercise_storage_dir = (
            exercise_storage_dir
            if exercise_storage_dir is not None
            else Path(__file__).resolve().parents[1] / "storage" / "exercises"
        )
        self.sessions: dict[str, tuple[int, float]] = {}
        self.sessions_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        _validate_database_files(self.database_path)
        connection = sqlite3.connect(self.database_path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            _validate_database_files(self.database_path)
            return connection
        except BaseException:
            connection.close()
            raise

    def _create_session_locked(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = (user_id, time.monotonic() + SESSION_TTL_SECONDS)
        return token

    def _create_session(self, user_id: int) -> str:
        now = time.monotonic()
        with self.sessions_lock:
            self.sessions = {
                session_token: session
                for session_token, session in self.sessions.items()
                if session[1] > now
            }
            return self._create_session_locked(user_id)

    def _session_user_id(self, connection: sqlite3.Connection, token: Any) -> int:
        if not isinstance(token, str) or not token:
            raise ValueError("Sessão inválida ou expirada.")
        now = time.monotonic()
        with self.sessions_lock:
            session = self.sessions.get(token)
            if session is None or session[1] <= now:
                self.sessions.pop(token, None)
                raise ValueError("Sessão inválida ou expirada.")
            user_id = session[0]
        user = connection.execute(
            "SELECT is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None or not user["is_active"]:
            with self.sessions_lock:
                self.sessions.pop(token, None)
            raise ValueError("Conta desativada ou sessão inválida.")
        return user_id

    def _require_admin(self, connection: sqlite3.Connection, token: Any) -> int:
        user_id = self._session_user_id(connection, token)
        row = connection.execute(
            "SELECT is_admin FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None or not row["is_admin"]:
            raise PermissionError("Acesso administrativo não autorizado.")
        return user_id

    @staticmethod
    def _resolve_linux_uid(connection: sqlite3.Connection, linux_uid: Any) -> int:
        linux_uid = Broker._integer(linux_uid, "UID Linux", minimum=0)
        row = connection.execute(
            """
            SELECT users.id, users.is_active
            FROM identity_bindings
            JOIN users ON users.id = identity_bindings.user_id
            WHERE identity_bindings.linux_uid = ?
              AND identity_bindings.is_active = 1
            """,
            (linux_uid,),
        ).fetchone()
        if row is None:
            raise PermissionError("UID Linux sem vínculo ativo com uma conta SIMPLE.")
        if not row["is_active"]:
            raise PermissionError("A conta SIMPLE vinculada está desativada.")
        return row["id"]

    def resolve_proxy_identity(self, linux_uid: Any) -> dict[str, Any]:
        linux_uid = self._integer(linux_uid, "UID Linux", minimum=1)
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT identity_bindings.id AS binding_id, users.id AS user_id,
                       users.username, users.is_active
                FROM identity_bindings
                JOIN users ON users.id = identity_bindings.user_id
                WHERE identity_bindings.linux_uid = ?
                  AND identity_bindings.is_active = 1
                """,
                (linux_uid,),
            ).fetchone()
            if row is None:
                raise PermissionError("UID Linux sem vínculo ativo com uma conta SIMPLE.")
            if not row["is_active"]:
                raise PermissionError("A conta SIMPLE vinculada está desativada.")
            return {
                "ok": True,
                "linux_uid": linux_uid,
                "binding_id": row["binding_id"],
                "user_id": row["user_id"],
                "username": row["username"],
            }

    @staticmethod
    def _integer(value: Any, label: str, minimum: int = 1) -> int:
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{label} inválido.")
        return value

    @staticmethod
    def _point_balance(connection: sqlite3.Connection, user_id: int) -> int:
        return connection.execute(
            """
            SELECT COALESCE((SELECT SUM(amount) FROM points_ledger WHERE user_id = ?), 0)
                 + COALESCE((SELECT SUM(amount) FROM admin_point_adjustments WHERE user_id = ?), 0)
            """,
            (user_id, user_id),
        ).fetchone()[0]

    @staticmethod
    def _point_history(
        connection: sqlite3.Connection, user_id: int
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT amount, entry_type, details, created_at
            FROM (
                SELECT id, amount, entry_type, details, created_at, 0 AS source_order
                FROM points_ledger WHERE user_id = ?
                UNION ALL
                SELECT id, amount, 'admin_adjustment', reason, created_at, 1
                FROM admin_point_adjustments WHERE user_id = ?
            )
            ORDER BY created_at DESC, id DESC, source_order DESC
            """,
            (user_id, user_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _exercise(self, connection: sqlite3.Connection, code: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT code, name, description, statement_path, statement, example,
                   delivery_directory, delivery_files, allowed_functions,
                   terminal_model, hints, status
            FROM exercises WHERE code = ?
            """,
            (code,),
        ).fetchone()
        if row is None:
            raise ValueError("Exercício não encontrado.")
        result = dict(row)
        if not result["statement"] and result["statement_path"]:
            filename = Path(result["statement_path"]).name
            exercise_path = self.exercise_storage_dir / filename
            try:
                root = self.exercise_storage_dir.resolve(strict=True)
                resolved_path = exercise_path.resolve(strict=True)
            except OSError as error:
                raise ValueError("Arquivo do enunciado indisponível.") from error
            if not resolved_path.is_relative_to(root) or not resolved_path.is_file():
                raise ValueError("Arquivo do enunciado indisponível.")
            result["statement"] = resolved_path.read_text(encoding="utf-8")
        return result

    def _user_session(self, connection: sqlite3.Connection, user_id: int) -> dict[str, Any]:
        row = connection.execute(
            "SELECT id, username, is_admin, must_change_password, is_active FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None or not row["is_active"]:
            raise ValueError("Usuário ou senha inválidos.")
        token = self._create_session(user_id)
        return {
            "token": token,
            "user_id": row["id"],
            "username": row["username"],
            "is_admin": bool(row["is_admin"]),
            "must_change_password": bool(row["must_change_password"]),
        }

    def _profile(self, connection: sqlite3.Connection, user_id: int) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT username, display_name, github_username, bio, created_at
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Usuário não encontrado.")
        completed = connection.execute(
            """
            SELECT e.code, e.name
            FROM submissions s
            JOIN exercises e ON e.id = s.exercise_id
            WHERE s.user_id = ? AND s.status = 'approved'
            GROUP BY e.id, e.code, e.name
            ORDER BY e.code
            """,
            (user_id,),
        ).fetchall()
        return {
            **dict(row),
            "completed_exercises": [dict(item) for item in completed],
            "points": self._point_balance(connection, user_id),
        }

    def _admin_user(self, connection: sqlite3.Connection, user_id: Any) -> dict[str, Any]:
        user_id = self._integer(user_id, "ID do usuário")
        row = connection.execute(
            """
            SELECT id, username, is_admin, is_active, must_change_password, created_at,
                   display_name, github_username, bio
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Usuário não encontrado.")
        profile = self._profile(connection, user_id)
        submissions = {
            item["status"]: item["count"]
            for item in connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM submissions WHERE user_id = ? GROUP BY status
                """,
                (user_id,),
            ).fetchall()
        }
        reviews = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN r.reviewer_id = ? THEN 1 ELSE 0 END), 0) AS given,
                   COALESCE(SUM(CASE WHEN s.user_id = ? THEN 1 ELSE 0 END), 0) AS received
            FROM reviews r
            JOIN evaluation_slots slot ON slot.id = r.slot_id
            JOIN submissions s ON s.id = slot.submission_id
            """,
            (user_id, user_id),
        ).fetchone()
        return {
            **dict(row),
            "completed_exercises": profile["completed_exercises"],
            "points": profile["points"],
            "submissions": submissions,
            "reviews": dict(reviews),
            "review_history": self._review_history(connection, user_id),
            "points_history": self._point_history(connection, user_id),
        }

    @staticmethod
    def _text(value: Any, label: str, maximum: int, allow_newlines: bool = True) -> str:
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{label} inválido ou excede o limite permitido.")
        if any(
            (ord(char) < 32 and (not allow_newlines or char != "\n"))
            or ord(char) == 127
            for char in value
        ):
            raise ValueError(f"{label} contém caracteres de controle.")
        return value

    @staticmethod
    def _assert_success(connection: sqlite3.Connection, operation: str) -> None:
        if connection.total_changes == 0:
            raise ValueError(operation)

    @staticmethod
    def _expire_evaluation_requests(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE evaluation_requests
            SET status = 'expired', responded_at = CURRENT_TIMESTAMP
            WHERE status = 'pending'
              AND requested_at <= datetime('now', '-24 hours')
            """
        )
        connection.execute(
            """
            UPDATE evaluation_slots
            SET accepted_by = NULL, accepted_at = NULL
            WHERE status = 'open'
              AND EXISTS (
                  SELECT 1 FROM evaluation_requests request
                  WHERE request.slot_id = evaluation_slots.id
                    AND request.reviewer_id = evaluation_slots.accepted_by
                    AND request.status = 'expired'
                    AND request.responded_at IS NOT NULL
                    AND request.requested_at <= datetime('now', '-24 hours')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM evaluation_requests active
                  WHERE active.slot_id = evaluation_slots.id
                    AND active.reviewer_id = evaluation_slots.accepted_by
                    AND active.status IN ('pending', 'accepted')
              )
            """
        )
        connection.commit()

    def dispatch(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("Requisição inválida.")
        method = request.get("method")
        if not isinstance(method, str) or method not in METHOD_FIELDS:
            raise ValueError("Operação não suportada.")
        fields = METHOD_FIELDS[method]
        if set(request) != {"method", *fields}:
            raise ValueError("Campos inválidos para a operação.")

        with closing(self._connect()) as connection, connection:
            if method == "auth.register":
                username = request["username"]
                password = request["password"]
                if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
                    raise ValueError("Nome de usuário inválido.")
                if not isinstance(password, str) or len(password) < 8:
                    raise ValueError("A senha deve conter pelo menos 8 caracteres.")
                try:
                    cursor = connection.execute(
                        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        (username.lower(), _password_hash(password)),
                    )
                except sqlite3.IntegrityError as error:
                    if "users.username" in str(error):
                        raise ValueError("Este nome de usuário já está em uso.") from error
                    raise
                connection.commit()
                return {"ok": True, **self._user_session(connection, cursor.lastrowid)}

            if method == "auth.login":
                username = request["username"]
                password = request["password"]
                if not isinstance(username, str) or not isinstance(password, str):
                    raise ValueError("Usuário ou senha inválidos.")
                row = connection.execute(
                    "SELECT id, password_hash, is_active FROM users WHERE username = ?",
                    (username.lower(),),
                ).fetchone()
                if (
                    row is None
                    or not row["is_active"]
                    or not _verify_password(password, row["password_hash"])
                ):
                    raise ValueError("Usuário ou senha inválidos.")
                return {"ok": True, **self._user_session(connection, row["id"])}

            user_id = self._session_user_id(connection, request["token"])
            if method not in {"auth.change_password", "auth.logout"}:
                password_status = connection.execute(
                    "SELECT must_change_password FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if password_status is None:
                    raise ValueError("Usuário não encontrado.")
                if password_status["must_change_password"]:
                    raise ValueError("Troque a senha temporária antes de continuar.")
            if method in {
                "evaluations.slots.list",
                "evaluations.requests.list",
                "evaluations.slot.get",
                "evaluations.request.respond",
                "evaluations.accept",
                "evaluations.cancel",
                "evaluations.review.submit",
                "notifications.count",
                "progress.get",
            }:
                self._expire_evaluation_requests(connection)
            if method == "auth.change_password":
                password = request["password"]
                if not isinstance(password, str) or len(password) < 8:
                    raise ValueError("A senha deve conter pelo menos 8 caracteres.")
                password_hash = _password_hash(password)
                with self.sessions_lock:
                    session = self.sessions.get(request["token"])
                    if (
                        session is None
                        or session[0] != user_id
                        or session[1] <= time.monotonic()
                    ):
                        self.sessions.pop(request["token"], None)
                        raise ValueError("Sessão inválida ou expirada.")
                    connection.execute("BEGIN IMMEDIATE")
                    updated = connection.execute(
                        """
                        UPDATE users SET password_hash = ?, must_change_password = 0
                        WHERE id = ? AND is_active = 1
                        """,
                        (password_hash, user_id),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("Conta SIMPLE desativada ou inexistente.")
                    connection.commit()
                    self.sessions = {
                        token: active_session
                        for token, active_session in self.sessions.items()
                        if active_session[0] != user_id
                        and active_session[1] > time.monotonic()
                    }
                    replacement_token = self._create_session_locked(user_id)
                return {"ok": True, "token": replacement_token}

            if method == "auth.logout":
                with self.sessions_lock:
                    self.sessions.pop(request["token"], None)
                return {"ok": True}

            if method == "profile.get":
                return {"ok": True, **self._profile(connection, user_id)}

            if method == "profile.update":
                display_name = request["display_name"]
                github_username = request["github_username"]
                bio = request["bio"]
                if not isinstance(display_name, str) or len(display_name) > 80:
                    raise ValueError("Nome de exibição deve ter até 80 caracteres.")
                if any(ord(char) < 32 or ord(char) == 127 for char in display_name):
                    raise ValueError("Nome de exibição contém caracteres de controle.")
                if not isinstance(github_username, str) or (
                    github_username and not GITHUB_PATTERN.fullmatch(github_username)
                ):
                    raise ValueError("Informe apenas um usuário GitHub válido.")
                if not isinstance(bio, str) or len(bio) > 500:
                    raise ValueError("A biografia deve ter até 500 caracteres.")
                if any((ord(char) < 32 and char != "\n") or ord(char) == 127 for char in bio):
                    raise ValueError("A biografia contém caracteres de controle.")
                connection.execute(
                    """
                    UPDATE users
                    SET display_name = ?, github_username = ?, bio = ?
                    WHERE id = ?
                    """,
                    (display_name, github_username, bio, user_id),
                )
                return {"ok": True}

            if method == "catalog.list":
                exercises = connection.execute(
                    """
                    SELECT code, name
                    FROM exercises
                    WHERE status = 'available'
                    ORDER BY code
                    """
                ).fetchall()
                return {"ok": True, "exercises": [dict(item) for item in exercises]}

            if method == "catalog.get":
                code = self._text(request["exercise_code"], "Código", 64, False)
                exercise = self._exercise(connection, code)
                if exercise["status"] != "available":
                    raise ValueError("Exercício indisponível.")
                return {"ok": True, "exercise": exercise}

            if method == "submissions.requirements":
                code = self._text(request["exercise_code"], "Código", 64, False)
                exercise = connection.execute(
                    "SELECT id FROM exercises WHERE code = ? AND status = 'available'",
                    (code,),
                ).fetchone()
                if exercise is None:
                    return {"ok": True, "eligible": False, "reason": "Exercício indisponível."}
                existing = connection.execute(
                    """
                    SELECT status FROM submissions
                    WHERE user_id = ? AND exercise_id = ?
                      AND status IN ('pending', 'approved')
                    LIMIT 1
                    """,
                    (user_id, exercise["id"]),
                ).fetchone()
                balance = self._point_balance(connection, user_id)
                if existing is not None:
                    reason = (
                        "Já existe uma submissão pendente."
                        if existing["status"] == "pending"
                        else "Exercício já aprovado."
                    )
                    return {"ok": True, "eligible": False, "reason": reason, "points": balance}
                if balance < 1:
                    return {
                        "ok": True,
                        "eligible": False,
                        "reason": "Saldo insuficiente para abrir um slot.",
                        "points": balance,
                    }
                return {"ok": True, "eligible": True, "points": balance}

            if method == "submissions.create":
                code = self._text(request["exercise_code"], "Código", 64, False)
                commit_url = request["commit_url"]
                directory = self._text(
                    request["delivery_directory"], "Diretório de entrega", 256, False
                )
                if not isinstance(commit_url, str) or not COMMIT_URL_PATTERN.fullmatch(commit_url):
                    raise ValueError("URL direta de commit GitHub inválida.")
                if not directory or directory.startswith("/") or "\\" in directory:
                    raise ValueError("Diretório de entrega relativo inválido.")
                if any(part == ".." for part in directory.split("/")):
                    raise ValueError("O diretório não pode sair da raiz do repositório.")
                if directory.startswith("~") or directory.startswith("//"):
                    raise ValueError("Diretório de entrega relativo inválido.")
                connection.execute("BEGIN IMMEDIATE")
                exercise = connection.execute(
                    "SELECT id FROM exercises WHERE code = ? AND status = 'available'",
                    (code,),
                ).fetchone()
                if exercise is None:
                    raise ValueError("Exercício indisponível.")
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO submissions (
                            user_id, exercise_id, content, content_kind,
                            commit_url, delivery_directory
                        ) VALUES (?, ?, 'Entrega referenciada no GitHub',
                                  'github_commit', ?, ?)
                        """,
                        (user_id, exercise["id"], commit_url, directory),
                    )
                except sqlite3.IntegrityError as error:
                    raise ValueError(str(error)) from error
                slot = connection.execute(
                    "SELECT id FROM evaluation_slots WHERE submission_id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                connection.commit()
                return {
                    "ok": True,
                    "submission_id": cursor.lastrowid,
                    "slot_id": slot["id"],
                    "points": self._point_balance(connection, user_id),
                }

            if method == "submissions.list":
                rows = connection.execute(
                    """
                    SELECT s.id, e.code AS exercise_code, e.name AS exercise_name,
                           s.status, s.content_kind, s.commit_url, s.delivery_directory,
                           s.submitted_at, slot.id AS slot_id, r.score, r.feedback
                    FROM submissions s
                    JOIN exercises e ON e.id = s.exercise_id
                    LEFT JOIN evaluation_slots slot ON slot.submission_id = s.id
                    LEFT JOIN reviews r ON r.slot_id = slot.id
                    WHERE s.user_id = ?
                    ORDER BY s.submitted_at DESC, s.id DESC
                    """,
                    (user_id,),
                ).fetchall()
                return {"ok": True, "submissions": [dict(row) for row in rows]}

            if method == "submissions.get":
                submission_id = self._integer(request["submission_id"], "Submissão")
                row = connection.execute(
                    """
                    SELECT s.id, e.code AS exercise_code, e.name AS exercise_name,
                           s.status, s.content_kind, s.commit_url, s.delivery_directory,
                           s.submitted_at, slot.id AS slot_id, r.score, r.feedback
                    FROM submissions s
                    JOIN exercises e ON e.id = s.exercise_id
                    LEFT JOIN evaluation_slots slot ON slot.submission_id = s.id
                    LEFT JOIN reviews r ON r.slot_id = slot.id
                    WHERE s.id = ? AND s.user_id = ?
                    """,
                    (submission_id, user_id),
                ).fetchone()
                if row is None:
                    raise ValueError("Submissão não encontrada.")
                return {"ok": True, "submission": dict(row)}

            if method == "evaluations.slots.list":
                rows = connection.execute(
                    """
                    SELECT slot.id, u.username, e.code AS exercise_code,
                           e.name AS exercise_name, slot.created_at,
                           COALESCE(request.id, '') AS request_id,
                           COALESCE(request.status, '') AS request_status,
                           CASE WHEN slot.accepted_by = ? THEN 1 ELSE 0 END AS accepted_by_me
                    FROM evaluation_slots slot
                    JOIN submissions s ON s.id = slot.submission_id
                    JOIN users u ON u.id = s.user_id
                    JOIN exercises e ON e.id = s.exercise_id
                    LEFT JOIN evaluation_requests request
                      ON request.slot_id = slot.id
                     AND request.reviewer_id = ?
                     AND request.status IN ('pending', 'accepted')
                    WHERE slot.status = 'open' AND s.user_id != ?
                      AND (slot.accepted_by IS NULL OR slot.accepted_by = ?)
                      AND NOT EXISTS (
                          SELECT 1 FROM evaluation_requests declined
                          WHERE declined.slot_id = slot.id
                            AND declined.reviewer_id = ?
                            AND declined.status = 'declined'
                      )
                    ORDER BY slot.created_at, slot.id
                    """,
                    (user_id, user_id, user_id, user_id, user_id),
                ).fetchall()
                return {"ok": True, "slots": [dict(row) for row in rows]}

            if method == "evaluations.requests.list":
                rows = connection.execute(
                    """
                    SELECT request.id AS request_id, slot.id AS slot_id,
                           evaluator.username AS reviewer,
                           e.code AS exercise_code, e.name AS exercise_name,
                           request.requested_at
                    FROM evaluation_requests request
                    JOIN evaluation_slots slot ON slot.id = request.slot_id
                    JOIN submissions s ON s.id = slot.submission_id
                    JOIN users evaluator ON evaluator.id = request.reviewer_id
                    JOIN exercises e ON e.id = s.exercise_id
                    WHERE s.user_id = ? AND slot.status = 'open'
                      AND slot.accepted_by = request.reviewer_id
                      AND request.status = 'pending'
                    ORDER BY request.requested_at, request.id
                    """,
                    (user_id,),
                ).fetchall()
                return {"ok": True, "requests": [dict(row) for row in rows]}

            if method == "evaluations.request.respond":
                request_id = self._integer(request["request_id"], "Pedido")
                decision = request["decision"]
                if not isinstance(decision, str) or decision not in {"accept", "decline"}:
                    raise ValueError("Decisão de pedido inválida.")
                connection.execute("BEGIN IMMEDIATE")
                pending = connection.execute(
                    """
                    SELECT request.slot_id, request.reviewer_id
                    FROM evaluation_requests request
                    JOIN evaluation_slots slot ON slot.id = request.slot_id
                    JOIN submissions s ON s.id = slot.submission_id
                    WHERE request.id = ? AND request.status = 'pending'
                      AND slot.status = 'open' AND s.user_id = ?
                      AND slot.accepted_by = request.reviewer_id
                    """,
                    (request_id, user_id),
                ).fetchone()
                if pending is None:
                    raise ValueError("Pedido expirado ou indisponível.")
                next_status = "accepted" if decision == "accept" else "declined"
                updated = connection.execute(
                    """
                    UPDATE evaluation_requests
                    SET status = ?, responded_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pending'
                    """,
                    (next_status, request_id),
                )
                if updated.rowcount != 1:
                    raise ValueError("Pedido expirado ou indisponível.")
                if decision == "decline":
                    connection.execute(
                        """
                        UPDATE evaluation_slots
                        SET accepted_by = NULL, accepted_at = NULL
                        WHERE id = ? AND status = 'open' AND accepted_by = ?
                        """,
                        (pending["slot_id"], pending["reviewer_id"]),
                    )
                connection.commit()
                return {"ok": True, "status": next_status}

            if method == "evaluations.slot.get":
                slot_id = self._integer(request["slot_id"], "Slot")
                row = connection.execute(
                    """
                    SELECT slot.id, u.username, e.code AS exercise_code,
                           e.name AS exercise_name, e.description, e.statement,
                           s.content_kind, s.content,
                           s.commit_url, s.delivery_directory, slot.accepted_by
                    FROM evaluation_slots slot
                    JOIN submissions s ON s.id = slot.submission_id
                    JOIN users u ON u.id = s.user_id
                    JOIN exercises e ON e.id = s.exercise_id
                    JOIN evaluation_requests request
                      ON request.slot_id = slot.id
                     AND request.reviewer_id = slot.accepted_by
                     AND request.status = 'accepted'
                    WHERE slot.id = ? AND slot.status = 'open'
                      AND s.user_id != ?
                      AND slot.accepted_by = ?
                    """,
                    (slot_id, user_id, user_id),
                ).fetchone()
                if row is None:
                    raise ValueError("O autor ainda não aceitou este pedido ou o slot está indisponível.")
                slot = dict(row)
                exercise = self._exercise(connection, slot["exercise_code"])
                slot["description"] = exercise["description"]
                slot["statement"] = exercise["statement"]
                return {"ok": True, "slot": slot}

            if method == "evaluations.review.get":
                review_id = self._integer(request["review_id"], "Avaliação")
                row = connection.execute(
                    """
                    SELECT r.id AS review_id, e.code AS exercise_code,
                           reviewer.username AS reviewer, r.score, r.feedback
                    FROM reviews r
                    JOIN evaluation_slots slot ON slot.id = r.slot_id
                    JOIN submissions s ON s.id = slot.submission_id
                    JOIN exercises e ON e.id = s.exercise_id
                    JOIN users reviewer ON reviewer.id = r.reviewer_id
                    WHERE r.id = ? AND s.user_id = ?
                      AND r.recipient_responded_at IS NULL
                    """,
                    (review_id, user_id),
                ).fetchone()
                if row is None:
                    raise ValueError("Avaliação indisponível ou já respondida.")
                return {"ok": True, "review": dict(row)}

            if method == "evaluations.accept":
                slot_id = self._integer(request["slot_id"], "Slot")
                connection.execute("BEGIN IMMEDIATE")
                declined = connection.execute(
                    """
                    SELECT 1 FROM evaluation_requests
                    WHERE slot_id = ? AND reviewer_id = ? AND status = 'declined'
                    LIMIT 1
                    """,
                    (slot_id, user_id),
                ).fetchone()
                if declined is not None:
                    raise ValueError("O autor recusou um pedido anterior para esta entrega.")
                existing = connection.execute(
                    """
                    SELECT slot.accepted_by, request.id AS request_id,
                           request.status AS request_status
                    FROM evaluation_slots slot
                    LEFT JOIN evaluation_requests request
                      ON request.slot_id = slot.id
                     AND request.reviewer_id = ?
                     AND request.status IN ('pending', 'accepted')
                    JOIN submissions s ON s.id = slot.submission_id
                    WHERE slot.id = ? AND slot.status = 'open'
                      AND s.user_id != ?
                      AND (slot.accepted_by IS NULL OR slot.accepted_by = ?)
                    """,
                    (user_id, slot_id, user_id, user_id),
                ).fetchone()
                if existing is None:
                    raise ValueError("Slot indisponível ou já reservado por outro avaliador.")
                if existing["request_id"] is None:
                    inserted = connection.execute(
                        """
                        INSERT INTO evaluation_requests (slot_id, reviewer_id, status)
                        VALUES (?, ?, 'pending')
                        """,
                        (slot_id, user_id),
                    )
                    updated = connection.execute(
                        """
                        UPDATE evaluation_slots
                        SET accepted_by = ?, accepted_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND status = 'open'
                          AND (accepted_by IS NULL OR accepted_by = ?)
                        """,
                        (user_id, slot_id, user_id),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("Slot indisponível ou já reservado por outro avaliador.")
                    request_id = inserted.lastrowid
                    request_status = "pending"
                else:
                    request_id = existing["request_id"]
                    request_status = existing["request_status"]
                connection.commit()
                return {
                    "ok": True,
                    "slot_id": slot_id,
                    "request_id": request_id,
                    "status": request_status,
                }

            if method == "evaluations.cancel":
                slot_id = self._integer(request["slot_id"], "Slot")
                connection.execute("BEGIN IMMEDIATE")
                active_request = connection.execute(
                    """
                    SELECT id FROM evaluation_requests
                    WHERE slot_id = ? AND reviewer_id = ?
                      AND status IN ('pending', 'accepted')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (slot_id, user_id),
                ).fetchone()
                if active_request is None:
                    raise ValueError("Não há pedido seu que possa ser cancelado.")
                connection.execute(
                    """
                    UPDATE evaluation_requests
                    SET status = 'cancelled', responded_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (active_request["id"],),
                )
                updated = connection.execute(
                    """
                    UPDATE evaluation_slots
                    SET accepted_by = NULL, accepted_at = NULL
                    WHERE id = ? AND status = 'open' AND accepted_by = ?
                    """,
                    (slot_id, user_id),
                )
                if updated.rowcount != 1:
                    raise ValueError("O pedido não pode mais ser cancelado.")
                connection.commit()
                return {"ok": True}

            if method == "evaluations.review.submit":
                slot_id = self._integer(request["slot_id"], "Slot")
                score_raw = request["score"]
                if not isinstance(score_raw, str) or not re.fullmatch(r"[0-5]", score_raw):
                    raise ValueError("A nota deve ser um inteiro entre 0 e 5.")
                feedback = self._text(request["feedback"], "Feedback", 2000)
                if not feedback.strip():
                    raise ValueError("O feedback da avaliação é obrigatório.")
                connection.execute("BEGIN IMMEDIATE")
                accepted = connection.execute(
                    """
                    SELECT slot.id FROM evaluation_slots slot
                    JOIN submissions s ON s.id = slot.submission_id
                    JOIN evaluation_requests request
                      ON request.slot_id = slot.id
                     AND request.reviewer_id = slot.accepted_by
                     AND request.status = 'accepted'
                    WHERE slot.id = ? AND slot.status = 'open'
                      AND slot.accepted_by = ? AND s.user_id != ?
                    """,
                    (slot_id, user_id, user_id),
                ).fetchone()
                if accepted is None:
                    raise ValueError("Aceite o slot antes de registrar a avaliação.")
                try:
                    review = connection.execute(
                        """
                        INSERT INTO reviews (slot_id, reviewer_id, score, feedback)
                        VALUES (?, ?, ?, ?)
                        """,
                        (slot_id, user_id, int(score_raw), feedback),
                    )
                except sqlite3.IntegrityError as error:
                    raise ValueError("Slot já avaliado ou avaliação inválida.") from error
                owner = connection.execute(
                    """
                    SELECT s.user_id
                    FROM evaluation_slots slot
                    JOIN submissions s ON s.id = slot.submission_id
                    WHERE slot.id = ?
                    """,
                    (slot_id,),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO notifications (user_id, type, review_id)
                    VALUES (?, 'review_received', ?)
                    """,
                    (owner["user_id"], review.lastrowid),
                )
                connection.execute(
                    """
                    UPDATE evaluation_requests
                    SET status = 'completed', responded_at = CURRENT_TIMESTAMP
                    WHERE slot_id = ? AND reviewer_id = ? AND status = 'accepted'
                    """,
                    (slot_id, user_id),
                )
                connection.commit()
                return {
                    "ok": True,
                    "review_id": review.lastrowid,
                    "status": "approved" if int(score_raw) >= 3 else "rejected",
                    "points": self._point_balance(connection, user_id),
                }

            if method == "evaluations.review.respond":
                review_id = self._integer(request["review_id"], "Avaliação")
                score_raw = request["score"]
                if not isinstance(score_raw, str) or not re.fullmatch(r"[0-5]", score_raw):
                    raise ValueError("A nota deve ser um inteiro entre 0 e 5.")
                feedback = self._text(request["feedback"], "Resposta", 2000)
                if not feedback.strip():
                    raise ValueError("A resposta da avaliação é obrigatória.")
                connection.execute("BEGIN IMMEDIATE")
                review = connection.execute(
                    """
                    SELECT r.reviewer_id
                    FROM reviews r
                    JOIN evaluation_slots slot ON slot.id = r.slot_id
                    JOIN submissions s ON s.id = slot.submission_id
                    WHERE r.id = ? AND s.user_id = ?
                      AND r.recipient_responded_at IS NULL
                    """,
                    (review_id, user_id),
                ).fetchone()
                if review is None:
                    raise ValueError("Avaliação indisponível ou já respondida.")
                updated = connection.execute(
                    """
                    UPDATE reviews
                    SET recipient_score = ?, recipient_feedback = ?,
                        recipient_responded_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND recipient_responded_at IS NULL
                    """,
                    (int(score_raw), feedback, review_id),
                )
                if updated.rowcount != 1:
                    raise ValueError("Avaliação indisponível ou já respondida.")
                connection.execute(
                    """
                    INSERT INTO notifications (user_id, type, review_id)
                    VALUES (?, 'response_received', ?)
                    """,
                    (review["reviewer_id"], review_id),
                )
                connection.commit()
                return {"ok": True}

            if method == "evaluations.history":
                rows = connection.execute(
                    """
                    SELECT r.id, e.code AS exercise_code, r.score, r.feedback,
                           r.created_at, owner.username AS submitter,
                           reviewer.username AS reviewer,
                           CASE WHEN s.user_id = ? THEN 'received' ELSE 'given' END AS relation
                    FROM reviews r
                    JOIN evaluation_slots slot ON slot.id = r.slot_id
                    JOIN submissions s ON s.id = slot.submission_id
                    JOIN exercises e ON e.id = s.exercise_id
                    JOIN users reviewer ON reviewer.id = r.reviewer_id
                    JOIN users owner ON owner.id = s.user_id
                    WHERE s.user_id = ? OR r.reviewer_id = ?
                    ORDER BY r.created_at DESC, r.id DESC
                    """,
                    (user_id, user_id, user_id),
                ).fetchall()
                return {"ok": True, "reviews": [dict(row) for row in rows]}

            if method == "points.balance":
                return {"ok": True, "points": self._point_balance(connection, user_id)}

            if method == "points.history":
                return {"ok": True, "entries": self._point_history(connection, user_id)}

            if method == "progress.get":
                profile = self._profile(connection, user_id)
                submissions = connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM submissions WHERE user_id = ? GROUP BY status
                    """,
                    (user_id,),
                ).fetchall()
                reviews = connection.execute(
                    """
                    SELECT COUNT(*) AS total,
                           COALESCE(SUM(CASE WHEN r.reviewer_id = ? THEN 1 ELSE 0 END), 0) AS given,
                           COALESCE(SUM(CASE WHEN s.user_id = ? THEN 1 ELSE 0 END), 0) AS received
                    FROM reviews r
                    JOIN evaluation_slots slot ON slot.id = r.slot_id
                    JOIN submissions s ON s.id = slot.submission_id
                    """,
                    (user_id, user_id),
                ).fetchone()
                accepted_evaluation_requests = connection.execute(
                    """
                    SELECT request.id AS request_id, slot.id AS slot_id,
                           owner.username AS submitter, e.code AS exercise_code,
                           e.name AS exercise_name, request.responded_at AS accepted_at
                    FROM evaluation_requests request
                    JOIN evaluation_slots slot ON slot.id = request.slot_id
                    JOIN submissions s ON s.id = slot.submission_id
                    JOIN users owner ON owner.id = s.user_id
                    JOIN exercises e ON e.id = s.exercise_id
                    WHERE request.reviewer_id = ? AND request.status = 'accepted'
                      AND slot.status = 'open' AND slot.accepted_by = ?
                    ORDER BY request.responded_at, request.id
                    """,
                    (user_id, user_id),
                ).fetchall()
                return {
                    "ok": True,
                    **profile,
                    "submissions": {row["status"]: row["count"] for row in submissions},
                    "reviews": dict(reviews),
                    "review_history": self._review_history(connection, user_id),
                    "pending_review_responses": self._pending_review_responses(
                        connection, user_id
                    ),
                    "pending_evaluation_requests": connection.execute(
                        """
                        SELECT COUNT(*) FROM evaluation_requests request
                        JOIN evaluation_slots slot ON slot.id = request.slot_id
                        JOIN submissions s ON s.id = slot.submission_id
                        WHERE s.user_id = ? AND slot.status = 'open'
                          AND slot.accepted_by = request.reviewer_id
                          AND request.status = 'pending'
                        """,
                        (user_id,),
                    ).fetchone()[0],
                    "accepted_evaluation_requests": [
                        dict(row) for row in accepted_evaluation_requests
                    ],
                    "notifications": self._notification_history(connection, user_id),
                    "points_history": self._point_history(connection, user_id),
                }

            if method == "notifications.count":
                count = connection.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM notifications
                       WHERE user_id = ? AND read_at IS NULL) +
                      (SELECT COUNT(*) FROM evaluation_requests request
                       JOIN evaluation_slots slot ON slot.id = request.slot_id
                       JOIN submissions s ON s.id = slot.submission_id
                       WHERE s.user_id = ? AND slot.status = 'open'
                         AND slot.accepted_by = request.reviewer_id
                         AND request.status = 'pending') +
                      (SELECT COUNT(*) FROM evaluation_requests request
                       JOIN evaluation_slots slot ON slot.id = request.slot_id
                       WHERE request.reviewer_id = ? AND request.status = 'accepted'
                         AND slot.status = 'open' AND slot.accepted_by = ?)
                    """,
                    (user_id, user_id, user_id, user_id),
                ).fetchone()[0]
                return {"ok": True, "count": count}

            if method == "notifications.mark_read":
                connection.execute(
                    """
                    UPDATE notifications SET read_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND read_at IS NULL
                    """,
                    (user_id,),
                )
                return {"ok": True}

            if method.startswith("admin."):
                admin_id = self._require_admin(connection, request["token"])
                if method == "admin.identity.list":
                    rows = connection.execute(
                        """
                        SELECT identity_bindings.linux_uid, identity_bindings.user_id,
                               identity_bindings.is_active, identity_bindings.created_at,
                               identity_bindings.updated_at, users.username,
                               users.is_active AS account_active, users.is_admin
                        FROM identity_bindings
                        JOIN users ON users.id = identity_bindings.user_id
                        ORDER BY identity_bindings.linux_uid
                        """
                    ).fetchall()
                    return {"ok": True, "identities": [dict(row) for row in rows]}

                if method == "admin.identity.get":
                    linux_uid = self._integer(
                        request["linux_uid"], "UID Linux", minimum=0
                    )
                    row = connection.execute(
                        """
                        SELECT identity_bindings.linux_uid, identity_bindings.user_id,
                               identity_bindings.is_active, identity_bindings.created_at,
                               identity_bindings.updated_at, users.username,
                               users.is_active AS account_active, users.is_admin
                        FROM identity_bindings
                        JOIN users ON users.id = identity_bindings.user_id
                        WHERE identity_bindings.linux_uid = ?
                        ORDER BY identity_bindings.is_active DESC,
                                 identity_bindings.created_at DESC
                        LIMIT 1
                        """,
                        (linux_uid,),
                    ).fetchone()
                    if row is None:
                        raise ValueError("Vínculo de identidade não encontrado.")
                    return {"ok": True, "identity": dict(row)}

                if method == "admin.identity.bind":
                    linux_uid = self._integer(
                        request["linux_uid"], "UID Linux", minimum=1
                    )
                    user_id = self._integer(request["user_id"], "ID SIMPLE")
                    if linux_uid == os.geteuid():
                        raise ValueError("O UID do serviço Broker não pode ser vinculado.")
                    connection.execute("BEGIN IMMEDIATE")
                    account = connection.execute(
                        "SELECT is_active FROM users WHERE id = ?", (user_id,)
                    ).fetchone()
                    if account is None or not account["is_active"]:
                        raise ValueError("A conta SIMPLE não existe ou está desativada.")
                    try:
                        connection.execute(
                            """
                            INSERT INTO identity_bindings (linux_uid, user_id)
                            VALUES (?, ?)
                            """,
                            (linux_uid, user_id),
                        )
                    except sqlite3.IntegrityError as error:
                        raise ValueError(
                            "UID Linux ou conta SIMPLE já possui vínculo."
                        ) from error
                    connection.commit()
                    return {"ok": True, "linux_uid": linux_uid, "user_id": user_id}

                if method == "admin.identity.update":
                    linux_uid = self._integer(
                        request["linux_uid"], "UID Linux", minimum=1
                    )
                    new_linux_uid = self._integer(
                        request["new_linux_uid"], "Novo UID Linux", minimum=1
                    )
                    if new_linux_uid == os.geteuid():
                        raise ValueError("O UID do serviço Broker não pode ser vinculado.")
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        updated = connection.execute(
                            """
                            UPDATE identity_bindings
                            SET linux_uid = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE linux_uid = ? AND is_active = 1
                            """,
                            (new_linux_uid, linux_uid),
                        )
                    except sqlite3.IntegrityError as error:
                        raise ValueError("O novo UID Linux já possui vínculo.") from error
                    if updated.rowcount != 1:
                        raise ValueError("Vínculo de identidade não encontrado.")
                    connection.commit()
                    return {
                        "ok": True,
                        "linux_uid": linux_uid,
                        "new_linux_uid": new_linux_uid,
                    }

                if method == "admin.identity.revoke":
                    linux_uid = self._integer(
                        request["linux_uid"], "UID Linux", minimum=1
                    )
                    updated = connection.execute(
                        """
                        UPDATE identity_bindings
                        SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                        WHERE linux_uid = ? AND is_active = 1
                        """,
                        (linux_uid,),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("Vínculo inexistente ou já revogado.")
                    return {"ok": True, "linux_uid": linux_uid, "is_active": False}

                if method == "admin.users.list":
                    users = connection.execute(
                        """
                        SELECT u.id, u.username, u.is_admin, u.is_active, u.created_at,
                               (SELECT COUNT(DISTINCT s.exercise_id) FROM submissions s
                                WHERE s.user_id = u.id AND s.status = 'approved') AS approved,
                               COALESCE((SELECT SUM(p.amount) FROM points_ledger p
                                         WHERE p.user_id = u.id), 0) +
                               COALESCE((SELECT SUM(a.amount) FROM admin_point_adjustments a
                                         WHERE a.user_id = u.id), 0) AS points
                        FROM users u ORDER BY u.username
                        """
                    ).fetchall()
                    return {"ok": True, "users": [dict(row) for row in users]}

                if method == "admin.user.get":
                    target_id = self._integer(request["user_id"], "ID do usuário")
                    return {"ok": True, "user": self._admin_user(connection, target_id)}

                if method == "admin.user.state":
                    target_id = self._integer(request["user_id"], "ID do usuário")
                    active_raw = request["is_active"]
                    if not isinstance(active_raw, str) or active_raw not in {"0", "1"}:
                        raise ValueError("Estado da conta inválido.")
                    active = int(active_raw)
                    connection.execute("BEGIN IMMEDIATE")
                    target = connection.execute(
                        "SELECT is_admin, is_active FROM users WHERE id = ?", (target_id,)
                    ).fetchone()
                    if target is None:
                        raise ValueError("Usuário não encontrado.")
                    if target_id == admin_id and not active:
                        raise ValueError("Não é permitido desativar a própria conta.")
                    if target["is_admin"] and not active:
                        other_admins = connection.execute(
                            """
                            SELECT COUNT(*) FROM users
                            WHERE is_admin = 1 AND is_active = 1 AND id != ?
                            """,
                            (target_id,),
                        ).fetchone()[0]
                        if other_admins == 0:
                            raise ValueError("Não é permitido desativar o último administrador ativo.")
                    connection.execute(
                        "UPDATE users SET is_active = ? WHERE id = ?",
                        (active, target_id),
                    )
                    connection.commit()
                    if not active:
                        with self.sessions_lock:
                            self.sessions = {
                                key: value
                                for key, value in self.sessions.items()
                                if value[0] != target_id
                            }
                    return {"ok": True, "is_active": bool(active)}

                if method == "admin.password.reset":
                    target_id = self._integer(request["user_id"], "ID do usuário")
                    password = request["password"]
                    if not isinstance(password, str) or len(password) < 8:
                        raise ValueError("A senha deve conter pelo menos 8 caracteres.")
                    password_hash = _password_hash(password)
                    connection.execute("BEGIN IMMEDIATE")
                    updated = connection.execute(
                        """
                        UPDATE users SET password_hash = ?, must_change_password = 1
                        WHERE id = ?
                        """,
                        (password_hash, target_id),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("Usuário não encontrado.")
                    connection.commit()
                    with self.sessions_lock:
                        self.sessions = {
                            key: value
                            for key, value in self.sessions.items()
                            if value[0] != target_id
                        }
                    return {"ok": True}

                if method == "admin.points.adjust":
                    target_id = self._integer(request["user_id"], "ID do usuário")
                    amount_raw = request["amount"]
                    if not isinstance(amount_raw, str) or not re.fullmatch(
                        r"-?[1-9][0-9]{0,8}", amount_raw
                    ):
                        raise ValueError("Quantidade de pontos inválida.")
                    amount = int(amount_raw)
                    reason = self._text(request["reason"], "Justificativa", 500, False)
                    if not reason.strip():
                        raise ValueError("A justificativa é obrigatória.")
                    connection.execute("BEGIN IMMEDIATE")
                    target = connection.execute(
                        "SELECT id FROM users WHERE id = ?", (target_id,)
                    ).fetchone()
                    if target is None:
                        raise ValueError("Usuário não encontrado.")
                    balance = self._point_balance(connection, target_id)
                    if balance + amount < 0:
                        raise ValueError("O ajuste não pode deixar o saldo negativo.")
                    connection.execute(
                        """
                        INSERT INTO admin_point_adjustments (user_id, admin_id, amount, reason)
                        VALUES (?, ?, ?, ?)
                        """,
                        (target_id, admin_id, amount, reason),
                    )
                    connection.commit()
                    return {
                        "ok": True,
                        "points": self._point_balance(connection, target_id),
                    }

                if method == "admin.exercises.list":
                    exercises = connection.execute(
                        """
                        SELECT id, code, name, status FROM exercises ORDER BY code
                        """
                    ).fetchall()
                    return {"ok": True, "exercises": [dict(row) for row in exercises]}

                if method == "admin.exercise.create":
                    values = {
                        field: self._text(request[field], field, 100000)
                        for field in (
                            "name", "description", "statement", "example",
                            "delivery_directory", "delivery_files", "allowed_functions",
                            "terminal_model", "hints",
                        )
                    }
                    values["name"] = self._text(request["name"], "Nome", 120, False)
                    values["delivery_directory"] = self._text(
                        request["delivery_directory"], "Diretório de entrega", 256, False
                    )
                    code = self._text(request["code"], "Código", 64, False).upper()
                    if not EXERCISE_CODE_PATTERN.fullmatch(code):
                        raise ValueError("Código de exercício inválido.")
                    if not values["name"].strip() or not values["description"].strip():
                        raise ValueError("Nome e descrição são obrigatórios.")
                    if not values["statement"].strip():
                        raise ValueError("O enunciado é obrigatório.")
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        inserted = connection.execute(
                            """
                            INSERT INTO exercises (
                                code, name, description, statement, example,
                                delivery_directory, delivery_files, allowed_functions,
                                terminal_model, hints, status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available')
                            """,
                            (
                                code, values["name"], values["description"],
                                values["statement"], values["example"],
                                values["delivery_directory"], values["delivery_files"],
                                values["allowed_functions"], values["terminal_model"],
                                values["hints"],
                            ),
                        )
                    except sqlite3.IntegrityError as error:
                        raise ValueError("Código de exercício já cadastrado.") from error
                    connection.commit()
                    return {"ok": True, "exercise_id": inserted.lastrowid, "code": code}

                if method == "admin.exercise.update":
                    code = self._text(request["exercise_code"], "Código", 64, False)
                    field = request["field"]
                    allowed_fields = {
                        "name", "description", "statement", "example",
                        "delivery_directory", "delivery_files", "allowed_functions",
                        "terminal_model", "hints",
                    }
                    if not isinstance(field, str) or field not in allowed_fields:
                        raise ValueError("Campo de exercício não editável.")
                    value = self._text(request["value"], str(field), 100000)
                    if field in {"name", "delivery_directory"}:
                        value = self._text(request["value"], str(field), 256, False)
                    if field in {"name", "description", "statement"} and not value.strip():
                        raise ValueError(f"{field} não pode ficar vazio.")
                    connection.execute("BEGIN IMMEDIATE")
                    updated = connection.execute(
                        f"UPDATE exercises SET {field} = ? WHERE code = ?",
                        (value, code),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("Exercício não encontrado.")
                    connection.commit()
                    return {"ok": True}

                if method in {"admin.exercise.archive", "admin.exercise.reactivate"}:
                    code = self._text(request["exercise_code"], "Código", 64, False)
                    old_status, new_status = (
                        ("available", "archived")
                        if method == "admin.exercise.archive"
                        else ("archived", "available")
                    )
                    updated = connection.execute(
                        "UPDATE exercises SET status = ? WHERE code = ? AND status = ?",
                        (new_status, code, old_status),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("Exercício não encontrado ou estado já alterado.")
                    return {"ok": True, "status": new_status}

                if method == "admin.submissions.list":
                    rows = connection.execute(
                        """
                        SELECT s.id, u.username, e.code AS exercise_code, s.status,
                               s.content_kind, s.commit_url, s.delivery_directory,
                               s.submitted_at, slot.id AS slot_id
                        FROM submissions s
                        JOIN users u ON u.id = s.user_id
                        JOIN exercises e ON e.id = s.exercise_id
                        LEFT JOIN evaluation_slots slot ON slot.submission_id = s.id
                        ORDER BY s.submitted_at DESC, s.id DESC
                        """
                    ).fetchall()
                    return {"ok": True, "submissions": [dict(row) for row in rows]}

                if method == "admin.evaluations.list":
                    rows = connection.execute(
                        """
                        SELECT slot.id AS slot_id, slot.status, owner.username AS submitter,
                               reviewer.username AS reviewer, reservation.username AS reserved_by,
                               slot.accepted_at, e.code AS exercise_code,
                               r.score, r.feedback, slot.created_at,
                               slot.completed_at
                        FROM evaluation_slots slot
                        JOIN submissions s ON s.id = slot.submission_id
                        JOIN exercises e ON e.id = s.exercise_id
                        JOIN users owner ON owner.id = s.user_id
                        LEFT JOIN users reviewer ON reviewer.id = slot.reviewer_id
                        LEFT JOIN users reservation ON reservation.id = slot.accepted_by
                        LEFT JOIN reviews r ON r.slot_id = slot.id
                        ORDER BY slot.created_at DESC, slot.id DESC
                        """
                    ).fetchall()
                    return {"ok": True, "evaluations": [dict(row) for row in rows]}

                if method == "admin.points.history":
                    target_id = self._integer(request["user_id"], "ID do usuário")
                    if connection.execute(
                        "SELECT 1 FROM users WHERE id = ?", (target_id,)
                    ).fetchone() is None:
                        raise ValueError("Usuário não encontrado.")
                    return {
                        "ok": True,
                        "entries": self._point_history(connection, target_id),
                    }

                if method == "admin.report":
                    counts = {
                        "users": connection.execute(
                            "SELECT COUNT(*) FROM users"
                        ).fetchone()[0],
                        "active_users": connection.execute(
                            "SELECT COUNT(*) FROM users WHERE is_active = 1"
                        ).fetchone()[0],
                        "available_exercises": connection.execute(
                            "SELECT COUNT(*) FROM exercises WHERE status = 'available'"
                        ).fetchone()[0],
                        "archived_exercises": connection.execute(
                            "SELECT COUNT(*) FROM exercises WHERE status = 'archived'"
                        ).fetchone()[0],
                        "submissions": connection.execute(
                            "SELECT COUNT(*) FROM submissions"
                        ).fetchone()[0],
                        "open_slots": connection.execute(
                            "SELECT COUNT(*) FROM evaluation_slots WHERE status = 'open'"
                        ).fetchone()[0],
                        "reviews": connection.execute(
                            "SELECT COUNT(*) FROM reviews"
                        ).fetchone()[0],
                    }
                    return {"ok": True, "report": counts}

        raise ValueError("Operação não suportada.")

    @staticmethod
    def _review_history(
        connection: sqlite3.Connection, user_id: int
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT r.id AS review_id, e.code AS exercise_code,
                   r.score, r.feedback, r.created_at,
                   r.recipient_score, r.recipient_feedback,
                   r.recipient_responded_at,
                   owner.username AS submitter, reviewer.username AS reviewer,
                   CASE WHEN s.user_id = ? THEN 'received' ELSE 'given' END AS relation
            FROM reviews r
            JOIN evaluation_slots slot ON slot.id = r.slot_id
            JOIN submissions s ON s.id = slot.submission_id
            JOIN exercises e ON e.id = s.exercise_id
            JOIN users reviewer ON reviewer.id = r.reviewer_id
            JOIN users owner ON owner.id = s.user_id
            WHERE s.user_id = ? OR r.reviewer_id = ?
            ORDER BY r.created_at DESC, r.id DESC
            """,
            (user_id, user_id, user_id),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _pending_review_responses(
        connection: sqlite3.Connection, user_id: int
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT r.id AS review_id, e.code AS exercise_code,
                   r.score, r.feedback, reviewer.username AS reviewer
            FROM reviews r
            JOIN evaluation_slots slot ON slot.id = r.slot_id
            JOIN submissions s ON s.id = slot.submission_id
            JOIN exercises e ON e.id = s.exercise_id
            JOIN users reviewer ON reviewer.id = r.reviewer_id
            WHERE s.user_id = ? AND r.recipient_responded_at IS NULL
            ORDER BY r.created_at, r.id
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _notification_history(
        connection: sqlite3.Connection, user_id: int
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT n.type, e.code AS exercise_code, n.created_at,
                   CASE WHEN n.type = 'review_received' THEN r.score
                        ELSE r.recipient_score END AS score,
                   CASE WHEN n.type = 'review_received' THEN r.feedback
                        ELSE r.recipient_feedback END AS feedback,
                   CASE WHEN n.type = 'review_received' THEN reviewer.username
                        ELSE owner.username END AS other_user
            FROM notifications n
            JOIN reviews r ON r.id = n.review_id
            JOIN evaluation_slots slot ON slot.id = r.slot_id
            JOIN submissions s ON s.id = slot.submission_id
            JOIN exercises e ON e.id = s.exercise_id
            JOIN users reviewer ON reviewer.id = r.reviewer_id
            JOIN users owner ON owner.id = s.user_id
            WHERE n.user_id = ? AND n.read_at IS NULL
            ORDER BY n.created_at DESC, n.id DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


class BrokerRequestHandler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.request.settimeout(10)

    def handle(self) -> None:
        try:
            raw_request = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            logging.debug("Broker client disconnected before sending a complete message")
            return
        except (OSError, TimeoutError):
            logging.warning("Broker request ended before a complete message arrived")
            return
        if not raw_request or len(raw_request) > MAX_REQUEST_BYTES or not raw_request.endswith(b"\n"):
            self._write_response({"ok": False, "error": "Requisição inválida ou muito grande."})
            return
        try:
            request = json.loads(
                raw_request.decode("utf-8"),
                object_pairs_hook=self._reject_duplicate_keys,
                parse_constant=self._reject_json_constant,
            )
            if (
                isinstance(request, dict)
                and request.get("protocol") == PROXY_INTERNAL_PROTOCOL
            ):
                response = self._dispatch_proxy_internal(request)
            else:
                response = self.server.broker.dispatch(request)  # type: ignore[attr-defined]
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            response = {"ok": False, "error": str(error)}
        except PermissionError as error:
            response = {"ok": False, "error": str(error)}
        except sqlite3.Error:
            logging.exception("Broker database operation failed")
            response = {"ok": False, "error": "Falha ao processar a operação."}
        except RuntimeError as error:
            logging.exception("Broker cryptographic operation failed")
            response = {"ok": False, "error": str(error)}
        except (OSError, struct.error):
            logging.exception("Broker peer credential validation failed")
            response = {"ok": False, "error": "Falha ao processar a operação."}
        self._write_response(response)

    def _dispatch_proxy_internal(self, request: Any) -> dict[str, Any]:
        if not hasattr(socket, "SO_PEERCRED"):
            raise PermissionError("O protocolo interno exige credenciais Unix do kernel.")
        _peer_pid, peer_uid, _peer_gid = struct.unpack(
            "3i",
            self.request.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            ),
        )
        if peer_uid != os.geteuid():
            raise PermissionError("A operação interna está restrita ao serviço Broker.")
        if set(request) != {"protocol", "method", "linux_uid"}:
            raise ValueError("Campos inválidos para a operação interna.")
        if request["method"] != "identity.resolve":
            raise ValueError("Operação interna não suportada.")
        return self.server.broker.resolve_proxy_identity(  # type: ignore[attr-defined]
            request["linux_uid"]
        )

    @staticmethod
    def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Chave duplicada no protocolo.")
            result[key] = value
        return result

    @staticmethod
    def _reject_json_constant(value: str) -> None:
        raise ValueError(f"Constante JSON inválida: {value}.")

    def _write_response(self, response: dict[str, Any]) -> None:
        try:
            self.wfile.write(
                json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
            )
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            logging.debug("Broker client disconnected before response delivery")


class BrokerServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False
    request_queue_size = BROKER_MAX_CONNECTIONS

    def __init__(self, socket_path: Path, broker: Broker):
        self.broker = broker
        self.connection_slots = threading.BoundedSemaphore(BROKER_MAX_CONNECTIONS)
        self.serve_ready = threading.Event()
        super().__init__(str(socket_path), BrokerRequestHandler)

    def service_actions(self) -> None:
        self.serve_ready.set()

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connection_slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket, client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


def _prepare_socket_path(socket_path: Path) -> None:
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = socket_path.parent.stat()
    if parent_stat.st_uid != os.geteuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise RuntimeError("O diretório do socket deve pertencer ao broker e ter permissão 0700.")
    try:
        socket_stat = socket_path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(socket_stat.st_mode):
        raise RuntimeError("O caminho do socket já existe e não é um socket.")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(socket_path))
    except OSError as error:
        if error.errno == errno.ENOENT:
            return
        if error.errno != errno.ECONNREFUSED:
            raise RuntimeError("Não foi possível verificar o socket existente.") from error
        socket_path.unlink()
    else:
        raise RuntimeError("Já existe um broker ativo nesse socket.")
    finally:
        probe.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Broker local do SIMPLE")
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "database" / "schema.sql",
    )
    parser.add_argument(
        "--initialize-database",
        action="store_true",
        help="cria/atualiza o schema antes de iniciar (faça backup antes de usar em dados existentes)",
    )
    parser.add_argument(
        "--initialize-database-only",
        action="store_true",
        help="cria um banco novo, valida a integridade e encerra sem iniciar o serviço",
    )
    parser.add_argument(
        "--migrate-database-only",
        action="store_true",
        help="faz backup, aplica migrações explícitas, valida e encerra sem iniciar o serviço",
    )
    parser.add_argument(
        "--validate-database-only",
        action="store_true",
        help="valida o schema e a integridade do banco e encerra",
    )
    parser.add_argument(
        "--backup-database",
        type=Path,
        metavar="DESTINO",
        help="cria e valida um backup SQLite sem iniciar o serviço",
    )
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    one_shot_modes = (
        arguments.initialize_database_only,
        arguments.migrate_database_only,
        arguments.validate_database_only,
        arguments.backup_database is not None,
    )
    if sum(one_shot_modes) > 1:
        parser.error("selecione apenas um modo one-shot")
    if any(one_shot_modes) and arguments.initialize_database:
        parser.error("--initialize-database não pode ser combinado com um modo one-shot")
    if arguments.initialize_database_only:
        database_path = Path(os.path.abspath(arguments.database))
        for suffix in SQLITE_AUXILIARY_SUFFIXES:
            auxiliary_path = Path(f"{database_path}{suffix}")
            try:
                auxiliary_path.lstat()
            except FileNotFoundError:
                continue
            raise RuntimeError(
                f"Arquivo auxiliar preexistente impede banco novo: {auxiliary_path.name}."
            )
        backup_path = initialize_database(
            arguments.database, arguments.schema, allow_existing=False
        )
        if backup_path is not None:
            raise RuntimeError("A inicialização de banco novo não deve criar backup.")
        validate_database(arguments.database)
        check_database_integrity(arguments.database)
        logging.info("Banco SQLite inicializado e íntegro.")
        return 0
    if arguments.migrate_database_only:
        _validate_database_files(arguments.database)
        backup_path = initialize_database(arguments.database, arguments.schema)
        if backup_path is None:
            raise RuntimeError("Não foi criado backup antes da migração.")
        validate_database(arguments.database)
        check_database_integrity(arguments.database)
        logging.info("Banco SQLite migrado e íntegro. Backup: %s", backup_path)
        return 0
    if arguments.validate_database_only:
        validate_database(arguments.database)
        check_database_integrity(arguments.database)
        logging.info("Banco SQLite íntegro.")
        return 0
    if arguments.backup_database is not None:
        destination = backup_database(arguments.database, arguments.backup_database)
        logging.info("Backup SQLite validado em %s", destination)
        return 0
    if arguments.socket is None:
        parser.error("--socket é obrigatório ao iniciar o Broker")
    if arguments.initialize_database:
        backup_path = initialize_database(arguments.database, arguments.schema)
        if backup_path is not None:
            logging.info("Backup do banco existente criado em %s", backup_path)
    validate_database(arguments.database)
    _prepare_socket_path(arguments.socket)
    server = BrokerServer(arguments.socket, Broker(arguments.database))
    os.chmod(arguments.socket, 0o600)
    logging.info("Broker SIMPLE iniciado em %s", arguments.socket)
    _serve_until_stopped(server, arguments.socket, "Broker SIMPLE")
    logging.info("Broker SIMPLE encerrado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
