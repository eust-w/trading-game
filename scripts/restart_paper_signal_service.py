#!/usr/bin/env python3
"""Restart the live-confirm paper signal service with a same-day log file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CST = timezone(timedelta(hours=8))
REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_PREFIX = "trading_game_" + hashlib.sha256(str(REPO_ROOT).encode()).hexdigest()[:12]
SERVICE_MARKER = str(REPO_ROOT / "quant/services/paper_signal_service.py")
SERVICE_ARGS = "--confirm-live"
DEFAULT_LOG_PATTERN = "reports/service/logs/paper_signal_service_confirm_live_{date_compact}.log"
DEFAULT_GRACE_SECONDS = 30.0


def now_cst() -> datetime:
    return datetime.now(CST)


def daily_path(pattern: str, base_time: datetime) -> Path:
    return REPO_ROOT / pattern.format(
        date=base_time.strftime("%Y-%m-%d"),
        date_compact=base_time.strftime("%Y%m%d"),
    )


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def matching_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    proc = run(["ps", "-axo", "pid=,ppid=,pgid=,command="])
    matches = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(maxsplit=3)
        if len(parts) < 4:
            continue
        pid, ppid, pgid, command = parts
        if int(pid) == current_pid:
            continue
        if SERVICE_MARKER in command and SERVICE_ARGS in command and "python" in command.lower():
            matches.append({"pid": int(pid), "ppid": int(ppid), "pgid": int(pgid), "command": command})
    return matches


def matching_screen_sessions() -> list[str]:
    proc = run(["screen", "-ls"])
    sessions = []
    for line in proc.stdout.splitlines():
        if SESSION_PREFIX not in line:
            continue
        match = re.search(r"(\d+\.[^\s]+)", line)
        if match:
            sessions.append(match.group(1))
    return sessions


def stop_existing(
    processes: list[dict[str, Any]],
    sessions: list[str],
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> dict[str, Any]:
    stopped: dict[str, Any] = {"screen_sessions": [], "process_groups": [], "killed_pids": []}
    seen_pgids: set[int] = set()
    for process in processes:
        if str(process["command"]).startswith("SCREEN "):
            continue
        pgid = int(process["pgid"])
        if pgid <= 1 or pgid in seen_pgids:
            continue
        seen_pgids.add(pgid)
        try:
            os.killpg(pgid, signal.SIGTERM)
            stopped["process_groups"].append(pgid)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            stopped.setdefault("errors", []).append(f"TERM pgid {pgid}: {exc}")
    deadline = time.time() + max(1.0, grace_seconds)
    while time.time() < deadline and matching_processes():
        time.sleep(0.5)
    for session in sessions:
        if session in matching_screen_sessions():
            run(["screen", "-S", session, "-X", "quit"])
            stopped["screen_sessions"].append(session)
    for process in matching_processes():
        try:
            os.kill(int(process["pid"]), signal.SIGKILL)
            stopped["killed_pids"].append(int(process["pid"]))
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            stopped.setdefault("errors", []).append(f"KILL pid {process['pid']}: {exc}")
    return stopped


def start_service(log_path: Path, base_time: datetime) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    session = f"{SESSION_PREFIX}_{base_time.strftime('%Y%m%d_%H%M%S')}"
    command = (
        f"cd {shlex.quote(str(REPO_ROOT))} && "
        f"exec python3 -u {shlex.quote(SERVICE_MARKER)} --confirm-live "
        f">> {shlex.quote(str(log_path))} 2>&1"
    )
    proc = run(["screen", "-dmS", session, "zsh", "-lc", command])
    if proc.returncode != 0:
        raise SystemExit(f"screen start failed: {proc.stderr.strip() or proc.stdout.strip()}")
    time.sleep(1)
    return {
        "session": session,
        "log_path": str(log_path),
        "command": command,
        "processes": matching_processes(),
        "screen_sessions": matching_screen_sessions(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--enable-live-restart", action="store_true", help="explicitly allow restarting this checkout's legacy live service")
    parser.add_argument("--log-pattern", default=DEFAULT_LOG_PATTERN)
    parser.add_argument(
        "--grace-seconds",
        type=float,
        default=DEFAULT_GRACE_SECONDS,
        help="seconds to wait for the old service to finish its current tick",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.enable_live_restart:
        parser.error("use --dry-run for inspection; live restart requires --enable-live-restart")

    base_time = now_cst()
    log_path = daily_path(args.log_pattern, base_time)
    processes = matching_processes()
    sessions = matching_screen_sessions()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "time": base_time.strftime("%Y-%m-%d %H:%M:%S %z"),
                    "dry_run": True,
                    "matched_processes": processes,
                    "matched_screen_sessions": sessions,
                    "daily_log_path": str(log_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    stopped = stop_existing(processes, sessions, grace_seconds=args.grace_seconds)
    started = start_service(log_path, base_time)
    print(
        json.dumps(
            {
                "time": base_time.strftime("%Y-%m-%d %H:%M:%S %z"),
                "stopped": stopped,
                "started": started,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
