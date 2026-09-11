import importlib.util
import json
import hashlib
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from contextox import cli
from contextox.local_install import own_instance

SPEC = importlib.util.spec_from_file_location("install_bundle", Path(__file__).resolve().parents[1] / "scripts/install_bundle.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class LocalInstallTests(unittest.TestCase):
    def test_duplicate_start_does_not_open_store_or_recover_runs(self):
        with tempfile.TemporaryDirectory() as directory, own_instance(Path(directory), 9123), \
                patch.object(cli, "create_app") as create, patch.object(cli.webbrowser, "open") as browser:
            result = cli.main(["start", "--data-dir", directory, "--open-browser"])
            self.assertEqual(result, 0)
            create.assert_not_called()
            browser.assert_called_once_with("http://127.0.0.1:9123")

    def test_busy_port_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            with patch.object(cli, "create_app") as create:
                self.assertEqual(cli.main(["start", "--data-dir", directory,
                    "--port", str(occupied.getsockname()[1])]), 3)
                create.assert_not_called()

    def test_version_install_is_repeatable_preserves_old_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory(prefix="contextox installer ") as directory:
            base = Path(directory)
            bundle, root = base / "bundle", base / "installed"
            bundle.mkdir()
            metadata = {"version":"1.0.0", "commit":"a" * 40, "content_hash":"b" * 64}
            (bundle / "BUILD.json").write_text(json.dumps(metadata))
            (bundle / "application.txt").write_text("synthetic application")
            manifest = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()}
            (bundle / "FILES.sha256.json").write_text(json.dumps(manifest))
            launcher = installer.install(bundle, root)
            first = (root / "current").resolve()
            self.assertTrue(launcher.is_file())
            self.assertEqual(installer.install(bundle, root), launcher)
            external = base / "external.txt"
            external.write_text("do not overwrite")
            launcher.unlink()
            launcher.symlink_to(external)
            with self.assertRaises(ValueError):
                installer.install(bundle, root)
            self.assertEqual(external.read_text(), "do not overwrite")
            launcher.unlink()
            installer.install(bundle, root)
            (root / "user-owned.txt").write_text("preserve")
            (bundle / "application.txt").write_text("corrupt")
            with self.assertRaises(ValueError):
                installer.install(bundle, root)
            self.assertEqual((root / "current").resolve(), first)
            self.assertEqual((root / "user-owned.txt").read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
