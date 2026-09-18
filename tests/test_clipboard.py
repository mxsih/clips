import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from gi.repository import Gio, GLib

from src.sub_utils.clipboard import ClipboardWriter
from test_paste import wait_for


class ClipboardWriterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'clip'
        self.path.write_bytes(b'clipboard test\x00\xff')
        self.writer = ClipboardWriter()
        self.results = []
        self.backend = patch('src.sub_utils.clipboard.is_wayland', return_value=False)
        self.backend.start()

    def tearDown(self):
        self.writer.close()
        self.directory.cleanup()
        self.backend.stop()

    def helper(self, code):
        spawn = Gio.Subprocess.new
        return patch('src.sub_utils.clipboard.Gio.Subprocess.new',
                     side_effect=lambda argv, flags: spawn([sys.executable, '-c', code], flags))

    def copy(self):
        self.writer.copy_file('application/octet-stream', str(self.path), None, self.results.append)

    def test_waits_for_publication_before_callback(self):
        marker = Path(self.directory.name) / 'received'
        with self.helper('import sys; from pathlib import Path; Path(' + repr(str(marker)) + ').write_bytes(sys.stdin.buffer.read())'):
            self.copy()
            wait_for(lambda: bool(self.results))
        self.assertEqual(self.results, [True])
        self.assertEqual(marker.read_bytes(), self.path.read_bytes())
        self.assertTrue(self.writer.owns_content(marker.read_bytes()))
        self.assertIsNone(self.writer.process)
        self.assertIsNone(self.writer.timeout)

    def test_failed_helper_preserves_previous_ownership(self):
        self.writer.own_digest = b'previous'
        with self.helper('import sys; sys.stdin.buffer.read(); sys.exit(1)'):
            self.copy()
            wait_for(lambda: bool(self.results))
        self.assertEqual(self.results, [False])
        self.assertEqual(self.writer.own_digest, b'previous')

    def test_missing_helper_and_file_report_failure(self):
        with patch('src.sub_utils.clipboard.Gio.Subprocess.new', side_effect=GLib.Error('Missing helper')):
            self.copy()
        self.path.unlink()
        self.copy()
        self.assertEqual(self.results, [False, False])

    def test_concurrent_copy_does_not_replace_active_operation(self):
        with self.helper('import sys, time; sys.stdin.buffer.read(); time.sleep(0.1)'):
            self.copy()
            self.copy()
            wait_for(lambda: len(self.results) == 2)
        self.assertEqual(self.results, [False, True])

    def test_shutdown_completes_pending_copy_as_failure(self):
        with self.helper('import sys, time; sys.stdin.buffer.read(); time.sleep(30)'):
            self.copy()
            self.writer.close()
            wait_for(lambda: bool(self.results))
        self.assertEqual(self.results, [False])
        self.assertIsNone(self.writer.timeout)


if __name__ == '__main__':
    unittest.main()
