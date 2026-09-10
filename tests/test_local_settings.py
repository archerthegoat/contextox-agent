import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from contextox.api import create_app
from contextox.credentials import CredentialUnavailableError, provider_key
from contextox.provider import DeepSeekProvider, ProviderNotConfiguredError


async def request(app, method="GET", body=b"", extra=None):
    messages, reads = [], []
    headers = {"host": "127.0.0.1:8787", "content-type": "application/json"} | (extra or {})
    async def receive():
        reads.append(True)
        return {"type": "http.request", "body": body, "more_body": False}
    async def send(value):
        messages.append(value)
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": "/api/local-settings/deepseek",
        "query_string": b"", "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 9000),
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    result = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], json.loads(result), dict(start["headers"]), len(reads)


class LocalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(data_dir=Path(self.directory.name), agent_profile="demo-fast")
        self.keychain = Mock()
        self.keychain.contains.return_value = False
        for target, kwargs in (("contextox.local_settings.MacKeychain", {"return_value": self.keychain}),
                               ("contextox.credentials.MacKeychain", {"return_value": self.keychain})):
            manager = patch(target, **kwargs); manager.start(); self.addCleanup(manager.stop)
        env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}); env.start(); self.addCleanup(env.stop)

    def call(self, *args, **kwargs):
        return asyncio.run(request(self.app, *args, **kwargs))

    def auth(self):
        status, body, headers, _ = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertEqual(body["source"], "missing")
        self.assertEqual(body["connection"], "not_run")
        self.assertEqual(body["thinking"], "disabled")
        return {"origin": "http://127.0.0.1:8787", "x-contextox-session": body["session_token"]}

    def test_save_remove_status_and_restart_never_return_or_verify_secret(self):
        headers = self.auth()
        secret = "synthetic-credential-for-local-api-test"
        self.keychain.contains.return_value = True
        with patch.object(DeepSeekProvider, "complete") as complete:
            status, result, _, _ = self.call("PUT", json.dumps({"api_key": secret}).encode(), headers)
            self.assertEqual(status, 200)
            self.assertEqual(result["source"], "keychain")
            self.assertNotIn(secret, json.dumps(result))
            self.keychain.save.assert_called_once_with(secret)
            self.keychain.read.assert_not_called()
            self.keychain.contains.return_value = False
            self.assertEqual(self.call("DELETE", extra=headers)[0], 200)
            self.keychain.remove.assert_called_once()
            complete.assert_not_called()
        self.app = create_app(data_dir=Path(self.directory.name), agent_profile="demo-fast")
        self.assertEqual(self.call("DELETE", extra=headers)[0], 403)

    def test_cross_origin_rebinding_and_missing_or_stale_token_rejected_before_body(self):
        headers = self.auth()
        for override in ({"origin": "https://example.org"}, {"host": "example.org:8787"},
                         {"x-contextox-session": "stale"}, {"origin": ""}):
            with self.subTest(override=override):
                status, _, response_headers, reads = self.call("PUT", b"sensitive body", headers | override)
                self.assertEqual(status, 403)
                self.assertEqual(reads, 0)
                self.assertEqual(response_headers[b"cache-control"], b"no-store")
        self.assertEqual(self.call("PUT", b"sensitive body")[0], 403)
        self.keychain.save.assert_not_called()

    def test_invalid_and_oversized_body_safe_errors(self):
        headers = self.auth()
        for secret in ("short", "with spaces " * 10, "x" * 600, "x" * 5000):
            status, body, response_headers, _ = self.call("PUT", json.dumps({"api_key": secret}).encode(), headers)
            self.assertEqual(status, 422)
            self.assertNotIn(secret, json.dumps(body))
            self.assertEqual(response_headers[b"cache-control"], b"no-store")
        self.keychain.save.assert_not_called()

    def test_busy_environment_and_keychain_failure_do_not_mutate(self):
        headers = self.auth()
        task = self.app.state.path2_runtime._reserve("run")
        self.assertEqual(self.call("DELETE", extra=headers)[0], 409)
        self.app.state.path2_runtime._release(task.token)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-environment-key"}):
            self.assertEqual(self.call()[1]["source"], "environment")
            self.assertEqual(self.call("DELETE", extra=headers)[0], 409)
            self.assertEqual(provider_key(), "synthetic-environment-key")
        self.keychain.remove.assert_not_called()
        self.keychain.read.side_effect = CredentialUnavailableError()
        with self.assertRaises(ProviderNotConfiguredError):
            DeepSeekProvider(transport=Mock()).complete([], stream=False, tools=None, user_id="synthetic")
        self.keychain.contains.side_effect = CredentialUnavailableError()
        self.assertEqual(self.call()[1]["source"], "unavailable")


if __name__ == "__main__":
    unittest.main()
