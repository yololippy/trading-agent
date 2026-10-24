#!/usr/bin/env python3
"""Poll Codex quota without model calls; optionally resume one existing thread.

Default: one read-only check. --watch keeps monitoring across quota recoveries.
--continue-thread sends one continuation per observed block/recovery cycle.
Stop with Ctrl-C, SIGTERM, or --stop-file when the overall work is complete.
No launch agent, task checkpoint, or config is installed.

Requires Python 3.10+ and an authenticated Codex CLI on PATH.
Protocol: https://learn.chatgpt.com/docs/app-server
"""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any


class RpcError(RuntimeError):
    def __init__(self, error: dict[str, Any]):
        self.error = error
        super().__init__(str(error.get("message", "RPC failed")))


class NeedsInput(RuntimeError):
    pass


class UnknownDelivery(RuntimeError):
    """A continuation may already have started: do not blindly submit again."""


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                      "event": event, **fields}, ensure_ascii=False), flush=True)


class AppServer:
    """Own one CLI transport process; never change global Codex configuration."""

    def __init__(self, command: list[str], timeout: float):
        self.command = command
        self.timeout = timeout
        self.process: subprocess.Popen | None = None
        self.incoming: queue.Queue = queue.Queue()
        self.completed: deque[dict[str, Any]] = deque(maxlen=64)
        self.next_id = 0
        self.reader: threading.Thread | None = None

    def __enter__(self) -> AppServer:
        try:
            self.process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                bufsize=1, start_new_session=True,
            )
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            self.request("initialize", {
                "clientInfo": {"name": "codex_quota_watch", "version": "1.0.0"}
            })
            self._send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("expected a JSON object")
                    self.incoming.put(message)
                except ValueError:
                    self.incoming.put(RuntimeError("app-server emitted invalid JSON"))
        finally:
            self.incoming.put(EOFError("app-server closed the connection"))

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _receive(self, deadline: float) -> dict[str, Any]:
        try:
            message = self.incoming.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError("app-server response timed out") from None
        if isinstance(message, BaseException):
            raise message
        if "method" in message and "id" in message:
            # Never grant approvals, answer questions, or refresh managed tokens
            # automatically on behalf of a resumed conversation.
            raise NeedsInput(f"server requested user input: {message['method']}")
        return message

    def request(self, method: str, params: dict | None = None) -> dict[str, Any]:
        self.next_id += 1
        ident = self.next_id
        self._send({"id": ident, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while True:
            message = self._receive(deadline)
            if message.get("id") == ident:
                if "error" in message:
                    raise RpcError(message["error"])
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"{method}: invalid response")
                return result
            if message.get("method") == "turn/completed":
                self.completed.append(message.get("params", {}))

    def wait_turn(self, thread_id: str, turn_id: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            while self.completed:
                params = self.completed.popleft()
                turn = params.get("turn", {})
                if params.get("threadId") == thread_id and turn.get("id") == turn_id:
                    return turn
            message = self._receive(deadline)
            if message.get("method") == "turn/completed":
                self.completed.append(message.get("params", {}))

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        # Only signal the new process group created by this script. For proxy
        # transport this closes the proxy, not the shared server.
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                # On macOS a process that has just exited can leave a process
                # group for which killpg reports EPERM until wait reaps it.
                if process.poll() is None:
                    process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                if process.poll() is None:
                    process.kill()
            process.wait(timeout=3)
        if self.reader:
            self.reader.join(timeout=1)
        if process.stdin:
            process.stdin.close()
        if process.stdout:
            process.stdout.close()
        self.process = None


def quota_state(data: dict, limit_id: str) -> tuple[str, str, dict]:
    """Only explicit backend permission establishes recovery (never percentages)."""
    buckets = data.get("rateLimitsByLimitId")
    snapshot = buckets.get(limit_id) if isinstance(buckets, dict) else None
    if snapshot is None and not buckets:
        legacy = data.get("rateLimits")
        if isinstance(legacy, dict) and legacy.get("limitId") in (None, limit_id):
            snapshot = legacy
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    summary = {key: snapshot.get(key) for key in (
        "limitId", "primary", "secondary", "spendControlReached", "rateLimitReachedType"
    )}
    allowed = data.get("ordinaryUsageAllowed")
    summary["ordinaryUsageAllowed"] = allowed
    if allowed is False:
        return "blocked", "ordinary usage denied by backend", summary
    if snapshot.get("spendControlReached") is True or snapshot.get("rateLimitReachedType"):
        return "blocked", "backend reports an active limit", summary
    if allowed is True and snapshot:
        return "available", "ordinary usage permitted by backend", summary
    return "unknown", "explicit usage permission or requested quota bucket unavailable", summary


def is_quota_error(error: dict | None) -> bool:
    if not isinstance(error, dict):
        return False
    info = error.get("codexErrorInfo")
    if info is None and isinstance(error.get("data"), dict):
        info = error["data"].get("codexErrorInfo")
    return info in ("usageLimitExceeded", "rateLimitExceeded")


def thread_busy(thread: dict) -> bool:
    # Do not steer an active turn or answer pending approvals with "继续".
    return thread.get("status", {}).get("type") == "active"


def continue_thread(client: AppServer, thread_id: str, turn_timeout: float) -> str:
    thread = client.request("thread/read", {"threadId": thread_id})["thread"]
    if thread_busy(thread):
        log("thread_busy", thread_id=thread_id)
        return "busy"
    thread = client.request("thread/resume", {
        "threadId": thread_id, "excludeTurns": True,
    })["thread"]
    if thread_busy(thread):
        log("thread_busy", thread_id=thread_id)
        return "busy"
    try:
        result = client.request("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": "继续"}],
        })
    except RpcError as exc:
        if is_quota_error(exc.error):
            log("continue_rate_limited", thread_id=thread_id)
            return "blocked"
        raise
    except NeedsInput:
        raise
    except (TimeoutError, EOFError, OSError, RuntimeError) as exc:
        raise UnknownDelivery("continuation submission outcome unknown; not resending") from exc
    turn = result.get("turn", {})
    turn_id = turn.get("id")
    if not isinstance(turn_id, str) or not turn_id:
        raise UnknownDelivery("continuation acknowledgement missing turn ID; not resending")
    log("continue_accepted", thread_id=thread_id, turn_id=turn_id)
    if turn.get("status") == "inProgress":
        try:
            turn = client.wait_turn(thread_id, turn_id, turn_timeout)
        except NeedsInput:
            raise
        except (TimeoutError, EOFError, OSError, RuntimeError) as exc:
            raise UnknownDelivery(
                f"continuation outcome unknown for turn {turn_id}; not resending"
            ) from exc
    error = turn.get("error")
    status = turn.get("status")
    log("continue_finished", thread_id=thread_id, turn_id=turn_id,
        status=status, error_code=error.get("codexErrorInfo") if isinstance(error, dict) else None)
    if status == "completed":
        return "completed"
    if status == "failed" and is_quota_error(error):
        return "blocked"
    raise RuntimeError(f"continuation stopped with status {status}; manual inspection required")


def stop_requested(args: argparse.Namespace, emit: Any) -> bool:
    if args.stop_file and Path(args.stop_file).exists():
        emit("stopped", reason="stop file present", stop_file=str(args.stop_file))
        return True
    return False


def wait_interval(args: argparse.Namespace, emit: Any, sleep: Any) -> bool:
    """Allow an external completion signal to stop a long polling wait promptly."""
    remaining = args.interval
    while remaining > 0:
        if stop_requested(args, emit):
            return True
        step = min(1.0, remaining) if args.stop_file else remaining
        sleep(step)
        remaining -= step
    return stop_requested(args, emit)


def run(args: argparse.Namespace, client_factory: Any = AppServer,
        emit: Any = log, sleep: Any = time.sleep) -> int:
    command = [args.codex, "app-server"]
    command += ["proxy", "--sock", args.server_socket] if args.server_socket else ["--stdio"]
    armed = False
    resume_paused = False
    cycles = 0
    for attempt in range(1, (args.max_checks or sys.maxsize) + 1):
        if stop_requested(args, emit):
            return 0
        continuation_attempted = False
        try:
            with client_factory(command, args.timeout) as client:
                data = client.request("account/rateLimits/read")
                state, reason, summary = quota_state(data, args.limit_id)
                emit("quota_check", attempt=attempt, state=state, reason=reason, **summary)
                if not args.watch:
                    return {"available": 0, "blocked": 2, "unknown": 3}[state]
                if state == "blocked":
                    armed = True
                elif state == "available" and armed and not resume_paused:
                    if stop_requested(args, emit):
                        return 0
                    if not args.continue_thread:
                        emit("quota_recovered", attempt=attempt, action="read_only")
                        armed = False
                    else:
                        continuation_attempted = True
                        try:
                            outcome = continue_thread(client, args.continue_thread, args.turn_timeout)
                        except UnknownDelivery as exc:
                            emit("continue_unknown", attempt=attempt, error=str(exc))
                            resume_paused = True
                        except NeedsInput as exc:
                            emit("needs_user_input", attempt=attempt, error=str(exc))
                            resume_paused = True
                        except (RpcError, RuntimeError) as exc:
                            emit("continue_error", attempt=attempt, error=str(exc))
                            resume_paused = True
                        else:
                            if outcome == "completed":
                                cycles += 1
                                armed = False
                                emit("recovery_complete", attempt=attempt, cycle=cycles,
                                     monitoring="continues")
                            # A quota error or a busy target keeps the monitor armed.
                        if resume_paused:
                            emit("resume_paused", attempt=attempt, monitoring="continues",
                                 reason="inspect the prior result before restarting automatic continuation")
                        continuation_attempted = False
        except NeedsInput as exc:
            emit("needs_user_input", attempt=attempt, error=str(exc))
            if not args.watch:
                return 5
            resume_paused = True
            emit("resume_paused", attempt=attempt, monitoring="continues")
        except (OSError, TimeoutError, EOFError, RuntimeError, KeyError, ValueError) as exc:
            emit("check_error", attempt=attempt, error_type=type(exc).__name__, error=str(exc))
            if continuation_attempted:
                # Transport cleanup must not turn a completed or uncertain
                # submission into another attempt to send the same message.
                emit("continue_unknown", attempt=attempt, error="transport failed after continuation; not resending")
                resume_paused = True
                emit("resume_paused", attempt=attempt, monitoring="continues")
            if not args.watch:
                return 1
        if stop_requested(args, emit):
            return 0
        if args.max_checks and attempt == args.max_checks:
            emit("check_limit_reached", attempts=attempt)
            return 3
        emit("waiting", seconds=args.interval)
        if wait_interval(args, emit, sleep):
            return 0
    return 3


@contextmanager
def monitor_lock(thread_id: str):
    """Prevent two monitors for the same thread; holds no business progress."""
    home = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    digest = hashlib.sha256(f"{home}:{thread_id}".encode()).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / f"codex-quota-watch-{os.getuid()}-{digest}.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another quota monitor already owns this thread") from None
        yield


def positive_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--watch", action="store_true", help="keep monitoring across quota recoveries until explicitly stopped")
    parser.add_argument("--continue-thread", metavar="THREAD_ID", help="send 继续 once per block/recovery cycle, then keep monitoring")
    parser.add_argument("--stop-file", metavar="PATH", help="stop gracefully when this file exists; create it when all work is complete")
    parser.add_argument("--interval", type=positive_number, default=900, help="seconds between attempts (default: 900)")
    parser.add_argument("--timeout", type=positive_number, default=30, help="timeout for each protocol request (default: 30)")
    parser.add_argument("--turn-timeout", type=positive_number, default=3600, help="seconds to wait for a continued turn (default: 3600)")
    parser.add_argument("--max-checks", type=positive_int, help="stop after this many checks; otherwise unlimited in watch mode")
    parser.add_argument("--limit-id", default="codex", help="quota bucket to inspect (default: codex)")
    parser.add_argument("--codex", default="codex", help="Codex CLI executable (default: codex on PATH)")
    parser.add_argument("--server-socket", help="use an existing app-server Unix control socket instead of a separate stdio server")
    args = parser.parse_args(argv)
    if args.continue_thread and not args.watch:
        parser.error("--continue-thread requires --watch")
    if args.stop_file is not None:
        if not args.watch:
            parser.error("--stop-file requires --watch")
        if not args.stop_file.strip():
            parser.error("--stop-file cannot be empty")
        args.stop_file = str(Path(args.stop_file).expanduser().resolve())
        if Path(args.stop_file).is_dir():
            parser.error("--stop-file must be a file path, not a directory")
    if args.continue_thread is not None and not args.continue_thread.strip():
        parser.error("--continue-thread cannot be empty")
    if not args.limit_id.strip():
        parser.error("--limit-id cannot be empty")
    executable = shutil.which(args.codex)
    if executable is None:
        parser.error(f"Codex CLI not found: {args.codex}")
    args.codex = executable
    return args


def main() -> int:
    args = parse_args()
    def handle_stop_signal(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, handle_stop_signal)
    try:
        if args.continue_thread:
            with monitor_lock(args.continue_thread):
                return run(args)
        return run(args)
    except KeyboardInterrupt:
        log("stopped", reason="keyboard interrupt")
        return 130
    except (OSError, RuntimeError) as exc:
        log("fatal_error", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
