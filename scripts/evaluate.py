"""Run workflow evaluation only in the dedicated disposable evaluation database."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
load_dotenv(root / ".env")
for runtime_key, evaluation_key in (("DATABASE_URL", "EVALUATION_DATABASE_URL"), ("ADMIN_DATABASE_URL", "ADMIN_EVALUATION_DATABASE_URL")):
    value = os.environ.get(evaluation_key)
    if not value or make_url(value).database != "resolvelab_evaluation":
        raise SystemExit("Evaluation resets fixtures and requires the dedicated resolvelab_evaluation database configuration.")
    os.environ[runtime_key] = value
os.environ["APP_ENV"] = "local"

from resolvelab.evaluate import main  # noqa: E402 - configure isolation before database imports

if __name__ == "__main__":
    raise SystemExit(main())
