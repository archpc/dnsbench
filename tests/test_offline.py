#!/usr/bin/env python3
"""Portable smoke suite: python3 tests/test_offline.py. No external traffic."""
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def network_guard(event, args):
    if event in ("socket.connect", "socket.sendto", "socket.bind"):
        address = args[-1]
        if isinstance(address, tuple):
            if not ipaddress.ip_address(address[0]).is_loopback:
                raise AssertionError(f"external network forbidden: {event} {address}")
    elif event == "socket.getaddrinfo":
        if not ipaddress.ip_address(args[0]).is_loopback:
            raise AssertionError("external DNS forbidden")
    elif event in ("socket.gethostbyname", "socket.gethostbyaddr"):
        raise AssertionError("DNS lookup forbidden in offline suite")


class OfflineTests(unittest.TestCase):
    def test_no_personal_data_import(self):
        import adapter
        scratch = Path.home() / ".hermes/cache/scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "pool.json").write_text('{"personal": true}')
        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch
            with patch.object(adapter, "DATASETS", tmp), patch.object(
                    adapter, "RUNS", str(Path(tmp) / "runs")):
                self.assertEqual(adapter.ensure_datasets(), [])
                self.assertFalse((Path(tmp) / "pool.json").exists())
                self.assertTrue((Path(tmp) / "runs").is_dir())

    def test_vendored_engine_and_wire_parser(self):
        import adapter
        for module in (adapter.dnslib, adapter.discover, adapter.measure):
            self.assertEqual(Path(module.__file__).parent, ROOT / "engine")
        self.assertNotIn(str(Path.home() / ".hermes/skills/macos-diagnostics/dns-benchmark/scripts"), sys.path)
        passed, total = adapter.selftest()
        self.assertEqual(passed, total)
        self.assertGreater(total, 0)

    def test_guard_rejects_external_traffic(self):
        import socket
        with socket.socket() as sock:
            with self.assertRaisesRegex(AssertionError, "external network"):
                sock.connect(("192.0.2.1", 53))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            with self.assertRaisesRegex(AssertionError, "external network"):
                sock.sendto(b"test", ("192.0.2.1", 53))

    def test_fixture_http_and_analysis(self):
        import server
        fixture = ROOT / "tests/fixtures/synthetic"
        self.assertTrue((fixture / "pool.json").is_file(), "synthetic fixture missing")
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            shutil.copytree(fixture, data)
            from unittest.mock import patch
            with patch.object(server.engine, "DATASETS", str(data)), patch.object(
                    server.engine, "RUNS", str(data / "runs")):
                server.engine.ensure_datasets()
                # HTTPServer otherwise reverse-resolves its bind address.
                with patch('socket.getfqdn', return_value='localhost'):
                    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                srv.daemon_threads = True
                worker = threading.Thread(target=srv.serve_forever, daemon=True)
                worker.start()
                base = f"http://127.0.0.1:{srv.server_port}"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                def get(path):
                    with opener.open(base + path, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        body = response.read()
                        return json.loads(body) if path.startswith('/api/') else body
                try:
                    for path in ("/", "/app.js", "/styles.css"):
                        self.assertTrue(get(path))
                    self.assertEqual(get('/api/state')['n_candidates'], 4)
                    self.assertFalse(get('/api/state')['running'])
                    self.assertEqual(get('/api/datasets')['pool']['eligible'], 2)
                    self.assertEqual(len(get('/api/points')['points']), 4)
                    self.assertEqual(len(get('/api/points-meta')['ips']), 4)
                    coverage = get('/api/coverage')
                    self.assertEqual([coverage[k] for k in ('alive', 'dead', 'unknown', 'hijack')], [2, 1, 1, 0])
                    runs = get('/api/runs')['runs']
                    self.assertEqual(len(runs), 2)
                    a, b = sorted(r['ts'] for r in runs)
                    ranked = get('/api/runs/' + a)
                    self.assertTrue(ranked['synthetic'])
                    self.assertEqual(ranked['ranked'][0]['u_med'], 10.0)
                    self.assertEqual(get(f'/api/compare/{a}/{b}')['overlap'], 2)
                    self.assertEqual(len(get('/api/history?ip=192.0.2.1')['runs']), 2)
                    samples = get(f'/api/runs/{a}/queries/192.0.2.1')
                    self.assertEqual(samples['uncached']['ok'], [9.0, 10.0, 11.0])
                    self.assertEqual(samples['cached']['ok'], [2.0, 3.0, 4.0])
                finally:
                    srv.shutdown()
                    srv.server_close()
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    if '--isolated' not in sys.argv:
        with tempfile.TemporaryDirectory(prefix='dnsbench-offline-') as tmp:
            env = dict(os.environ, HOME=tmp, PYTHONNOUSERSITE='1',
                       PYTHONDONTWRITEBYTECODE='1', DNSBENCH_DATA_DIR=str(Path(tmp) / 'data'))
            env.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--isolated'], env=env)
            sys.exit(result.returncode)
    sys.argv.remove('--isolated')
    # An installed skill must not become an implicit import fallback.
    (Path.home() / '.hermes/skills/macos-diagnostics/dns-benchmark/scripts').mkdir(parents=True)
    sys.path[:0] = [str(ROOT), str(ROOT / 'engine')]
    sys.addaudithook(network_guard)
    unittest.main(verbosity=2)
