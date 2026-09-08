"""Non-stream fallback waiting is bounded by existing total and Run budgets."""
import json
import os
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

from contextox import agent, provider
from contextox.models import RunBudget
from contextox.provider import DeepSeekProvider, ProviderTimeoutUnknownError
import test_stream_fallback as fallback_fixtures


class FallbackTimeoutTests(unittest.TestCase):
    def test_nonstream_waits_past_stream_first_response_but_not_total(self):
        for stream, delay, succeeds in ((True, .8, False), (False, .8, True), (False, 1.6, False)):
            with self.subTest(stream=stream, delay=delay):
                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers['Content-Length']))
                        time.sleep(delay)
                        body = json.dumps({'id': 'synthetic', 'choices': [{'index': 0,
                            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{}'}}],
                            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
                        try:
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/json')
                            self.send_header('Content-Length', str(len(body)))
                            self.end_headers()
                            self.wfile.write(body)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                server = HTTPServer(('127.0.0.1', 0), Handler)
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                configured = agent._provider_timeouts(RunBudget(), 300000, non_stream_fallback=not stream)
                # Scale only wall-clock time for this local HTTP/spawn experiment.
                scaled = replace(configured, connect_ms=1000,
                    first_event_ms=configured.first_event_ms // 100,
                    idle_ms=configured.idle_ms // 100, total_ms=configured.total_ms // 100)
                try:
                    with patch.object(provider, 'DEEPSEEK_ENDPOINT',
                                      f'http://127.0.0.1:{server.server_port}/chat/completions'), \
                         patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'synthetic'}):
                        if succeeds:
                            result = DeepSeekProvider().complete([{'role': 'user', 'content': 'synthetic'}],
                                stream=stream, tools=None, user_id='ws-synthetic', timeouts=scaled)
                            self.assertEqual(result.content, '{}')
                        else:
                            with self.assertRaises(ProviderTimeoutUnknownError):
                                DeepSeekProvider().complete([{'role': 'user', 'content': 'synthetic'}],
                                    stream=stream, tools=None, user_id='ws-synthetic', timeouts=scaled)
                finally:
                    server.shutdown(); server.server_close(); thread.join(3)
                self.assertFalse(thread.is_alive())

    def test_run_calls_use_mode_specific_wait_and_keep_remaining_budget(self):
        fixture = fallback_fixtures.StreamFallbackTests()
        fixture.setUp()
        try:
            with patch.object(agent, '_remaining_run_ms', return_value=90000):
                p, run = fixture.run_case([provider.ProviderStreamInterruptedError(), fallback_fixtures.answer()])
            first, second = [call[1]['timeouts'] for call in p.calls]
            self.assertEqual((first.first_event_ms, second.first_event_ms), (60000, 90000))
            self.assertEqual((first.total_ms, second.total_ms), (90000, 90000))
            self.assertEqual(first.connect_ms, second.connect_ms)
            self.assertEqual(first.idle_ms, second.idle_ms)
            self.assertEqual(run.status, 'partial')
        finally:
            fixture.doCleanups()
