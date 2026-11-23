"""Private native Vercel Queue entrypoint; importing it needs no database."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from vercel.queue import subscribe


@subscribe(topic="resolvelab-evaluations", consumer_group="resolvelab-evaluator-v1",
           retry_after=60, max_concurrency=1, max_attempts=10)
async def evaluate(message: dict[str, str]) -> None:
    from starlette.concurrency import run_in_threadpool
    from resolvelab.queue_jobs import consume
    await run_in_threadpool(consume, message)
