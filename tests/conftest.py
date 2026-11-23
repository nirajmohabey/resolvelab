"""Database tests require the disposable local PostgreSQL instance."""
from pathlib import Path
import os

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# A direct `pytest` invocation must fail before autouse seeding can touch the
# serving .env database. The wrapper sets these two variables before import.
from resolvelab.isolation import validate_disposable_urls
validate_disposable_urls(os.environ.get("DATABASE_URL"),os.environ.get("ADMIN_DATABASE_URL"),"resolvelab_test")


@pytest.fixture(autouse=True)
def seeded_database():
    from resolvelab.seed import seed
    seed(reset=True)


@pytest.fixture
def actor():
    from resolvelab.auth import fixture_principal
    return fixture_principal("alpha-operator")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from resolvelab.api import app
    with TestClient(app) as test_client:
        yield test_client
