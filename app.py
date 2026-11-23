"""Vercel's explicit ASGI entrypoint; native development uses the same app."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from resolvelab.api import app
