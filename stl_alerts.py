"""Entry point: `python stl_alerts.py [--dry-run] [--fixture F] [--test-notify]`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from stlalerts.main import cli  # noqa: E402

if __name__ == "__main__":
    cli()
