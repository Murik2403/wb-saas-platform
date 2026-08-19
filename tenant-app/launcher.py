from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    worker = subprocess.Popen([sys.executable, str(ROOT / "sync.py"), "--loop"], cwd=ROOT)
    try:
        dashboard = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(ROOT / "app.py"),
                "--server.address=127.0.0.1",
                "--server.port=8501",
            ],
            cwd=ROOT,
        )
        dashboard.wait()
    finally:
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
