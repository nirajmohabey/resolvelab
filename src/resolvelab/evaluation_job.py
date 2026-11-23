"""Independent evaluation mutex survives a parent worker's unexpected death."""
import sys
from sqlalchemy import text
from .db import engine


def main(argv=None):
    from .evaluate import main as evaluate
    with engine.connect() as coordinator:
        coordinator.execute(text("SELECT pg_advisory_lock(hashtext('resolvelab-isolated-evaluator'))"))
        coordinator.commit()
        try:
            return evaluate(argv)
        finally:
            coordinator.execute(text("SELECT pg_advisory_unlock(hashtext('resolvelab-isolated-evaluator'))"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
