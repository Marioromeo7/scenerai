"""Run the API and ARQ worker together in Render's single free web service."""
import os
import signal
import subprocess
import sys
import time


def main() -> int:
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    subprocess.run([sys.executable, "seed_demo.py"], check=True)

    port = os.environ.get("PORT", "10000")
    processes = [
        subprocess.Popen([
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", "0.0.0.0", "--port", port,
        ]),
        subprocess.Popen(["arq", "worker.WorkerSettings"]),
    ]
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        while not stopping:
            for process in processes:
                code = process.poll()
                if code is not None:
                    stop(signal.SIGTERM, None)
                    return code or 1
            time.sleep(0.5)
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
