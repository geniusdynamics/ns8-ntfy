"""Backup snapshot and restore checks without requiring Podman or systemd."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "imageroot/pypkg"))
import ntfy_backup  # noqa: E402


class SnapshotTests(unittest.TestCase):
    def test_snapshot_and_restore_preserve_databases_attachments_and_config(self):
        with tempfile.TemporaryDirectory() as directory:
            volume = Path(directory) / "volume"
            config = Path(directory) / "state/config/server.yml"
            volume.mkdir()
            config.parent.mkdir(parents=True)
            config.write_text('auth-file: "/var/lib/ntfy/auth.db"\n')
            config.chmod(0o600)
            (volume / "cache.db").write_bytes(b"messages")
            (volume / "auth.db").write_bytes(b"users and ACLs")
            (volume / "auth.db-wal").write_bytes(b"pending transaction")
            (volume / "attachments").mkdir()
            (volume / "attachments/file").write_bytes(b"attachment")

            ntfy_backup.run_in_user_namespace(
                ntfy_backup.SNAPSHOT_SCRIPT, volume, config, prefix=()
            )
            snapshot = volume / ntfy_backup.SNAPSHOT
            self.assertEqual((snapshot / "data/auth.db").read_bytes(), b"users and ACLs")
            self.assertEqual((snapshot / "data/auth.db-wal").read_bytes(), b"pending transaction")
            self.assertEqual((snapshot / "data/attachments/file").read_bytes(), b"attachment")
            self.assertEqual((snapshot / "server.yml").read_text(), config.read_text())

            (volume / "auth.db").unlink()
            (volume / "auth.db-wal").unlink()
            (volume / "cache.db").unlink()
            (volume / "attachments/file").unlink()
            config.unlink()
            ntfy_backup.run_in_user_namespace(
                ntfy_backup.RESTORE_SCRIPT, volume, config, prefix=()
            )
            self.assertEqual((volume / "auth.db").read_bytes(), b"users and ACLs")
            self.assertEqual((volume / "auth.db-wal").read_bytes(), b"pending transaction")
            self.assertEqual((volume / "cache.db").read_bytes(), b"messages")
            self.assertEqual((volume / "attachments/file").read_bytes(), b"attachment")
            self.assertIn("auth-file:", config.read_text())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertFalse(snapshot.exists())

    def test_snapshot_links_attachments_and_copies_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            volume = Path(directory)
            config = volume / "missing.yml"
            (volume / "cache.db").write_bytes(b"messages")
            (volume / "attachments").mkdir()
            (volume / "attachments/file").write_bytes(b"attachment")

            ntfy_backup.run_in_user_namespace(
                ntfy_backup.SNAPSHOT_SCRIPT, volume, config, "attachments", prefix=()
            )
            snapshot = volume / ntfy_backup.SNAPSHOT / "data"
            self.assertTrue((snapshot / "attachments/file").samefile(volume / "attachments/file"))
            self.assertFalse((snapshot / "cache.db").samefile(volume / "cache.db"))
            self.assertEqual((snapshot / "cache.db").read_bytes(), b"messages")
            self.assertFalse((snapshot / "attachments/attachments").exists())

            # An expired attachment removed by ntfy stays in the snapshot.
            (volume / "attachments/file").unlink()
            self.assertEqual((snapshot / "attachments/file").read_bytes(), b"attachment")

    def test_attachment_directory_is_a_plain_top_level_volume_entry(self):
        cases = {
            'attachment-cache-dir: "/var/lib/ntfy/attachments"\n': "attachments",
            "attachment-cache-dir: /var/lib/ntfy/files/ # comment\n": "files",
            'attachment-cache-dir: "/var/lib/ntfy/a/b"\n': "",
            'attachment-cache-dir: "/srv/attachments"\n': "",
            'attachment-cache-dir: "/var/lib/ntfy/.hidden"\n': "",
            'attachment-cache-dir: "/var/lib/ntfy/*"\n': "",
            "base-url: https://ntfy.test\n": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "server.yml"
            self.assertEqual(ntfy_backup.attachment_directory(config), "")
            for content, expected in cases.items():
                config.write_text(content)
                self.assertEqual(ntfy_backup.attachment_directory(config), expected, content)

    def test_new_snapshot_replaces_old_copy_without_nesting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            volume = Path(directory)
            config = volume / "missing.yml"
            (volume / "cache.db").write_text("before")
            for content in ("before", "after"):
                (volume / "cache.db").write_text(content)
                ntfy_backup.run_in_user_namespace(
                    ntfy_backup.SNAPSHOT_SCRIPT, volume, config, prefix=()
                )
            snapshot = volume / ntfy_backup.SNAPSHOT
            self.assertEqual((snapshot / "data/cache.db").read_text(), "after")
            self.assertFalse((snapshot / "data" / ntfy_backup.SNAPSHOT).exists())
            ntfy_backup.run_in_user_namespace(
                ntfy_backup.CLEANUP_SCRIPT, volume, prefix=()
            )
            self.assertFalse(snapshot.exists())

    def test_ntfy_restarts_if_the_snapshot_copy_fails(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(tuple(command))
            if command[:2] == ("podman", "unshare"):
                raise subprocess.CalledProcessError(1, command)
            if command[:3] == ("podman", "ps", "--filter"):
                return subprocess.CompletedProcess(command, 0, "")
            return subprocess.CompletedProcess(command, 0, "")

        with tempfile.TemporaryDirectory() as state_dir:
            with patch.object(ntfy_backup, "volume_mountpoint", return_value="/test/volume"):
                with patch.object(ntfy_backup.subprocess, "run", side_effect=fake_run):
                    with self.assertRaises(subprocess.CalledProcessError):
                        ntfy_backup.make_snapshot(state_dir)

        self.assertIn(("systemctl", "--user", "stop", "ntfy.service"), calls)
        self.assertIn(("systemctl", "--user", "start", "ntfy.service"), calls)
        self.assertLess(
            calls.index(("systemctl", "--user", "stop", "ntfy.service")),
            calls.index(("systemctl", "--user", "start", "ntfy.service")),
        )

    def test_ntfy_stays_off_if_it_was_off_before_the_backup(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(tuple(command))
            result = 3 if "is-active" in command else 0
            return subprocess.CompletedProcess(command, result, "")

        with patch.object(ntfy_backup.subprocess, "run", side_effect=fake_run):
            ntfy_backup.with_ntfy_stopped(lambda: None)
        self.assertNotIn(("systemctl", "--user", "start", "ntfy.service"), calls)

    def test_restore_without_snapshot_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            volume = Path(directory) / "volume"
            volume.mkdir()
            (volume / "cache.db").write_bytes(b"messages")
            with self.assertRaises(subprocess.CalledProcessError):
                ntfy_backup.run_in_user_namespace(
                    ntfy_backup.RESTORE_SCRIPT, volume,
                    Path(directory) / "config/server.yml", prefix=(),
                )
            self.assertEqual((volume / "cache.db").read_bytes(), b"messages")

    def test_restore_snapshot_accepts_backups_without_a_snapshot(self):
        real = ntfy_backup.run_in_user_namespace

        def without_unshare(script, *arguments, **kwargs):
            return real(script, *arguments, prefix=(), **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            volume = Path(directory) / "volume"
            volume.mkdir()
            (volume / "cache.db").write_bytes(b"messages")
            with patch.object(ntfy_backup, "volume_mountpoint", return_value=str(volume)), \
                    patch.object(ntfy_backup, "run_in_user_namespace", side_effect=without_unshare), \
                    patch.object(ntfy_backup, "with_ntfy_stopped", side_effect=lambda operation: operation()):
                # A backup of a release up to 1.x: no snapshot in the volume
                self.assertFalse(ntfy_backup.restore_snapshot(directory))
                # An incomplete snapshot is still an error
                (volume / ntfy_backup.SNAPSHOT).mkdir()
                with self.assertRaises(subprocess.CalledProcessError):
                    ntfy_backup.restore_snapshot(directory)
                (volume / ntfy_backup.SNAPSHOT / "data").mkdir()
                (volume / ntfy_backup.SNAPSHOT / "data/cache.db").write_bytes(b"restored")
                self.assertTrue(ntfy_backup.restore_snapshot(directory))
            self.assertEqual((volume / "cache.db").read_bytes(), b"restored")
