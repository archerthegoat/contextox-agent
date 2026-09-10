import asyncio
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

from contextox.api import create_app
from contextox.cli import main
from contextox.credentials import EnvFileError, load_env_file, provider_key
from contextox.provider import DeepSeekProvider
from test_local_settings import request


class EnvFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.file = self.directory / "synthetic-config.env"
        load_env_file(None)
        self.addCleanup(load_env_file, None)
        environment = patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.keychain = Mock()
        self.keychain.read.return_value = "synthetic-keychain-value"
        for target in ("contextox.credentials.MacKeychain", "contextox.local_settings.MacKeychain"):
            manager = patch(target, return_value=self.keychain)
            manager.start()
            self.addCleanup(manager.stop)

    def write(self, contents):
        self.file.write_text(contents)
        load_env_file(self.file)

    def test_explicit_file_precedence_and_startup_snapshot(self):
        # A file in the current directory is not automatically discovered.
        self.file.write_text("DEEPSEEK_API_KEY=synthetic-file-value\n")
        self.assertEqual(provider_key(), "synthetic-keychain-value")
        load_env_file(self.file)
        self.assertEqual(provider_key(), "synthetic-file-value")
        self.file.write_text("DEEPSEEK_API_KEY=synthetic-changed-value\n")
        self.assertEqual(provider_key(), "synthetic-file-value")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-environment-value"}):
            self.assertEqual(provider_key(), "synthetic-environment-value")
        load_env_file(self.file)
        self.assertEqual(provider_key(), "synthetic-changed-value")
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "")
        load_env_file(None)
        self.assertEqual(provider_key(), "synthetic-keychain-value")

    def test_quotes_comments_and_other_assignments_do_not_execute_or_import(self):
        marker = self.directory / "must-not-exist"
        for value in ("synthetic-file-value", "'synthetic-file-value'", '"synthetic-file-value"'):
            self.write(f"# synthetic fixture\nOTHER=$(touch {marker})\nexport DEEPSEEK_API_KEY = {value} # note\n")
            self.assertEqual(provider_key(), "synthetic-file-value")
        self.assertFalse(marker.exists())
        for value in (f"$(touch {marker})", "${SYNTHETIC_OTHER_KEY}", "`synthetic-command`"):
            with self.assertRaises(EnvFileError):
                self.write(f'DEEPSEEK_API_KEY="{value}"\n')
        self.assertFalse(marker.exists())

    def test_invalid_or_unbounded_files_fail_without_disclosing_contents(self):
        secret = "synthetic-secret-do-not-echo"
        cases = ["", "OTHER=value", f"DEEPSEEK_API_KEY={secret}\nDEEPSEEK_API_KEY={secret}",
                 "DEEPSEEK_API_KEY=short", f"DEEPSEEK_API_KEY='{secret}",
                 "DEEPSEEK_API_KEY=" + "x" * 513, "#" * (16 * 1024 + 1), f"run {secret}"]
        for contents in cases:
            with self.subTest(size=len(contents)), self.assertRaises(EnvFileError) as raised:
                self.write(contents)
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(str(self.file), str(raised.exception))
        self.file.write_bytes(b"\xff")
        for path in (self.file, self.directory, self.directory / "missing"):
            with self.assertRaises(EnvFileError):
                load_env_file(path)
        fifo = self.directory / "pipe"
        os.mkfifo(fifo)
        with self.assertRaises(EnvFileError):
            load_env_file(fifo)

    def test_start_flag_configures_before_serving_and_invalid_file_does_not_create_data(self):
        self.file.write_text("DEEPSEEK_API_KEY=synthetic-file-value\n")
        data = self.directory / "data"
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        def serve(args, data_dir, listener):
            self.assertEqual(provider_key(), "synthetic-file-value")
            self.assertEqual(data_dir, data.resolve())
            return 0
        with patch("contextox.cli._serve", side_effect=serve):
            self.assertEqual(main(["start", "--env-file", str(self.file), "--data-dir", str(data), "--port", str(port)]), 0)
        missing_data = self.directory / "must-not-create"
        self.file.write_text("DEEPSEEK_API_KEY=bad")
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            main(["start", "--env-file", str(self.file), "--data-dir", str(missing_data)])
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(missing_data.exists())
        self.assertNotIn(str(self.file), error.getvalue())

    def test_settings_identifies_file_and_rejects_web_changes_without_provider_call(self):
        secret = "synthetic-file-value"
        self.write(f"DEEPSEEK_API_KEY={secret}\n")
        app = create_app(data_dir=self.directory / "data", agent_profile="demo-fast")
        with patch.object(DeepSeekProvider, "complete") as complete:
            status, result, headers, _ = asyncio.run(request(app))
            self.assertEqual(status, 200)
            self.assertEqual(result["source"], "env_file")
            self.assertTrue(result["configured"])
            self.assertNotIn(secret, json.dumps(result))
            self.assertEqual(headers[b"cache-control"], b"no-store")
            auth = {"origin": "http://127.0.0.1:8787", "x-contextox-session": result["session_token"]}
            for method, body in (("DELETE", b""), ("PUT", json.dumps({"api_key": "synthetic-replacement"}).encode())):
                self.assertEqual(asyncio.run(request(app, method, body, auth))[0], 409)
            complete.assert_not_called()
        self.keychain.contains.assert_not_called()
        self.keychain.read.assert_not_called()
        self.keychain.save.assert_not_called()
        self.keychain.remove.assert_not_called()
