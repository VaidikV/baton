"""Stdlib-only tests for baton.py. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import baton  # noqa: E402

# Strings that must never reach a backend.
SECRETS = ["SECRET-PROMPT-TEXT", "SECRET-REPLY-TEXT", "SECRET-CMD", "secret_dir/secret_file.py",
           "SECRET-TOOL-OUTPUT", "mcp__acme_crm__lookup", "SECRET-SIDECHAIN"]


def rec(kind, content, ts, mid=None, usage=None, **extra):
    msg = {"content": content}
    if mid:
        msg["id"] = mid
    if usage:
        msg["usage"] = usage
    e = {"type": kind, "timestamp": ts, "message": msg}
    e.update(extra)
    return e


def usage(tokens):
    return {"input_tokens": 10, "cache_read_input_tokens": tokens - 10,
            "cache_creation_input_tokens": 0}


def transcript(tokens=260000, todo_status="completed", test_error=False, reply="All done."):
    return [
        rec("user", "first SECRET-PROMPT-TEXT", "2026-01-01T10:00:00.000Z"),
        rec("assistant", [{"type": "text", "text": "old reply"}], "2026-01-01T10:01:00.000Z",
            mid="m1", usage=usage(1000)),
        rec("user", "This session is being continued", "2026-01-01T10:02:00Z",
            isCompactSummary=True),
        rec("user", "SECRET-PROMPT-TEXT: can you fix it?", "2026-01-01T10:03:00.000Z"),
        rec("assistant", [
            {"type": "tool_use", "id": "t1", "name": "Edit",
             "input": {"file_path": "secret_dir/secret_file.py", "old_string": "a"}},
            {"type": "tool_use", "id": "t2", "name": "Bash",
             "input": {"command": "pytest -q SECRET-CMD && git commit -m SECRET-CMD"}},
            {"type": "tool_use", "id": "t3", "name": "mcp__acme_crm__lookup", "input": {}},
            {"type": "tool_use", "id": "t4", "name": "TodoWrite",
             "input": {"todos": [{"content": "SECRET-CMD", "status": todo_status}]}},
        ], "2026-01-01T10:04:00.000Z", mid="m2", usage=usage(tokens - 1000)),
        rec("user", [
            {"type": "tool_result", "tool_use_id": "t1", "content": "SECRET-TOOL-OUTPUT"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "SECRET-TOOL-OUTPUT",
             "is_error": test_error},
        ], "2026-01-01T10:05:00.000Z"),
        rec("assistant", [{"type": "text", "text": "SECRET-SIDECHAIN"}],
            "2026-01-01T10:05:30.000Z", mid="side", isSidechain=True),
        rec("assistant", [{"type": "text", "text": reply}], "2026-01-01T10:45:00.000Z",
            mid="m3", usage=usage(tokens)),
    ]


class Backend:
    """Local mock of a Jev-compatible /v1/systemone endpoint."""

    def __init__(self, response):
        self.response = response
        self.bodies = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                outer.bodies.append(self.rfile.read(n).decode("utf-8"))
                if outer.response == "drop":  # promise a body, then hang up mid-way
                    self.send_response(200)
                    self.send_header("Content-Length", "1000")
                    self.end_headers()
                    self.wfile.write(b'{"answers": ')
                    return
                data = outer.response if isinstance(outer.response, bytes) \
                    else json.dumps(outer.response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/systemone"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def answers(finished, hands_on):
    def c(probs):
        return {"type": "choice", "choice": max(probs, key=probs.get),
                "probabilities": probs, "confidence": max(probs.values())}
    return {"model": "jev-test", "usage": {"input_tokens": 1, "output_tokens": 1}, "answers": {
        "done": c({"finished": finished, "not_finished": round(1 - finished - 0.05, 4),
                   "unclear": 0.05}),
        "shape": c({"hands_on": hands_on, "coordinating": round(1 - hands_on - 0.05, 4),
                    "unclear": 0.05}),
    }}


class BatonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        for k in ("TYPESAFE_API_KEY", "BATON_ENDPOINT", "BATON", "BATON_DEBUG"):
            os.environ.pop(k, None)
        self.backend = None

    def tearDown(self):
        if self.backend:
            self.backend.close()
        self.env.stop()
        self.tmp.cleanup()

    def write_transcript(self, entries):
        path = self.home / "t.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        return path

    def run_hook(self, path, last_message="All done.", session="s1", **hook):
        payload = {"session_id": session, "transcript_path": str(path),
                   "last_assistant_message": last_message}
        payload.update(hook)
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["baton.py"]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                contextlib.redirect_stdout(out):
            baton.main()
        text = out.getvalue().strip()
        return json.loads(text)["systemMessage"] if text else None

    def use_backend(self, response):
        self.backend = Backend(response)
        os.environ["BATON_ENDPOINT"] = self.backend.url
        return self.backend

    def test_request_carries_no_conversation_text(self):
        backend = self.use_backend(answers(0.9, 0.9))
        path = self.write_transcript(transcript())
        self.run_hook(path, last_message="SECRET-REPLY-TEXT. Want me to push it?")
        self.assertEqual(len(backend.bodies), 1)
        body = backend.bodies[0]
        for s in SECRETS:
            self.assertNotIn(s, body)
        state = json.loads(body)["state"]
        self.assertEqual(state["latest_turn"]["tool_calls_by_kind"].get("mcp"), 1)
        self.assertTrue(state["last_reply"]["closing_offers_options"])
        self.assertTrue(state["last_reply"]["closing_asks_question"])

    def test_good_answer_nudges_and_low_score_does_not(self):
        self.use_backend(answers(0.9, 0.9))
        msg = self.run_hook(self.write_transcript(transcript()))
        self.assertRegex(msg, r"model score 0\.8\d \(need 0\.45\)")
        self.backend.response = answers(0.2, 0.9)
        self.assertIsNone(self.run_hook(self.write_transcript(transcript()), session="s2",
                                        last_message="different"))

    def test_malformed_answer_falls_back_to_heuristic(self):
        bad = answers(0.9, 0.9)
        bad["answers"]["done"]["probabilities"]["finished"] = 0.5  # no longer sums to 1
        self.use_backend(bad)
        msg = self.run_hook(self.write_transcript(transcript()))
        self.assertIn("heuristic, backend unavailable", msg)

    def test_non_json_answer_falls_back_to_heuristic(self):
        self.use_backend(b"<html>not json</html>")
        msg = self.run_hook(self.write_transcript(transcript()))
        self.assertIn("heuristic, backend unavailable", msg)

    def test_dropped_connection_falls_back_and_backs_off(self):
        backend = self.use_backend("drop")
        path = self.write_transcript(transcript())
        self.assertIn("heuristic, backend unavailable", self.run_hook(path))
        session = json.loads((self.home / ".claude/state/baton/s1.json").read_text())
        self.assertEqual(session["fails"], 1)
        self.assertGreater(session["backoff_until"], 0)
        self.assertEqual(len(backend.bodies), 1)

    def test_keyless_typesafe_makes_no_network_call(self):
        with mock.patch.object(baton.urllib.request, "urlopen") as urlopen:
            msg = self.run_hook(self.write_transcript(transcript()))
        urlopen.assert_not_called()
        self.assertIn("heuristic, no backend configured", msg)

    def test_below_floor_is_silent(self):
        self.use_backend(answers(0.99, 0.99))
        self.assertIsNone(self.run_hook(self.write_transcript(transcript(tokens=100000))))
        self.assertEqual(self.backend.bodies, [])

    def test_cooldown_and_checkpoint_dedupe(self):
        backend = self.use_backend(answers(0.2, 0.2))
        path = self.write_transcript(transcript())
        self.assertIsNone(self.run_hook(path))
        self.assertIsNone(self.run_hook(path))  # same reply: not re-judged
        self.assertEqual(len(backend.bodies), 1)
        backend.response = answers(0.9, 0.9)
        self.assertIsNotNone(self.run_hook(path, last_message="new reply"))
        self.assertIsNone(self.run_hook(path, last_message="another reply"))  # cooldown
        self.assertEqual(len(backend.bodies), 2)

    def test_heuristic_waits_while_busy_but_not_forever(self):
        busy = transcript(tokens=210000, todo_status="in_progress")
        self.assertIsNone(self.run_hook(self.write_transcript(busy)))
        over = transcript(tokens=260000, todo_status="in_progress")
        self.assertIsNotNone(self.run_hook(self.write_transcript(over), session="s2"))

    def test_features(self):
        features, reply, prompt = baton.scan_transcript(
            self.write_transcript(transcript(test_error=True)))
        turn = features["turn"]
        self.assertEqual(features["compactions"], 1)
        self.assertEqual(features["user_prompts"], 2)
        self.assertEqual(features["assistant_messages"], 3)  # sidechain excluded
        self.assertEqual(features["minutes_elapsed"], 45.0)
        self.assertEqual(features["todos"], {"pending": 0, "in_progress": 0, "completed": 1})
        self.assertEqual(turn["files_edited"], 1)
        self.assertEqual(turn["tool_calls_since_last_edit"], 3)
        self.assertTrue(turn["ran_tests"] and turn["git_commit"])
        self.assertTrue(turn["last_test_run_failed"])
        self.assertEqual(reply, "All done.")
        self.assertTrue(prompt.endswith("can you fix it?"))

    def test_task_tools_count_as_todos(self):
        entries = transcript()
        entries[4]["message"]["content"] += [
            {"type": "tool_use", "id": "a", "name": "TaskCreate", "input": {"subject": "x"}},
            {"type": "tool_use", "id": "b", "name": "TaskCreate", "input": {"subject": "y"}},
            {"type": "tool_use", "id": "c", "name": "TaskUpdate",
             "input": {"taskId": "1", "status": "in_progress"}},
        ]
        features, _, _ = baton.scan_transcript(self.write_transcript(entries))
        self.assertEqual(features["todos"], {"pending": 1, "in_progress": 1, "completed": 1})

    def test_ssl_falls_back_to_system_bundle_without_certifi(self):
        bundle = self.home / "ca.pem"
        bundle.write_text("")
        empty = mock.Mock(cafile=None, capath=None)
        with mock.patch.dict(sys.modules, {"certifi": None}), \
                mock.patch.object(baton.ssl, "get_default_verify_paths", return_value=empty), \
                mock.patch.object(baton.ssl, "create_default_context") as make, \
                mock.patch.object(baton, "SYSTEM_CA_BUNDLES", ("/nonexistent.pem", str(bundle))):
            ctx, source = baton.ssl_context()
            make.assert_called_once_with(cafile=str(bundle))
            self.assertEqual(source, str(bundle))
            with mock.patch.object(baton, "SYSTEM_CA_BUNDLES", ()):
                self.assertEqual(baton.ssl_context(), (None, None))

    def test_ssl_keeps_python_default_when_it_has_certs(self):
        good = mock.Mock(cafile=str(self.home), capath=None)  # any existing path
        with mock.patch.dict(sys.modules, {"certifi": None}), \
                mock.patch.object(baton.ssl, "get_default_verify_paths", return_value=good):
            self.assertEqual(baton.ssl_context(), (None, "python default"))

    def test_threshold_slides(self):
        self.assertEqual(baton.threshold_for_pct(50), 0.85)
        self.assertAlmostEqual(baton.threshold_for_pct(87.5), 0.675)
        self.assertEqual(baton.threshold_for_pct(130), 0.45)


if __name__ == "__main__":
    unittest.main()
