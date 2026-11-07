"""Apply the schema and seed the prefab-backed portfolio scenario at build time."""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run_build() -> None:
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be configured in Vercel before deploying.")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "seed_demo.py"], cwd=ROOT, check=True)


def main() -> None:
    run_build()


if __name__ == "__main__":
    main()
