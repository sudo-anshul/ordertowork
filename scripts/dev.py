#!/usr/bin/env python3
"""Start the local API, worker and interface; keep PostgreSQL data between runs."""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command):
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    os.chdir(ROOT)
    for executable in ("uv", "npm", "docker"):
        if shutil.which(executable) is None:
            sys.exit(f"Install {executable} before starting OrderToWork.")
    if not (ROOT / ".env").exists():
        shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        print("Created .env for explicitly local reference-mode development.", flush=True)
    run("uv", "sync", "--locked")
    if not (ROOT / "frontend/node_modules").is_dir():
        run("npm", "--prefix", "frontend", "ci")
    run("docker", "compose", "up", "--detach", "--wait", "db")
    run("uv", "run", "alembic", "upgrade", "head")
    commands = [
        ["uv", "run", "uvicorn", "ordertowork.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload", "--no-access-log"],
        ["uv", "run", "python", "-m", "ordertowork.worker"],
        ["npm", "--prefix", "frontend", "run", "dev"],
    ]
    processes = []
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=ROOT, start_new_session=True))
        print("OrderToWork: http://localhost:5173 — Ctrl+C stops the application; database data is retained.", flush=True)
        while True:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f"An application process exited with code {process.returncode}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
