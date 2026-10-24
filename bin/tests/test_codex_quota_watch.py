"""Fault tests use fake Codex transports; never send messages to real threads."""

import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "codex-quota-watch.py"
spec = importlib.util.spec_from_file_location("codex_quota_watch", SCRIPT)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def snapshot(allowed=True, **changes):
    bucket = {"limitId": "codex", "primary": {"usedPercent": 25, "resetsAt": 1},
              "secondary": {"usedPercent": 15}, "spendControlReached": False,
              "rateLimitReachedType": None}
    bucket.update(changes)
    return {"ordinaryUsageAllowed": allowed, "rateLimits": bucket,
            "rateLimitsByLimitId": {"codex": bucket}}


def options(**changes):
    result = dict(codex="fake-codex", server_socket=None, timeout=1,
                  turn_timeout=1, watch=True, interval=0.001, max_checks=6,
                  limit_id="codex", continue_thread="test-main", stop_file=None)
    result.update(changes)
    return argparse.Namespace(**result)


class FakeClient:
    def __init__(self, data, outcome="completed", busy=False):
        self.data = data
        self.outcome = outcome
        self.busy = busy
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def request(self, method, params=None):
        self.calls.append((method, params))
        if method == "account/rateLimits/read":
            if isinstance(self.data, Exception):
                raise self.data
            return self.data
        if method in ("thread/read", "thread/resume"):
            return {"thread": {"status": {"type": "active" if self.busy else "idle"}}}
        if method == "turn/start":
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return {"turn": {"id": "test-turn", "status": "inProgress"}}
        raise AssertionError(method)

    def wait_turn(self, *args):
        if self.outcome == "quota":
            return {"id": "test-turn", "status": "failed",
                    "error": {"codexErrorInfo": "usageLimitExceeded"}}
        return {"id": "test-turn", "status": self.outcome}


class QuotaTests(unittest.TestCase):
    def test_requires_explicit_recovery_not_reset_time(self):
        data = snapshot(None, primary={"usedPercent": 0, "resetsAt": 1})
        self.assertEqual(watch.quota_state(data, "codex")[0], "unknown")

    def test_backend_permission_is_authoritative_not_rounded_percentages(self):
        self.assertEqual(watch.quota_state(snapshot(True, primary={"usedPercent": 100}), "codex")[0], "available")
        self.assertEqual(watch.quota_state(snapshot(False), "codex")[0], "blocked")

    def test_spend_control_or_active_limit_blocks_even_with_permission(self):
        for changes in [{"spendControlReached": True},
                        {"rateLimitReachedType": "rate_limit_reached"}]:
            with self.subTest(changes=changes):
                self.assertEqual(watch.quota_state(snapshot(True, **changes), "codex")[0], "blocked")

    def test_missing_data_and_other_bucket_do_not_mean_available(self):
        for data in [{}, {"ordinaryUsageAllowed": True},
                     {"ordinaryUsageAllowed": True, "rateLimits": {"limitId": "other"}}]:
            with self.subTest(data=data):
                self.assertEqual(watch.quota_state(data, "codex")[0], "unknown")

    def test_prefers_requested_bucket_over_legacy(self):
        data = snapshot(True)
        data["rateLimitsByLimitId"]["codex"] = {"rateLimitReachedType": "rate_limit_reached"}
        self.assertEqual(watch.quota_state(data, "codex")[0], "blocked")

    def test_another_bucket_cannot_be_used_to_infer_recovery(self):
        data = snapshot(True)
        data["rateLimitsByLimitId"] = {"other": {"limitId": "other"}}
        self.assertEqual(watch.quota_state(data, "codex")[0], "unknown")

    def test_string_or_integer_permission_is_not_boolean_permission(self):
        for allowed in ["true", 1, 0]:
            self.assertEqual(watch.quota_state(snapshot(allowed), "codex")[0], "unknown")


class MonitorTests(unittest.TestCase):
    def exercise(self, clients, stop_on_recovery=True, **changes):
        events = []
        sleeps = []
        remaining = iter(clients)
        changes.setdefault("max_checks", len(clients))
        with tempfile.TemporaryDirectory() as folder:
            stop = Path(folder) / "stop"
            changes.setdefault("stop_file", str(stop))
            def emit(event, **fields):
                events.append((event, fields))
                if stop_on_recovery and event in ("recovery_complete", "quota_recovered"):
                    stop.touch()
            with patch.object(watch, "log", side_effect=emit):
                code = watch.run(options(**changes), lambda *args: next(remaining), emit, sleeps.append)
        return code, events, sleeps

    def test_blocked_then_recovered_submits_exactly_one_continue(self):
        clients = [FakeClient(snapshot(False)), FakeClient(snapshot(False)), FakeClient(snapshot(True))]
        code, events, sleeps = self.exercise(clients)
        self.assertEqual(code, 0)
        self.assertEqual(len(sleeps), 2)
        calls = [params for c in clients for method, params in c.calls if method == "turn/start"]
        self.assertEqual(calls, [{"threadId": "test-main", "input": [{"type": "text", "text": "继续"}]}])
        self.assertIn("recovery_complete", [event for event, _ in events])
        self.assertEqual(events[-1][0], "stopped")

    def test_two_recovery_cycles_keep_monitoring_without_duplicate_continues(self):
        clients = [FakeClient(snapshot(allowed)) for allowed in [False, True, True, False, True, True]]
        code, events, sleeps = self.exercise(clients, stop_on_recovery=False)
        self.assertEqual(code, 3)  # Explicit max-checks is the test's stop condition.
        self.assertEqual(len(sleeps), 5)
        self.assertEqual(sum(m == "turn/start" for c in clients for m, _ in c.calls), 2)
        recoveries = [fields for event, fields in events if event == "recovery_complete"]
        self.assertEqual([fields["cycle"] for fields in recoveries], [1, 2])
        self.assertTrue(all(fields["monitoring"] == "continues" for fields in recoveries))

    def test_read_only_watch_also_survives_multiple_recoveries(self):
        clients = [FakeClient(snapshot(allowed)) for allowed in [False, True, True, False, True]]
        code, events, _ = self.exercise(clients, stop_on_recovery=False, continue_thread=None)
        self.assertEqual(code, 3)
        self.assertEqual(sum(event == "quota_recovered" for event, _ in events), 2)
        self.assertFalse(any(m == "turn/start" for c in clients for m, _ in c.calls))

    def test_initially_available_never_sends(self):
        clients = [FakeClient(snapshot(True)), FakeClient(snapshot(True))]
        code, _, _ = self.exercise(clients, max_checks=2)
        self.assertEqual(code, 3)
        self.assertFalse(any(m.startswith("thread/") or m == "turn/start" for c in clients for m, _ in c.calls))

    def test_network_timeout_does_not_arm_recovery(self):
        clients = [FakeClient(TimeoutError("offline")), FakeClient(snapshot(True))]
        code, events, _ = self.exercise(clients, max_checks=2)
        self.assertEqual(code, 3)
        self.assertEqual(events[0][0], "check_error")
        self.assertFalse(any(m == "turn/start" for c in clients for m, _ in c.calls))

    def test_unknown_data_and_network_failure_do_not_erase_observed_block(self):
        clients = [FakeClient(snapshot(False)), FakeClient({}),
                   FakeClient(EOFError("offline")), FakeClient(snapshot(True))]
        code, _, sleeps = self.exercise(clients)
        self.assertEqual(code, 0)
        self.assertEqual(len(sleeps), 3)

    def test_read_only_watch_reports_recovery_without_opening_thread(self):
        clients = [FakeClient(snapshot(False)), FakeClient(snapshot(True))]
        code, events, _ = self.exercise(clients, continue_thread=None)
        self.assertEqual(code, 0)
        self.assertIn("quota_recovered", [event for event, _ in events])
        self.assertEqual(events[-1][0], "stopped")
        self.assertFalse(any(m.startswith("thread/") for c in clients for m, _ in c.calls))

    def test_actual_turn_rate_limit_retries_until_completed(self):
        clients = [FakeClient(snapshot(False)), FakeClient(snapshot(True), outcome="quota"),
                   FakeClient(snapshot(True))]
        code, _, _ = self.exercise(clients)
        self.assertEqual(code, 0)
        self.assertEqual(sum(m == "turn/start" for c in clients for m, _ in c.calls), 2)

    def test_busy_thread_waits_instead_of_steering(self):
        busy = FakeClient(snapshot(True), busy=True)
        code, _, _ = self.exercise([FakeClient(snapshot(False)), busy, FakeClient(snapshot(True))])
        self.assertEqual(code, 0)
        self.assertFalse(any(m == "turn/start" for m, _ in busy.calls))

    def test_unknown_submission_pauses_sending_but_keeps_checking(self):
        bad = FakeClient(snapshot(True), outcome=TimeoutError("lost ack"))
        later = [FakeClient(snapshot(False)), FakeClient(snapshot(True))]
        clients = [FakeClient(snapshot(False)), bad, *later]
        code, events, sleeps = self.exercise(clients)
        self.assertEqual(code, 3)
        self.assertIn("continue_unknown", [event for event, _ in events])
        self.assertIn("resume_paused", [event for event, _ in events])
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(sum(m == "turn/start" for c in clients for m, _ in c.calls), 1)
        self.assertTrue(all(c.calls for c in later))

    def test_unknown_turn_result_pauses_without_resending(self):
        bad = FakeClient(snapshot(True))
        bad.wait_turn = lambda *args: (_ for _ in ()).throw(EOFError("disconnected"))
        code, events, _ = self.exercise([FakeClient(snapshot(False)), bad])
        self.assertEqual(code, 3)
        self.assertIn("continue_unknown", [event for event, _ in events])
        self.assertIn("resume_paused", [event for event, _ in events])

    def test_cleanup_error_after_submission_does_not_cause_duplicate(self):
        class CleanupFailure(FakeClient):
            def __exit__(self, *exc):
                raise OSError("failed transport cleanup")
        client = CleanupFailure(snapshot(True))
        later = FakeClient(snapshot(True))
        code, events, _ = self.exercise([FakeClient(snapshot(False)), client, later], stop_on_recovery=False)
        self.assertEqual(code, 3)
        self.assertIn("recovery_complete", [event for event, _ in events])
        self.assertIn("check_error", [event for event, _ in events])
        self.assertEqual(sum(m == "turn/start" for m, _ in client.calls), 1)
        self.assertFalse(any(m == "turn/start" for m, _ in later.calls))

    def test_explicit_quota_error_in_submission_can_be_retried(self):
        bad = FakeClient(snapshot(True), outcome=watch.RpcError({
            "code": -32000, "message": "limited",
            "data": {"codexErrorInfo": "usageLimitExceeded"},
        }))
        code, _, _ = self.exercise([FakeClient(snapshot(False)), bad, FakeClient(snapshot(True))])
        self.assertEqual(code, 0)

    def test_non_quota_failure_pauses_sending_but_keeps_checking(self):
        code, events, _ = self.exercise([FakeClient(snapshot(False)), FakeClient(snapshot(True), outcome="failed")])
        self.assertEqual(code, 3)
        self.assertIn("continue_error", [event for event, _ in events])
        self.assertIn("resume_paused", [event for event, _ in events])

    def test_approval_is_not_automatically_granted(self):
        bad = FakeClient(snapshot(True))
        bad.wait_turn = lambda *args: (_ for _ in ()).throw(watch.NeedsInput("approval"))
        code, events, _ = self.exercise([FakeClient(snapshot(False)), bad])
        self.assertEqual(code, 3)
        self.assertIn("needs_user_input", [event for event, _ in events])
        self.assertIn("resume_paused", [event for event, _ in events])

    def test_existing_stop_file_exits_without_any_quota_request(self):
        with tempfile.TemporaryDirectory() as folder:
            stop = Path(folder) / "stop"
            stop.touch()
            client = FakeClient(snapshot(True))
            code, events, sleeps = self.exercise([client], stop_file=str(stop))
            self.assertEqual(code, 0)
            self.assertEqual(client.calls, [])
            self.assertEqual(events[-1][0], "stopped")
            self.assertEqual(sleeps, [])

    def test_stop_during_wait_does_not_wait_for_the_full_interval(self):
        with tempfile.TemporaryDirectory() as folder:
            stop = Path(folder) / "stop"
            calls = []
            events = []
            def sleep(seconds):
                calls.append(seconds)
                stop.touch()
            result = watch.wait_interval(options(interval=900, stop_file=str(stop)),
                                         lambda event, **fields: events.append(event), sleep)
            self.assertIs(result, True)
            self.assertEqual(calls, [1.0])
            self.assertEqual(events, ["stopped"])

    def test_stop_arriving_during_quota_read_prevents_continuation(self):
        with tempfile.TemporaryDirectory() as folder:
            stop = Path(folder) / "stop"
            class Finished(FakeClient):
                def request(self, method, params=None):
                    if method == "account/rateLimits/read":
                        stop.touch()
                    return super().request(method, params)
            client = Finished(snapshot(True))
            code, _, _ = self.exercise([FakeClient(snapshot(False)), client], stop_file=str(stop))
            self.assertEqual(code, 0)
            self.assertFalse(any(m == "turn/start" for m, _ in client.calls))

    def test_one_shot_exit_codes(self):
        for data, expected in [(snapshot(True), 0), (snapshot(False), 2), ({}, 3), (TimeoutError(), 1)]:
            with self.subTest(expected=expected):
                code, events, sleeps = self.exercise([FakeClient(data)], watch=False, continue_thread=None)
                self.assertEqual(code, expected)
                self.assertEqual(len(events), 1)
                self.assertEqual(sleeps, [])


FAKE_SERVER = r'''
import json, sys, time
mode = sys.argv[1]
initialized = False
for line in sys.stdin:
    msg = json.loads(line)
    method = msg['method']
    if method == 'initialized':
        initialized = True
        continue
    if method == 'initialize':
        result = {'userAgent': 'fake'}
    elif method == 'account/rateLimits/read':
        if not initialized:
            raise RuntimeError('missing initialized notification')
        if mode == 'timeout':
            time.sleep(5)
        elif mode == 'eof':
            sys.exit(0)
        elif mode == 'malformed':
            print('not JSON', flush=True)
            continue
        elif mode == 'needs-input':
            print(json.dumps({'id': 100, 'method': 'item/tool/requestUserInput'}), flush=True)
            continue
        result = {'ordinaryUsageAllowed': True, 'rateLimits': {'limitId': 'codex'}}
    elif method == 'turn/start':
        print(json.dumps({'method': 'turn/completed', 'params': {
            'threadId': 'test-main', 'turn': {'id': 'test-turn', 'status': 'completed'}
        }}), flush=True)
        result = {'turn': {'id': 'test-turn', 'status': 'inProgress'}}
    else:
        result = {}
    print(json.dumps({'id': msg['id'], 'result': result}), flush=True)
'''


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "fake_server.py"
        self.path.write_text(FAKE_SERVER)

    def tearDown(self):
        self.temp.cleanup()

    def client(self, mode="happy", timeout=2):
        return watch.AppServer([sys.executable, str(self.path), mode], timeout)

    def test_real_subprocess_handshake_and_read_only_query(self):
        with self.client() as client:
            self.assertIs(client.request("account/rateLimits/read")["ordinaryUsageAllowed"], True)

    def test_completion_before_ack_is_retained(self):
        with self.client() as client:
            client.request("turn/start", {"threadId": "test-main"})
            turn = client.wait_turn("test-main", "test-turn", 1)
            self.assertEqual(turn["status"], "completed")

    def test_transport_timeout_eof_and_invalid_json_are_distinct(self):
        for mode, error in [("timeout", TimeoutError), ("eof", EOFError), ("malformed", RuntimeError)]:
            with self.subTest(mode=mode):
                with self.client(mode) as client:
                    client.timeout = 0.05
                    with self.assertRaises(error):
                        client.request("account/rateLimits/read")

    def test_server_request_is_reported_as_needing_input(self):
        with self.client("needs-input") as client:
            with self.assertRaises(watch.NeedsInput):
                client.request("account/rateLimits/read")

    def test_full_cli_recovery_with_fake_server_never_calls_real_codex(self):
        fixture = Path(self.temp.name)
        executable = fixture / "fake-codex"
        executable.write_text("#!" + sys.executable + "\n" + r'''
import json, os
from pathlib import Path
import sys
root = Path(os.environ['QUOTA_WATCH_FIXTURE_DIR'])
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'initialized':
        continue
    if method == 'initialize':
        result = {}
    elif method == 'account/rateLimits/read':
        count_file = root / 'checks'
        count = int(count_file.read_text()) + 1 if count_file.exists() else 1
        count_file.write_text(str(count))
        result = {'ordinaryUsageAllowed': count not in (1, 4), 'rateLimits': {'limitId': 'codex'}}
    elif method in ['thread/read', 'thread/resume']:
        result = {'thread': {'status': {'type': 'idle'}}}
    elif method == 'turn/start':
        with (root / 'sent').open('a') as output:
            output.write(json.dumps(request['params']) + '\n')
        if len((root / 'sent').read_text().splitlines()) == 2:
            (root / 'stop').touch()
        print(json.dumps({'method': 'turn/completed', 'params': {
            'threadId': 'test-main', 'turn': {'id': 'test-turn', 'status': 'completed'}
        }}), flush=True)
        result = {'turn': {'id': 'test-turn', 'status': 'inProgress'}}
    else:
        raise RuntimeError(method)
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
''')
        executable.chmod(0o755)
        env = dict(os.environ, QUOTA_WATCH_FIXTURE_DIR=str(fixture))
        result = subprocess.run([
            sys.executable, str(SCRIPT), "--codex", str(executable),
            "--watch", "--continue-thread", "test-main", "--interval", "0.01", "--max-checks", "8",
            "--stop-file", str(fixture / "stop"),
        ], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(events[-1]["event"], "stopped")
        self.assertEqual(sum(event["event"] == "recovery_complete" for event in events), 2)
        self.assertEqual((fixture / "checks").read_text(), "5")
        sent = [json.loads(line) for line in (fixture / "sent").read_text().splitlines()]
        self.assertEqual(sent, [{"threadId": "test-main", "input": [{"type": "text", "text": "继续"}]}] * 2)

    def test_command_line_help_runs_without_codex_auth(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--continue-thread", result.stdout)


class ArgumentsAndLockTests(unittest.TestCase):
    def test_invalid_intervals_and_continue_without_watch_are_rejected(self):
        with patch.object(watch.shutil, "which", return_value="/fake/codex"):
            for argv in [["--interval", "0"], ["--timeout", "nan"],
                         ["--max-checks", "0"], ["--continue-thread", "test-main"],
                         ["--stop-file", "/tmp/stop"], ["--watch", "--stop-file", ""]]:
                with self.subTest(argv=argv), redirect_stdout(io.StringIO()), patch("sys.stderr", new=io.StringIO()):
                    with self.assertRaises(SystemExit):
                        watch.parse_args(argv)

    def test_second_monitor_for_same_thread_is_rejected(self):
        with patch.object(watch.tempfile, "gettempdir", return_value=tempfile.gettempdir()):
            with watch.monitor_lock("test-thread-lock"):
                with self.assertRaises(RuntimeError):
                    with watch.monitor_lock("test-thread-lock"):
                        self.fail("second monitor acquired the lock")


if __name__ == "__main__":
    unittest.main()
