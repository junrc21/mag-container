import json
import os
from pathlib import Path
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bootstrap"))

from mag_credit_guard import CreditStatus, check_authoritative_credits


class _CreditsHandler(BaseHTTPRequestHandler):
    payload = {"creditsRemaining": 0, "plan": "free"}

    def do_GET(self):
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        return


class CreditGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _CreditsHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_env = {
            "MAG_API_URL": f"http://127.0.0.1:{cls.server.server_port}",
            "MAG_INTERNAL_KEY": "test-key",
            "MAG_TENANT_SLUG": "tenant test",
        }

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_zero_balance_is_exhausted(self):
        _CreditsHandler.payload = {"creditsRemaining": 0, "plan": "free"}
        with patch.dict(os.environ, self.base_env, clear=True):
            result = check_authoritative_credits()
        self.assertIs(result.status, CreditStatus.EXHAUSTED)
        self.assertEqual(result.plan, "free")

    def test_positive_balance_is_available(self):
        _CreditsHandler.payload = {"creditsRemaining": 1, "plan": "teams"}
        with patch.dict(os.environ, self.base_env, clear=True):
            result = check_authoritative_credits()
        self.assertIs(result.status, CreditStatus.AVAILABLE)

    def test_missing_configuration_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            result = check_authoritative_credits()
        self.assertIs(result.status, CreditStatus.UNAVAILABLE)

    def test_malformed_balance_fails_closed(self):
        _CreditsHandler.payload = {"creditsRemaining": "0", "plan": "free"}
        with patch.dict(os.environ, self.base_env, clear=True):
            result = check_authoritative_credits()
        self.assertIs(result.status, CreditStatus.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
