"""Update and restore compatibility with releases up to 1.x.

Those releases kept the ntfy settings in NTFY_* variables of the module
environment (release 1.0.0 wrote "None" for missing inputs), the secrets in
the environment or in passwords.env, and the ntfy data in the state
directory, which they mounted as /var/lib/ntfy.
"""

import json
import os
import runpy
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "imageroot/pypkg"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ntfy_backup  # noqa: E402
import ntfy_config  # noqa: E402
from test_config_actions import FakeAgent, action_environment  # noqa: E402

UPDATE = ROOT / "imageroot/update-module.d"
RESTORE_SNAPSHOT = ROOT / "imageroot/actions/restore-module/30restore_data_snapshot"

# configure-module of release 1.0.0 run from the UI (host, http2https and
# lets_encrypt only): ntfy could not start with these values.
RELEASE_100_UI = {
    "TRAEFIK_HOST": "ntfy.example.org",
    "NTFY_BASE_URL": "",
    "NTFY_AUTH_DEFAULT_ACCESS": "",
    "NTFY_BEHIND_PROXY": "None",
    "NTFY_ENABLE_LOGIN": "None",
    "NTFY_ENABLE_SIGNUP": "None",
    "NTFY_UPSTREAM_BASE_URE": "None",
    "NTFY_UPSTREAM_ACCESS_TOKEN": "None",
    "NTFY_WEB_PUSH_EMAIL_ADDRESS": "None",
    "NTFY_CACHE_FILE": "None",
    "NTFY_ATTACHMENT_CACHE_DIR": "None",
    "NTFY_AUTH_FILE": "None",
    "NTFY_WEB_PUSH_FILE": "None",
    "NTFY_WEB_PUSH_PUBLIC_KEY": "None",
    "NTFY_WEB_PUSH_PRIVATE_KEY": "None",
}

# configure-module called through the API with every setting
WORKING_API = {
    "TRAEFIK_HOST": "ntfy.example.org",
    "NTFY_BASE_URL": "https://push.example.org",
    "NTFY_AUTH_DEFAULT_ACCESS": "read-only",
    "NTFY_BEHIND_PROXY": "true",
    "NTFY_ENABLE_LOGIN": "true",
    "NTFY_ENABLE_SIGNUP": "false",
    "NTFY_UPSTREAM_BASE_URE": "https://ntfy.sh",
    "NTFY_WEB_PUSH_EMAIL_ADDRESS": "admin@example.org",
    "NTFY_CACHE_FILE": "/var/lib/ntfy/cache.db",
    "NTFY_ATTACHMENT_CACHE_DIR": "/var/lib/ntfy/attachments",
    "NTFY_AUTH_FILE": "/var/lib/ntfy/auth.db",
    "NTFY_WEB_PUSH_FILE": "",
}


def write_environment(directory, values):
    with open(Path(directory) / "environment", "w", encoding="utf-8") as stream:
        for key in sorted(values):
            stream.write(f"{key}={values[key]}\n")


class EnvironmentFileAgent(FakeAgent):
    """unset_env() edits the environment file, as the NS8 agent does."""

    def unset_env(self, variable):
        super().unset_env(variable)
        path = Path(os.environ["AGENT_STATE_DIR"]) / "environment"
        values = self.read_envfile(path)
        values.pop(variable, None)
        write_environment(path.parent, values)


def run_update_step(directory, name):
    agent = EnvironmentFileAgent()
    with action_environment(directory, {"AGENT_STATE_DIR": directory}):
        with patch.dict(sys.modules, {"agent": agent}):
            runpy.run_path(str(UPDATE / name), run_name="__main__")
    return agent


class LegacyConfigurationTests(unittest.TestCase):
    def test_instance_that_could_not_start_gets_new_defaults(self):
        content = ntfy_config.config_from_legacy_environment(RELEASE_100_UI, "ntfy.example.org")
        self.assertEqual(
            ntfy_config.parse_config(content),
            ntfy_config.parse_config(ntfy_config.default_config("ntfy.example.org")),
        )
        self.assertNotIn("None", content)

    def test_empty_auth_default_access_could_not_start(self):
        self.assertFalse(ntfy_config.legacy_environment_was_usable(
            {"NTFY_AUTH_DEFAULT_ACCESS": "", "NTFY_ENABLE_LOGIN": "false"}))
        self.assertFalse(ntfy_config.legacy_environment_was_usable(
            {"NTFY_AUTH_DEFAULT_ACCESS": "read-write", "NTFY_ENABLE_LOGIN": "true"}))
        self.assertTrue(ntfy_config.legacy_environment_was_usable(
            {"NTFY_AUTH_DEFAULT_ACCESS": "read-write", "NTFY_ENABLE_LOGIN": "false",
             "NTFY_BEHIND_PROXY": "True"}))

    def test_working_instance_keeps_its_settings(self):
        config = ntfy_config.parse_config(
            ntfy_config.config_from_legacy_environment(WORKING_API, "ntfy.example.org"))
        self.assertEqual(config, {
            "base-url": "https://push.example.org",
            "auth-default-access": "read-only",
            "enable-login": True,
            "enable-signup": False,
            "web-push-email-address": "admin@example.org",
            "cache-file": "/var/lib/ntfy/cache.db",
            "attachment-cache-dir": "/var/lib/ntfy/attachments",
            "auth-file": "/var/lib/ntfy/auth.db",
            # The misspelled variable was never effective: keep it switched off
            "x-ns8-disabled-upstream-base-url": "https://ntfy.sh",
        })
        # The editor never shows the module metadata or behind-proxy
        self.assertNotIn("x-ns8", ntfy_config.editable_config(ntfy_config.dump_config(config)))

    def test_working_instance_without_base_url_gets_the_host(self):
        environment = {"NTFY_AUTH_DEFAULT_ACCESS": "read-write", "NTFY_BASE_URL": ""}
        config = ntfy_config.parse_config(
            ntfy_config.config_from_legacy_environment(environment, "ntfy.example.org"))
        self.assertEqual(config, {"base-url": "https://ntfy.example.org",
                                  "auth-default-access": "read-write"})

    def test_ios_switch_enables_the_saved_upstream(self):
        content = ntfy_config.config_from_legacy_environment(WORKING_API, "ntfy.example.org")
        enabled = ntfy_config.parse_config(ntfy_config.merge_configuration(
            ntfy_config.editable_config(content), content, True))
        self.assertEqual(enabled["upstream-base-url"], "https://ntfy.sh")

    def test_legacy_data_entries(self):
        content = ("cache-file: /var/lib/ntfy/cache.db\n"
                   "auth-file: /var/lib/ntfy/db/auth.db\n"
                   "web-push-file: /var/lib/ntfy/environment\n"
                   "attachment-cache-dir: /var/lib/ntfy/attachments\n")
        self.assertEqual(ntfy_config.legacy_data_entries(content), [
            "cache.db", "cache.db-wal", "cache.db-shm", "cache.db-journal",
            "db", "attachments",
        ])
        self.assertEqual(ntfy_config.legacy_data_entries("cache-file: /srv/cache.db\n"), [])

    def test_invalid_legacy_input_is_rejected(self):
        for data in ({"auth_default_access": "everything"}, {"base_url": "ftp://x"},
                     {"upstream_base_url": "ntfy.sh"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                ntfy_config.apply_legacy_options("", data)


class SecretsFileTests(unittest.TestCase):
    def test_empty_and_none_values_are_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "passwords.env"
            path.write_text("NTFY_WEB_PUSH_PUBLIC_KEY=\nNTFY_WEB_PUSH_PRIVATE_KEY=None\n"
                            "NTFY_UPSTREAM_ACCESS_TOKEN=tk_keep=1\n")
            ntfy_config.write_secrets({}, directory)
            self.assertEqual(path.read_text(), "NTFY_UPSTREAM_ACCESS_TOKEN=tk_keep=1\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            ntfy_config.write_secrets({"NTFY_UPSTREAM_ACCESS_TOKEN": ""}, directory)
            self.assertEqual(path.read_text(), "")

    def test_newlines_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                ntfy_config.write_secrets({"NTFY_UPSTREAM_ACCESS_TOKEN": "a\nNTFY_X=1"}, directory)


class UpdateStepTests(unittest.TestCase):
    def test_update_from_release_100(self):
        with tempfile.TemporaryDirectory() as directory:
            write_environment(directory, {**RELEASE_100_UI, "MODULE_ID": "ntfy1"})
            run_update_step(directory, "10move_secrets")
            run_update_step(directory, "15server_config")

            environment = FakeAgent().read_envfile(Path(directory) / "environment")
            self.assertEqual(sorted(environment), ["MODULE_ID", "TRAEFIK_HOST"])
            # "None" secrets would enable Web Push and stop ntfy
            self.assertEqual((Path(directory) / "passwords.env").read_text(), "")
            config = Path(directory) / "config/server.yml"
            self.assertEqual(
                ntfy_config.parse_config(config.read_text()),
                ntfy_config.parse_config(ntfy_config.default_config("ntfy.example.org")),
            )
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(config.parent.stat().st_mode), 0o700)

    def test_update_moves_secrets_and_keeps_working_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            write_environment(directory, {**WORKING_API, "NTFY_UPSTREAM_ACCESS_TOKEN": "tk_abc"})
            run_update_step(directory, "10move_secrets")
            run_update_step(directory, "15server_config")
            self.assertEqual((Path(directory) / "passwords.env").read_text(),
                             "NTFY_UPSTREAM_ACCESS_TOKEN=tk_abc\n")
            environment = (Path(directory) / "environment").read_text()
            self.assertEqual(environment, "TRAEFIK_HOST=ntfy.example.org\n")
            config = ntfy_config.parse_config((Path(directory) / "config/server.yml").read_text())
            self.assertEqual(config["auth-default-access"], "read-only")

            # Running the steps again changes nothing
            before = (Path(directory) / "config/server.yml").read_text()
            run_update_step(directory, "10move_secrets")
            run_update_step(directory, "15server_config")
            self.assertEqual((Path(directory) / "config/server.yml").read_text(), before)
            self.assertEqual((Path(directory) / "passwords.env").read_text(),
                             "NTFY_UPSTREAM_ACCESS_TOKEN=tk_abc\n")

    def test_existing_server_yml_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            write_environment(directory, WORKING_API)
            (Path(directory) / "config").mkdir()
            (Path(directory) / "config/server.yml").write_text("enable-login: true\n")
            run_update_step(directory, "15server_config")
            self.assertEqual((Path(directory) / "config/server.yml").read_text(),
                             "enable-login: true\n")
            self.assertNotIn("NTFY_", (Path(directory) / "environment").read_text())

    def test_unconfigured_instance_gets_no_server_yml(self):
        with tempfile.TemporaryDirectory() as directory:
            write_environment(directory, {"MODULE_ID": "ntfy1"})
            run_update_step(directory, "15server_config")
            self.assertFalse((Path(directory) / "config/server.yml").exists())

    def test_data_moves_from_the_state_directory_to_the_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            volume = Path(directory) / "volume"
            state.mkdir()
            volume.mkdir()
            (state / "config").mkdir()
            (state / "config/server.yml").write_text(
                ntfy_config.config_from_legacy_environment(WORKING_API, "ntfy.example.org"))
            for name, content in (("cache.db", "messages"), ("cache.db-wal", "wal"),
                                  ("auth.db", "users"), ("agent.env", "REDIS_PASSWORD=x"),
                                  ("environment", "MODULE_ID=ntfy1")):
                (state / name).write_text(content)
            (state / "attachments").mkdir()
            (state / "attachments/file").write_text("attachment")
            (volume / "auth.db").write_text("newer")
            calls = []

            with patch.object(ntfy_backup, "volume_mountpoint", return_value=str(volume)), \
                    patch.object(ntfy_backup, "service_is_active", return_value=True), \
                    patch.object(ntfy_backup, "stop_and_verify", side_effect=lambda: calls.append("stop")):
                run_update_step(str(state), "16data_volume")

            self.assertEqual(calls, ["stop"])
            self.assertEqual((volume / "cache.db").read_text(), "messages")
            self.assertEqual((volume / "cache.db-wal").read_text(), "wal")
            self.assertEqual((volume / "attachments/file").read_text(), "attachment")
            self.assertFalse((state / "cache.db").exists())
            # Never overwrite data in the volume, never move agent files
            self.assertEqual((volume / "auth.db").read_text(), "newer")
            self.assertEqual((state / "auth.db").read_text(), "users")
            self.assertTrue((state / "agent.env").exists())
            self.assertTrue((state / "environment").exists())
            self.assertTrue((state / "config/server.yml").exists())

    def test_no_data_means_no_service_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            write_environment(directory, RELEASE_100_UI)
            run_update_step(directory, "15server_config")
            with patch.object(ntfy_backup, "volume_mountpoint") as mountpoint, \
                    patch.object(ntfy_backup, "stop_and_verify") as stop:
                run_update_step(directory, "16data_volume")
            mountpoint.assert_not_called()
            stop.assert_not_called()


class RestoreTests(unittest.TestCase):
    def run_restore(self, directory, environment, has_snapshot):
        payload = json.dumps({"environment": environment})
        with patch.object(ntfy_backup, "restore_snapshot", return_value=has_snapshot):
            with action_environment(directory, {"AGENT_STATE_DIR": directory}, stdin=payload):
                runpy.run_path(str(RESTORE_SNAPSHOT), run_name="__main__")

    def test_backup_of_release_100_is_accepted(self):
        environment = {**WORKING_API, "NTFY_WEB_PUSH_PRIVATE_KEY": "private",
                       "NTFY_WEB_PUSH_PUBLIC_KEY": "None"}
        with tempfile.TemporaryDirectory() as directory:
            self.run_restore(directory, environment, has_snapshot=False)
            config = ntfy_config.parse_config((Path(directory) / "config/server.yml").read_text())
            self.assertEqual(config["auth-default-access"], "read-only")
            self.assertEqual((Path(directory) / "passwords.env").read_text(),
                             "NTFY_WEB_PUSH_PRIVATE_KEY=private\n")

    def test_restored_passwords_env_takes_precedence(self):
        environment = {**RELEASE_100_UI, "NTFY_UPSTREAM_ACCESS_TOKEN": "old"}
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "passwords.env").write_text("NTFY_UPSTREAM_ACCESS_TOKEN=new\n")
            self.run_restore(directory, environment, has_snapshot=False)
            self.assertEqual((Path(directory) / "passwords.env").read_text(),
                             "NTFY_UPSTREAM_ACCESS_TOKEN=new\n")

    def test_snapshot_server_yml_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config").mkdir()
            (Path(directory) / "config/server.yml").write_text("enable-login: true\n")
            self.run_restore(directory, {"TRAEFIK_HOST": "ntfy.example.org"}, has_snapshot=True)
            self.assertEqual((Path(directory) / "config/server.yml").read_text(),
                             "enable-login: true\n")


if __name__ == "__main__":
    unittest.main()
