#!/usr/bin/env python3
"""Offline bootstrap regressions; no runtime download or Xcode required."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / 'app/scripts/fetch-python.sh'
        self.script.parent.mkdir(parents=True)
        shutil.copy2(ROOT / 'app/scripts/fetch-python.sh', self.script)
        self.runtime = self.root / 'app/Resources/python'
        self.archive = self.root / 'runtime.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as tar:
            data = b'#!/bin/sh\nexit 0\n'
            info = tarfile.TarInfo('python/bin/python3.13')
            info.mode = 0o755
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        tools = self.root / 'tools'
        tools.mkdir()
        curl = tools / 'curl'
        curl.write_text('#!/bin/bash\nset -eu\n'
                        'echo call >> "$CALLS"\n'
                        '[ "${FAIL_DOWNLOAD:-0}" = 0 ] || exit 22\n'
                        'while [ "$#" -gt 0 ]; do\n'
                        ' if [ "$1" = -o ]; then cp "$ARCHIVE" "$2"; exit 0; fi\n'
                        ' shift\ndone\nexit 1\n')
        curl.chmod(0o755)
        self.env = dict(os.environ, PATH=str(tools) + ':/usr/bin:/bin',
                        ARCHIVE=str(self.archive), CALLS=str(self.root / 'calls'),
                        TMPDIR=str(self.root))

    def run_script(self):
        return subprocess.run(['/bin/bash', str(self.script)], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_install_layout_and_idempotence(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.runtime / 'bin/python3.13').is_file(), result.stdout)
        self.assertFalse((self.runtime / 'python').exists())
        again = self.run_script()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual((self.root / 'calls').read_text().splitlines(), ['call'])

    def test_download_failure_leaves_no_install(self):
        self.env['FAIL_DOWNLOAD'] = '1'
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.runtime.exists())

    def test_invalid_archive_leaves_no_install(self):
        self.archive.write_bytes(b'not an archive')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.runtime.exists())

    def test_invalid_existing_install_is_preserved(self):
        self.runtime.mkdir(parents=True)
        sentinel = self.runtime / 'keep-me'
        sentinel.write_text('existing data')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), 'existing data')
        self.assertIn('incomplete', result.stderr.lower())


if __name__ == '__main__':
    unittest.main(verbosity=2)
