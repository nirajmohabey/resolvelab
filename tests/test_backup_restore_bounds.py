"""Restore verification rejects routing overrides before a workflow can write."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("restore_guard_under_test", Path(__file__).resolve().parents[1]/"scripts/backup_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
NAME = "resolvelab_restore_0123456789ab"
URL = "postgresql+psycopg://fixture:synthetic@127.0.0.1:55439/" + NAME


@pytest.mark.parametrize("query", ["dbname=resolvelab", "service=fixture", "hostaddr=127.0.0.2"])
@pytest.mark.parametrize("side", ["runtime", "admin"])
def test_restore_url_cannot_redirect_a_probe_to_another_database(monkeypatch, query, side):
    def forbidden_connection(*args, **kwargs):
        raise AssertionError("Invalid routing must fail before any connection")
    monkeypatch.setattr(guard, "create_engine", forbidden_connection)
    runtime, admin = (URL+"?"+query, URL) if side == "runtime" else (URL, URL+"?"+query)
    with pytest.raises(RuntimeError, match="routing overrides"):
        guard.verify_restore_destination(runtime, admin, NAME, {"database": "resolvelab", "server": "127.0.0.1/32", "port": 55439})


def test_restore_verification_never_accepts_the_serving_database_as_its_owned_target():
    with pytest.raises(RuntimeError, match="newly owned"):
        guard.check_restore_urls(URL, URL, "resolvelab")
