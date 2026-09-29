"""Archive staging guards keep reviewed bytes stable without elevation."""

import os
import tempfile
import unittest
from pathlib import Path

from orbit_gtk.backend.local_deb import staged_archive
from orbit_gtk.backend.transactions import TransactionRejected


class ArchiveTests(unittest.TestCase):
    def test_staging_is_private_and_survives_source_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "local.deb"
            source.write_bytes(b"original")
            with staged_archive(str(source)) as staged:
                target = Path(staged)
                source.write_bytes(b"changed")
                self.assertEqual(target.read_bytes(), b"original")
                self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
            self.assertFalse(target.exists())

    def test_symlinks_and_special_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.deb"
            path.symlink_to("/dev/null")
            with self.assertRaises(OSError), staged_archive(str(path)):
                pass
            path.unlink()
            os.mkfifo(path)
            with self.assertRaises(TransactionRejected), staged_archive(str(path)):
                pass

    def test_missing_archive_is_reported(self):
        with self.assertRaises(FileNotFoundError), staged_archive("/missing/package.deb"):
            pass
