"""Official CLI shutdown with isolated stores and a credential-free Provider."""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from threading import Event, Thread
from unittest.mock import patch

from contextox.api import _event_stream
from contextox import agent, cli
from contextox.provider import ProviderCancelledError
from contextox.runtime import Path2Runtime
from contextox.store import WorkspaceStore
from test_dialogue import AnswerProvider, request
from test_runtime import _mission

ROOT = Path(__file__).resolve().parents[1]


def child(mode, directory, port):
    original_create = cli.create_app

    class WaitingProvider(AnswerProvider):
        def complete(self, messages, **kwargs):
            Path(directory, 'provider-entered').touch()
            if not kwargs['cancel_event'].wait(20):
                raise AssertionError('shutdown did not cancel synthetic request')
            raise ProviderCancelledError(outcome_unknown=True)

    def create(**kwargs):
        app = original_create(**kwargs)
        if mode in {'terminal', 'running'}:
            store = app.state.workspace_store
            ws, mission = _mission(store)
            runtime = app.state.path2_runtime
            receipt, _ = runtime.send_task_message(ws, mission.mission_id, request())
            if mode == 'terminal':
                deadline = time.monotonic() + 5
                while runtime.is_run_active(ws, mission.mission_id, receipt.run.run_id):
                    if time.monotonic() > deadline:
                        raise AssertionError('synthetic Run did not terminate')
                    time.sleep(.01)
            Path(directory, 'identity.json').write_text(json.dumps([ws, mission.mission_id, receipt.run.run_id]))
        return app

    provider = WaitingProvider if mode == 'running' else AnswerProvider
    with patch.object(cli, 'create_app', create), patch.object(agent, 'get_provider', provider):
        return cli.main(['start', '--port', port, '--data-dir', directory])


class ShutdownTests(unittest.TestCase):
    def test_official_cli_shutdown_closes_streams_and_preserves_receipts(self):
        for mode in ('idle', 'global', 'disconnected', 'terminal', 'running'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='contextox-shutdown-', dir='/private/tmp') as directory:
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                # No credentials are inherited by the synthetic test process.
                env = {k: v for k, v in os.environ.items() if 'KEY' not in k and 'TOKEN' not in k and 'SECRET' not in k}
                proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child', mode, directory, str(port)], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                streams = []
                try:
                    url = f'http://127.0.0.1:{port}'
                    deadline = time.monotonic() + 8
                    while True:
                        try:
                            urllib.request.urlopen(url + '/api/health', timeout=.2).close()
                            break
                        except OSError:
                            if proc.poll() is not None or time.monotonic() > deadline:
                                self.fail('test service failed to start')
                            time.sleep(.02)
                    identity = None
                    if mode != 'idle':
                        stream = urllib.request.urlopen(url + '/api/events', timeout=3)
                        stream.readline()
                        streams.append(stream)
                        if mode == "disconnected":
                            stream.close()
                            streams.clear()
                    if mode in {'terminal', 'running'}:
                        identity = json.loads(Path(directory, 'identity.json').read_text())
                        ws, mid, rid = identity
                        stream = urllib.request.urlopen(url + f'/api/workspaces/{ws}/missions/{mid}/runs/{rid}/events', timeout=3)
                        stream.readline()
                        streams.append(stream)
                    if mode == 'running':
                        # Health and the first Run event can precede Provider
                        # entry. Exercise shutdown during a request, not during
                        # the worker's scheduling/startup race.
                        entered = Path(directory, 'provider-entered')
                        deadline = time.monotonic() + 3
                        while not entered.exists() and proc.poll() is None and time.monotonic() < deadline:
                            time.sleep(.01)
                        self.assertTrue(entered.exists(), 'synthetic Provider did not start')
                    started = time.monotonic()
                    proc.send_signal(signal.SIGINT)
                    out, err = proc.communicate(timeout=5)
                    self.assertLess(time.monotonic() - started, 5)
                    self.assertEqual(proc.returncode, 0, err)
                    self.assertIn('Application shutdown complete', err)
                    self.assertNotIn('Traceback', err)
                    with socket.socket() as probe:
                        self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)
                    for stream in streams:
                        stream.read()  # EOF while clients remain open, not a client-close workaround.
                    with closing(sqlite3.connect(Path(directory, 'contextox.sqlite3'))) as db:
                        self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                        if identity:
                            status = db.execute('SELECT status,error_code,input_tokens FROM provider_receipts WHERE run_id=?', (identity[2],)).fetchall()
                            if mode == 'running':
                                self.assertEqual(status, [('cancelled', 'provider_cancelled_outcome_unknown', None)])
                            else:
                                self.assertEqual(status, [('succeeded', None, 1)])
                    if identity:
                        store = WorkspaceStore.open(directory)
                        snapshot = store.get_run_snapshot(*identity)
                        self.assertEqual(snapshot.status, 'cancelled' if mode == 'running' else 'partial')
                finally:
                    for stream in streams:
                        stream.close()
                    if proc.poll() is None:
                        proc.kill()
                    proc.communicate()

    def test_disconnected_global_stream_does_not_start_executor_wait(self):
        async def check():
            stopped = asyncio.Event()
            stream = _event_stream(stopped)
            await anext(stream)
            pending = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            await stream.aclose()
            self.assertFalse(stopped.is_set())
        with patch("asyncio.to_thread", side_effect=AssertionError("global SSE must not use a worker")):
            asyncio.run(check())

    def test_stop_before_wait_does_not_lose_wakeup(self):
        with tempfile.TemporaryDirectory(prefix='contextox-stop-wait-', dir='/private/tmp') as directory:
            runtime = Path2Runtime(WorkspaceStore.open(directory))
            stopped = Event()
            stopped.set()
            runtime.wake_event_waiters()
            thread = Thread(target=runtime.wait_for_change, args=('unused', 'unused', 'unused', 0, 15, stopped), daemon=True)
            thread.start()
            thread.join(.5)
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        raise SystemExit(child(*sys.argv[2:]))
    unittest.main()
