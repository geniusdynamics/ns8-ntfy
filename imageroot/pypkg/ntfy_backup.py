#
# Copyright (C) 2026 Nethesis S.r.l.
# Copyright (C) 2026 Genius Dynamics
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Adapted from Platypuschan/ns8-ntfy-reworked (GPL-3.0).
#

"""Prepare a consistent ntfy data snapshot for the NS8 module backup."""

import os
import re
import shutil
import subprocess
import sys

import ntfy_config


# Podman volume mounted at /var/lib/ntfy. Releases up to 1.x listed it in
# state-include.conf but kept the data in the state directory.
DATA_VOLUME = "ntfy-app"
SNAPSHOT = ".ntfy-backup-snapshot"
# RESTORE_SCRIPT exit code for a backup without a snapshot: it comes from a
# release that did not back up ntfy data.
NO_SNAPSHOT = 3

# Run file operations in Podman's user namespace: ntfy may own files as a
# subordinate UID that the module agent cannot read directly.
SNAPSHOT_SCRIPT = r"""
volume=$1
config=$2
attachments=$3
snapshot="$volume/.ntfy-backup-snapshot"
staging="$volume/.ntfy-backup-snapshot.tmp"
rm -rf -- "$staging"
mkdir -p -- "$staging/data"
# Copy everything but the attachments. Without an attachment directory the
# third pattern repeats the first one.
find "$volume" -mindepth 1 -maxdepth 1 \
  ! -name '.ntfy-backup-snapshot' \
  ! -name '.ntfy-backup-snapshot.tmp' \
  ! -name "${attachments:-.ntfy-backup-snapshot}" \
  -exec cp -a -- {} "$staging/data/" \;
# ntfy never changes an attachment file after writing it, so hard links
# give a stable copy without duplicating the attachment data.
if [ -n "$attachments" ] && [ -e "$volume/$attachments" ]; then
  cp -al -- "$volume/$attachments" "$staging/data/"
fi
if [ -f "$config" ]; then
  cp -a -- "$config" "$staging/server.yml"
fi
rm -rf -- "$snapshot"
mv -- "$staging" "$snapshot"
"""

RESTORE_SCRIPT = r"""
volume=$1
config=$2
snapshot="$volume/.ntfy-backup-snapshot"
[ -e "$snapshot" ] || exit 3
[ -d "$snapshot/data" ] || { echo "The backup has no complete ntfy data snapshot" >&2; exit 1; }
cp -a -- "$snapshot/data/." "$volume/"
if [ -f "$snapshot/server.yml" ]; then
  mkdir -p -- "${config%/*}"
  chmod 700 "${config%/*}"
  cp -a -- "$snapshot/server.yml" "$config"
  chmod 600 "$config"
fi
rm -rf -- "$snapshot"
"""

CLEANUP_SCRIPT = r"""
volume=$1
rm -rf -- "$volume/.ntfy-backup-snapshot" "$volume/.ntfy-backup-snapshot.tmp"
"""


def volume_mountpoint():
    # The service may not have created the volume yet.
    subprocess.run(
        ("podman", "volume", "create", "--ignore", DATA_VOLUME),
        check=True, stdout=subprocess.DEVNULL,
    )
    output = subprocess.run(
        ("podman", "volume", "inspect", "--format", "{{.Mountpoint}}", DATA_VOLUME),
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if not output or not os.path.isabs(output):
        raise RuntimeError("Podman returned an invalid ntfy-app mountpoint")
    return output


def run_in_user_namespace(script, *arguments, prefix=("podman", "unshare"), check=True):
    return subprocess.run(
        (*prefix, "sh", "-ec", script, "ntfy-backup", *map(str, arguments)),
        check=check,
    )


def service_is_active():
    return subprocess.run(
        ("systemctl", "--user", "is-active", "--quiet", "ntfy.service"),
        check=False,
    ).returncode == 0


def stop_and_verify():
    subprocess.run(
        ("systemctl", "--user", "stop", "ntfy.service"),
        check=True,
    )
    running = subprocess.run(
        ("podman", "ps", "--filter", "name=ntfy-app", "--format", "{{.Names}}"),
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    if "ntfy-app" in running:
        raise RuntimeError("ntfy-app is still running; refusing to copy SQLite files")


def with_ntfy_stopped(operation):
    was_active = service_is_active()
    try:
        stop_and_verify()
        return operation()
    finally:
        if was_active:
            # Restart independently of Restic: NS8 does not call
            # module-cleanup-state if its backup command fails.
            subprocess.run(
                ("systemctl", "--user", "start", "ntfy.service"),
                check=True,
            )


def attachment_directory(config_path):
    """Return the top-level volume entry holding ntfy attachments, or ""."""
    try:
        with open(config_path, "r", encoding="utf-8") as stream:
            content = stream.read()
    except FileNotFoundError:
        return ""
    value = ntfy_config.root_scalar(content, "attachment-cache-dir")
    if not value:
        return ""
    path = os.path.normpath(value)
    prefix = ntfy_config.DATA_MOUNT + "/"
    if not path.startswith(prefix):
        return ""
    name = path[len(prefix):]
    # Only a plain top-level name is linked; it is also used as a find
    # pattern. Any other location is copied like the remaining data.
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", name):
        return ""
    return name


def make_snapshot(state_dir):
    with ntfy_config.state_lock(state_dir):
        mountpoint = volume_mountpoint()
        config_path = os.path.join(state_dir, ntfy_config.CONFIG_PATH)
        attachments = attachment_directory(config_path)
        with_ntfy_stopped(
            lambda: run_in_user_namespace(
                SNAPSHOT_SCRIPT, mountpoint, config_path, attachments
            )
        )


def restore_snapshot(state_dir):
    """Put the snapshot contents back in place.

    Return False if the backup has no snapshot, as the backups of releases
    up to 1.x, which saved only the module environment and passwords.env.
    """
    def restore():
        result = run_in_user_namespace(
            RESTORE_SCRIPT, mountpoint, config_path, check=False)
        if result.returncode == NO_SNAPSHOT:
            return False
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, "restore snapshot")
        return True

    with ntfy_config.state_lock(state_dir):
        mountpoint = volume_mountpoint()
        config_path = os.path.join(state_dir, ntfy_config.CONFIG_PATH)
        # The agent may not be able to traverse subordinate UID directories.
        return with_ntfy_stopped(restore)


def cleanup_snapshot():
    run_in_user_namespace(CLEANUP_SCRIPT, volume_mountpoint())


def move_legacy_data(state_dir):
    """Move data of releases up to 1.x from the state directory to the volume.

    Those releases mounted the state directory itself as /var/lib/ntfy, so
    ntfy wrote its databases and attachments next to the agent files. Only
    the entries named by the data options of server.yml are moved; an entry
    that already exists in the volume is left in place and reported.
    Return the list of moved entries.
    """
    content = ntfy_config.read_server_config()
    names = [name for name in ntfy_config.legacy_data_entries(content)
             if os.path.lexists(os.path.join(state_dir, name))]
    if not names:
        return []
    moved = []
    with ntfy_config.state_lock(state_dir):
        mountpoint = volume_mountpoint()

        def move():
            for name in names:
                target = os.path.join(mountpoint, name)
                if os.path.lexists(target):
                    print(f"<4>{name} exists in the {DATA_VOLUME} volume; "
                          f"the copy in the state directory was not moved", file=sys.stderr)
                    continue
                shutil.move(os.path.join(state_dir, name), target)
                moved.append(name)

        # SQLite files must not be open while they are moved. The service
        # is started again by update-module.d/20restart.
        if service_is_active():
            stop_and_verify()
        move()
    return moved
