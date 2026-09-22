import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import reddit_client as app


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.config = Path(self.temp.name) / "config"
        self.patches = [mock.patch.object(app, "DATA_DIR", self.data),
                        mock.patch.object(app, "CONFIG_DIR", self.config)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_live_calls_require_approval_before_http(self):
        with mock.patch.object(app, "token_request") as token:
            with self.assertRaises(app.ClientError):
                app.Reddit()
            token.assert_not_called()

    def test_draft_is_private_and_rejects_invalid_parent(self):
        with self.assertRaises(app.ClientError):
            app.draft("bad", "text")
        with contextlib.redirect_stdout(io.StringIO()):
            app.draft("t3_abc123", "Hello")
        state = app.read_json(self.data / "state.json")
        self.assertEqual(next(iter(state["drafts"].values()))["text"], "Hello")
        self.assertEqual((self.data / "state.json").stat().st_mode & 0o777, 0o600)

    def test_send_is_one_attempt_even_if_response_is_uncertain(self):
        with contextlib.redirect_stdout(io.StringIO()):
            app.draft("t1_abc123", "Hello")
        draft_id = next(iter(app.read_json(self.data / "state.json")["drafts"]))
        client = mock.Mock()
        client.request.side_effect = [{"name": "tester"}, app.ClientError("connection lost")]
        with mock.patch("builtins.input", return_value=f"SEND {draft_id}"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(app.ClientError):
                app.send(client, draft_id)
        self.assertEqual(app.read_json(self.data / "state.json")["drafts"][draft_id]["status"], "pending")
        with self.assertRaises(app.ClientError):
            app.send(client, draft_id)
        self.assertEqual(client.request.call_count, 2)

    def test_inbox_deduplicates_without_marking_read(self):
        client = mock.Mock()
        client.request.return_value = {"data": {"children": [{"data": {"name": "t1_one", "body": "Hi"}}], "after": None}}
        with contextlib.redirect_stdout(io.StringIO()) as output:
            app.inbox(client, notify=True)
            app.inbox(client, notify=True)
        self.assertIn("1 new inbox item(s)", output.getvalue())
        self.assertIn("0 new inbox item(s)", output.getvalue())
        self.assertEqual(app.read_json(self.data / "state.json")["processed_inbox_ids"], ["t1_one"])
        self.assertEqual(client.request.call_args.args[:2], ("GET", "/message/inbox"))
        self.assertEqual(client.request.call_args.args[2]["mark"], "false")

    def test_oauth_state_mismatch_does_not_exchange_code(self):
        app.save_json(self.config / "approval.json", {"status": "approved", "reference": "ticket-1"})
        app.save_json(self.config / "config.json", {"client_id": "id", "redirect_uri": "http://127.0.0.1:8765/callback", "user_agent": "test"})
        app.save_json(self.config / "oauth_pending.json", {"state": "good"})
        with mock.patch.object(app, "token_request") as token:
            with self.assertRaisesRegex(app.ClientError, "state mismatch"):
                app.authorize_finish("http://127.0.0.1:8765/callback?state=bad&code=secret")
            token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
