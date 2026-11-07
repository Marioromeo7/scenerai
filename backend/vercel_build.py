"""Apply the schema and seed the prefab-backed portfolio scenario at build time."""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run_build() -> None:
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be configured in Vercel before deploying.")
    migration_env = os.environ.copy()
    # Neon app traffic should use the pooled URL. Alembic needs a session-safe
    # direct connection because PgBouncer transaction pooling is incompatible
    # with some migration operations. Fall back for other Postgres providers.
    migration_env["DATABASE_URL"] = os.getenv("DATABASE_URL_UNPOOLED") or os.environ["DATABASE_URL"]
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, check=True, env=migration_env,
    )
    subprocess.run([sys.executable, "seed_demo.py"], cwd=ROOT, check=True, env=os.environ.copy())


def main() -> None:
    run_build()


if __name__ == "__main__":
    main()
