"""Load local application defaults before importing storage or model clients."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(
    Path(__file__).resolve().parents[2] / ".env",
    override=False,
    interpolate=False,
    encoding="utf-8-sig",
)
