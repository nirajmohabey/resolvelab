"""Execute the actual backup script against mocked PostgreSQL failure paths."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("cleanup_fault", ["none", "snapshot_close", "database_drop"])
def test_partial_private_dump_is_removed_when_other_cleanup_fails(monkeypatch, tmp_path, cleanup_fault):
    import dotenv
    import psycopg
    import shutil
    import subprocess
    import uuid

    root = Path(__file__).resolve().parents[1]
    # Copy the exact script bytes into an owned empty fixture repository. Its
    # actual top-level execution then owns only this temporary private dump.
    script = tmp_path / "scripts/backup_check.py"
    script.parent.mkdir()
    script.write_bytes((root / "scripts/backup_check.py").read_bytes())
    monkeypatch.syspath_prepend(str(root / "scripts"))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
    for key in ("DATABASE_URL", "ADMIN_DATABASE_URL"):
        monkeypatch.setenv(key, "postgresql+psycopg://fixture:synthetic@127.0.0.1:55439/resolvelab_test")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("BACKUP_CHECK_OUTPUT", str(tmp_path / "reports"))
    monkeypatch.setattr(uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    connections, operations, closed = [], [], []

    class Result:
        def __init__(self, value): self.value = value
        def fetchone(self): return self.value
        def fetchall(self): return self.value
        def __iter__(self): return iter(self.value)

    class Connection:
        def __init__(self, database): self.database = database
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def execute(self, statement):
            value = statement.as_string() if hasattr(statement, "as_string") else str(statement)
            if "current_database()" in value:
                return Result(("resolvelab_test", "127.0.0.1", 55439))
            if "pg_export_snapshot()" in value:
                return Result(("synthetic-exported-snapshot",))
            if value.startswith("CREATE DATABASE"):
                operations.append("create")
            if value.startswith("DROP DATABASE"):
                operations.append("drop")
                if cleanup_fault == "database_drop":
                    raise RuntimeError("Synthetic database drop failure")
            return Result([])
        def close(self):
            closed.append(self.database)
            if cleanup_fault == "snapshot_close":
                raise RuntimeError("Synthetic snapshot close failure")

    def connect(**kwargs):
        connections.append(kwargs["dbname"])
        return Connection(kwargs["dbname"])

    def partial_dump_then_fail(command, **_kwargs):
        assert "-Fc" in command, "Only the mocked dump subprocess may run"
        Path(command[command.index("-f") + 1]).write_bytes(b"synthetic partial dump without private data")
        raise RuntimeError("Synthetic pg_dump failure")

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(subprocess, "run", partial_dump_then_fail)
    with pytest.raises(RuntimeError, match="Synthetic"):
        runpy.run_path(str(script), run_name="__main__")
    assert connections == ["resolvelab_test", "postgres", "postgres"]
    assert operations == ["create", "drop"]
    assert closed == ["resolvelab_test"]
    assert not list((tmp_path / ".runtime").glob("*.dump"))
