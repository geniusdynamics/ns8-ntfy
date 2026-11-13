import contextlib
import io
import json
import os
import runpy
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONFIGURE_ACTION = (
    REPOSITORY_ROOT
    / "imageroot/actions/configure-module/10configure_environment_vars"
)
GET_CONFIGURATION_ACTION = (
    REPOSITORY_ROOT / "imageroot/actions/get-configuration/20read"
)
DISCOVER_SMARTHOST = REPOSITORY_ROOT / "imageroot/bin/discover-smarthost"
VALIDATE_ACTION = (
    REPOSITORY_ROOT
    / "imageroot/actions/configure-module/02validate_server_config"
)
RESTORE_COPYENV = REPOSITORY_ROOT / "imageroot/actions/restore-module/06copyenv"
STATE_INCLUDE = REPOSITORY_ROOT / "imageroot/etc/state-include.conf"
PYPKG = REPOSITORY_ROOT / "imageroot/pypkg"

sys.path.insert(0, str(PYPKG))
import ntfy_backup  # noqa: E402
import ntfy_config  # noqa: E402

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False
SERVICE_UNIT = REPOSITORY_ROOT / "imageroot/systemd/user/ntfy-app.service"
START_SERVICES = REPOSITORY_ROOT / "imageroot/actions/configure-module/80start_services"


class FakeAgent(types.ModuleType):
    def __init__(self, smarthost=None):
        super().__init__("agent")
        self.unset_variables = []
        self.smarthost = smarthost
        self.status = None
        self.weights = {}
        self.environment = {}
        self.dumped = False

    def read_envfile(self, path):
        values = {}
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                if "=" in line:
                    key, value = line.rstrip("\n").split("=", 1)
                    values[key] = value
        return values

    def unset_env(self, variable):
        self.unset_variables.append(variable)
        os.environ.pop(variable, None)

    def set_env(self, variable, value):
        self.environment[variable] = value

    def dump_env(self):
        self.dumped = True

    def redis_connect(self, **kwargs):
        return kwargs

    def get_smarthost_settings(self, _connection):
        if self.smarthost is None:
            raise AssertionError("Redis must not be queried")
        return self.smarthost

    def set_status(self, status):
        self.status = status

    def set_weight(self, step, weight):
        self.weights[step] = weight


@contextlib.contextmanager
def action_environment(directory, environment=None, stdin="", smarthost=None):
    previous_directory = os.getcwd()
    previous_stdin = sys.stdin
    previous_stdout = sys.stdout
    fake_agent = FakeAgent(smarthost)
    output = io.StringIO()

    with patch.dict(os.environ, environment or {}, clear=True):
        with patch.dict(sys.modules, {"agent": fake_agent}):
            os.chdir(directory)
            sys.stdin = io.StringIO(stdin)
            sys.stdout = output
            try:
                yield fake_agent, output
            finally:
                sys.stdin = previous_stdin
                sys.stdout = previous_stdout
                os.chdir(previous_directory)


class ConfigureActionTests(unittest.TestCase):
    def test_writes_explicit_config_atomically_with_private_permissions(self):
        payload = json.dumps({
            "host": "ntfy.example.org",
            "server_config": "base-url: https://ntfy.example.org",
        })

        with tempfile.TemporaryDirectory() as directory:
            with action_environment(directory, stdin=payload):
                runpy.run_path(str(CONFIGURE_ACTION), run_name="__main__")

            config_path = Path(directory) / "config/server.yml"
            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                "base-url: https://ntfy.example.org\n",
            )
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE(config_path.parent.stat().st_mode),
                0o700,
            )

    def test_writes_defaults_for_a_new_instance(self):
        payload = json.dumps({"host": "ntfy.example.org"})

        with tempfile.TemporaryDirectory() as directory:
            with action_environment(directory, stdin=payload):
                runpy.run_path(str(CONFIGURE_ACTION), run_name="__main__")

            config_path = Path(directory) / "config/server.yml"
            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                ntfy_config.default_config("ntfy.example.org"),
            )
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

    def test_secrets_go_to_passwords_env_not_the_environment(self):
        payload = json.dumps({
            "host": "ntfy.example.org",
            "upstream_access_token": "tk_secret",
            "web_push_private_key": "",
        })

        with tempfile.TemporaryDirectory() as directory:
            with action_environment(directory, stdin=payload) as (agent, _):
                runpy.run_path(str(CONFIGURE_ACTION), run_name="__main__")

            secrets = Path(directory) / "passwords.env"
            self.assertEqual(secrets.read_text(), "NTFY_UPSTREAM_ACCESS_TOKEN=tk_secret\n")
            self.assertEqual(stat.S_IMODE(secrets.stat().st_mode), 0o600)
            self.assertEqual(agent.environment, {})
            self.assertIn("NTFY_UPSTREAM_ACCESS_TOKEN", agent.unset_variables)
            self.assertNotIn("tk_secret", (Path(directory) / "config/server.yml").read_text())

    def test_omitted_server_config_keeps_settings_and_follows_host(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config").mkdir()
            (Path(directory) / "config/server.yml").write_text(
                'base-url: "https://old.example.org"\nauth-file: /var/lib/ntfy/auth.db\n'
                'upstream-access-token: secret\n')
            payload = json.dumps({"host": "new.example.org", "http2https": True, "lets_encrypt": False})
            with action_environment(directory, {"TRAEFIK_HOST": "old.example.org"}, stdin=payload):
                runpy.run_path(str(CONFIGURE_ACTION), run_name="__main__")
            result = ntfy_config.parse_config((Path(directory) / "config/server.yml").read_text())

        self.assertEqual(result["base-url"], "https://new.example.org")
        self.assertEqual(result["auth-file"], "/var/lib/ntfy/auth.db")
        self.assertEqual(result["upstream-access-token"], "secret")

    def test_legacy_input_keys_are_written_to_server_yml(self):
        payload = json.dumps({
            "host": "ntfy.example.org",
            "auth_default_access": "read-only",
            "enable_signup": "false",
            "cache_file": "",
            "upstream_base_url": "https://ntfy.sh",
        })
        with tempfile.TemporaryDirectory() as directory:
            with action_environment(directory, stdin=payload):
                runpy.run_path(str(CONFIGURE_ACTION), run_name="__main__")
            result = ntfy_config.parse_config((Path(directory) / "config/server.yml").read_text())

        self.assertEqual(result["auth-default-access"], "read-only")
        self.assertIs(result["enable-signup"], False)
        self.assertNotIn("cache-file", result)
        self.assertEqual(result["upstream-base-url"], "https://ntfy.sh")

    def test_empty_config_is_supported(self):
        payload = json.dumps({
            "host": "ntfy.example.org",
            "server_config": "",
        })

        with tempfile.TemporaryDirectory() as directory:
            with action_environment(directory, stdin=payload):
                runpy.run_path(str(CONFIGURE_ACTION), run_name="__main__")

            self.assertEqual(
                (Path(directory) / "config/server.yml").read_text(
                    encoding="utf-8"
                ),
                "",
            )


@unittest.skipUnless(HAVE_YAML, "PyYAML is provided by the NS8 agent")
class ValidateServerConfigActionTests(unittest.TestCase):
    def run_validation(self, server_config):
        payload = json.dumps({
            "host": "ntfy.example.org",
            "server_config": server_config,
        })
        with tempfile.TemporaryDirectory() as directory:
            with action_environment(directory, stdin=payload) as (agent, output):
                try:
                    runpy.run_path(str(VALIDATE_ACTION), run_name="__main__")
                except SystemExit as raised:
                    return agent, output.getvalue(), raised.code
                return agent, output.getvalue(), 0

    def test_accepts_mapping_and_empty_config(self):
        for server_config in ("base-url: https://ntfy.example.org\n", ""):
            with self.subTest(server_config=server_config):
                agent, output, code = self.run_validation(server_config)
                self.assertEqual(code, 0)
                self.assertIsNone(agent.status)
                self.assertEqual(output, "")

    def test_rejects_invalid_yaml_and_non_mapping(self):
        for server_config in ("base-url: [unclosed\n", "- a\n- b\n", "just text"):
            with self.subTest(server_config=server_config):
                agent, output, code = self.run_validation(server_config)
                self.assertNotEqual(code, 0)
                self.assertEqual(agent.status, "validation-failed")
                self.assertEqual(
                    json.loads(output)[0]["error"],
                    "invalid_server_config",
                )


class GetConfigurationActionTests(unittest.TestCase):
    def test_returns_server_yml_without_modifying_it(self):
        environment = {
            "TRAEFIK_HOST": "ntfy.example.org",
            "TRAEFIK_HTTP2HTTPS": "True",
            "TRAEFIK_LETS_ENCRYPT": "False",
        }

        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            expected = "base-url: https://ntfy.example.org\n# keep spacing\n"
            (config_directory / "server.yml").write_text(
                expected,
                encoding="utf-8",
            )
            with action_environment(directory, environment) as (_, output):
                runpy.run_path(
                    str(GET_CONFIGURATION_ACTION),
                    run_name="__main__",
                )
                result = json.loads(output.getvalue())

            self.assertEqual(result["server_config"], ntfy_config.editable_config(expected))
            self.assertEqual((config_directory / "server.yml").read_text(), expected)
            self.assertTrue(result["http2https"])
            self.assertFalse(result["lets_encrypt"])
            self.assertFalse(result["auth_enabled"])
            self.assertFalse(result["ios_push"])

    def test_never_returns_secrets(self):
        environment = {"TRAEFIK_HOST": "ntfy.example.org"}
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config").mkdir()
            (Path(directory) / "config/server.yml").write_text(
                'auth-file: /var/lib/ntfy/auth.db\nupstream-base-url: https://ntfy.sh\n'
                'upstream-access-token: tk_secret\nweb-push-private-key: private\n')
            (Path(directory) / "passwords.env").write_text("NTFY_WEB_PUSH_PRIVATE_KEY=private2\n")
            with action_environment(directory, environment) as (_, output):
                runpy.run_path(str(GET_CONFIGURATION_ACTION), run_name="__main__")
                text = output.getvalue()
        result = json.loads(text)
        self.assertTrue(result["auth_enabled"])
        self.assertTrue(result["ios_push"])
        for secret in ("tk_secret", "private", "upstream"):
            self.assertNotIn(secret, text)

    def test_returns_quoted_defaults_without_server_yml(self):
        environment = {"TRAEFIK_HOST": "ntfy.example.org"}

        with tempfile.TemporaryDirectory() as directory:
            with action_environment(directory, environment) as (_, output):
                runpy.run_path(
                    str(GET_CONFIGURATION_ACTION),
                    run_name="__main__",
                )
                result = json.loads(output.getvalue())

        self.assertEqual(
            result["server_config"],
            ntfy_config.editable_config(ntfy_config.default_config("ntfy.example.org")),
        )
        self.assertIn(
            'base-url: https://ntfy.example.org',
            result["server_config"],
        )


class RestoreEnvironmentTests(unittest.TestCase):
    def test_restores_only_the_traefik_settings(self):
        environment = {
            "TRAEFIK_HOST": "ntfy.example.org",
            "TRAEFIK_HTTP2HTTPS": "True",
            "TRAEFIK_LETS_ENCRYPT": "False",
            "NTFY_BASE_URL": "https://other.example.org",
        }
        payload = json.dumps({"environment": environment})

        with tempfile.TemporaryDirectory() as directory:
            with action_environment(directory, stdin=payload) as (agent, _):
                runpy.run_path(str(RESTORE_COPYENV), run_name="__main__")

        self.assertEqual(agent.environment, {
            "TRAEFIK_HOST": "ntfy.example.org",
            "TRAEFIK_HTTP2HTTPS": "True",
            "TRAEFIK_LETS_ENCRYPT": "False",
        })
        self.assertTrue(agent.dumped)


class BackupIncludeTests(unittest.TestCase):
    def test_backup_uses_the_stopped_service_snapshot(self):
        lines = [
            line.strip()
            for line in STATE_INCLUDE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        # NS8 ignores patterns that do not start with state/ or volumes/.
        for line in lines:
            self.assertTrue(line.startswith(("state/", "volumes/")), line)
        self.assertIn(
            "volumes/" + ntfy_backup.DATA_VOLUME + "/.ntfy-backup-snapshot",
            lines,
        )
        self.assertNotIn("volumes/" + ntfy_backup.DATA_VOLUME, lines)
        self.assertNotIn("state/" + ntfy_config.CONFIG_PATH, lines)

    def test_default_data_paths_are_inside_the_data_volume(self):
        config = ntfy_config.default_config("ntfy.example.org")
        for option in ("cache-file", "auth-file", "attachment-cache-dir", "web-push-file"):
            value = ntfy_config.root_scalar(config, option)
            if value:
                self.assertTrue(value.startswith(ntfy_config.DATA_MOUNT + "/"))


class DiscoverSmarthostTests(unittest.TestCase):
    def test_writes_compatible_smarthost_as_private_ntfy_environment(self):
        smarthost = {
            "enabled": True,
            "host": "smtp.example.org",
            "port": 587,
            "username": "ntfy@example.org",
            "password": "secret=value",
            "encrypt_smtp": "starttls",
            "tls_verify": True,
        }

        with tempfile.TemporaryDirectory() as directory:
            environment = {"TRAEFIK_HOST": "ntfy.example.org"}
            with action_environment(directory, environment, smarthost=smarthost):
                runpy.run_path(str(DISCOVER_SMARTHOST), run_name="__main__")

            env_path = Path(directory) / "smarthost.env"
            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "NTFY_SMTP_SENDER_ADDR=smtp.example.org:587\n"
                "NTFY_SMTP_SENDER_FROM=ntfy@example.org\n"
                "NTFY_SMTP_SENDER_PASS=secret=value\n"
                "NTFY_SMTP_SENDER_USER=ntfy@example.org\n",
            )
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

    def test_preserves_explicit_sender_from_in_server_yml(self):
        smarthost = {
            "enabled": True,
            "host": "smtp.example.org",
            "port": 25,
            "username": "relay-user",
            "password": "secret",
            "encrypt_smtp": "none",
            "tls_verify": True,
        }

        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            (config_directory / "server.yml").write_text(
                'smtp-sender-from: "alerts@example.org"\n',
                encoding="utf-8",
            )
            with action_environment(directory, smarthost=smarthost):
                runpy.run_path(str(DISCOVER_SMARTHOST), run_name="__main__")

            environment_file = (Path(directory) / "smarthost.env").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("NTFY_SMTP_SENDER_FROM", environment_file)

    def test_uses_public_host_when_smtp_username_is_not_an_email(self):
        smarthost = {
            "enabled": True,
            "host": "smtp.example.org",
            "port": 25,
            "username": "relay-user",
            "password": "secret",
            "encrypt_smtp": "none",
            "tls_verify": True,
        }

        with tempfile.TemporaryDirectory() as directory:
            environment = {"TRAEFIK_HOST": "ntfy.example.org"}
            with action_environment(directory, environment, smarthost=smarthost):
                runpy.run_path(str(DISCOVER_SMARTHOST), run_name="__main__")

            environment_file = (Path(directory) / "smarthost.env").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "NTFY_SMTP_SENDER_FROM=no-reply@ntfy.example.org\n",
                environment_file,
            )

    def test_skips_disabled_or_implicit_tls_smarthost(self):
        variants = (
            {
                "enabled": False,
                "host": "smtp.example.org",
                "port": 587,
                "username": "",
                "password": "",
                "encrypt_smtp": "starttls",
                "tls_verify": True,
            },
            {
                "enabled": True,
                "host": "smtp.example.org",
                "port": 465,
                "username": "user",
                "password": "secret",
                "encrypt_smtp": "tls",
                "tls_verify": True,
            },
        )

        for smarthost in variants:
            with self.subTest(smarthost=smarthost):
                with tempfile.TemporaryDirectory() as directory:
                    with action_environment(directory, smarthost=smarthost):
                        with self.assertRaises(SystemExit) as raised:
                            runpy.run_path(
                                str(DISCOVER_SMARTHOST),
                                run_name="__main__",
                            )
                    self.assertEqual(raised.exception.code, 0)
                    self.assertEqual(
                        (Path(directory) / "smarthost.env").read_text(
                            encoding="utf-8"
                        ),
                        "",
                    )


    def test_server_yml_smtp_relay_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            (config_directory / "server.yml").write_text(
                'smtp-sender-addr: "mail.example.org:587"\n',
                encoding="utf-8",
            )
            # smarthost=None makes the fake agent fail if Redis is queried
            with action_environment(directory):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(str(DISCOVER_SMARTHOST), run_name="__main__")

            self.assertEqual(raised.exception.code, 0)
            self.assertEqual(
                (Path(directory) / "smarthost.env").read_text(encoding="utf-8"),
                "",
            )

    def test_empty_smtp_sender_addr_does_not_disable_smarthost(self):
        smarthost = {
            "enabled": True,
            "host": "smtp.example.org",
            "port": 587,
            "encrypt_smtp": "starttls",
        }

        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            (config_directory / "server.yml").write_text(
                'smtp-sender-addr:\nsmtp-sender-from: ""\n',
                encoding="utf-8",
            )
            environment = {"TRAEFIK_HOST": "ntfy.example.org"}
            with action_environment(directory, environment, smarthost=smarthost):
                runpy.run_path(str(DISCOVER_SMARTHOST), run_name="__main__")

            environment_file = (Path(directory) / "smarthost.env").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "NTFY_SMTP_SENDER_ADDR=smtp.example.org:587\n",
                environment_file,
            )
            self.assertIn(
                "NTFY_SMTP_SENDER_FROM=no-reply@ntfy.example.org\n",
                environment_file,
            )

    def test_nested_sender_from_is_not_a_root_key(self):
        smarthost = {
            "enabled": True,
            "host": "smtp.example.org",
            "username": "relay-user",
            "encrypt_smtp": "none",
        }

        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            (config_directory / "server.yml").write_text(
                'other:\n  smtp-sender-from: "nested@example.org"\n',
                encoding="utf-8",
            )
            environment = {"TRAEFIK_HOST": "ntfy.example.org"}
            with action_environment(directory, environment, smarthost=smarthost):
                runpy.run_path(str(DISCOVER_SMARTHOST), run_name="__main__")

            environment_file = (Path(directory) / "smarthost.env").read_text(
                encoding="utf-8"
            )
            # a missing port falls back to the SMTP default
            self.assertIn(
                "NTFY_SMTP_SENDER_ADDR=smtp.example.org:25\n",
                environment_file,
            )
            self.assertIn(
                "NTFY_SMTP_SENDER_FROM=no-reply@ntfy.example.org\n",
                environment_file,
            )


class ServiceUnitTests(unittest.TestCase):
    def test_data_volume_and_config_mounts_have_private_selinux_labels(self):
        unit = SERVICE_UNIT.read_text(encoding="utf-8")

        self.assertIn(
            f"--volume {ntfy_backup.DATA_VOLUME}:{ntfy_config.DATA_MOUNT}:Z",
            unit,
        )
        self.assertIn("--volume ./config:/etc/ntfy:ro,Z", unit)
        # Secrets stay in passwords.env, read with the documented EnvironmentFile
        self.assertIn("EnvironmentFile=-%S/state/passwords.env", unit)
        self.assertIn("RestartSec=10", unit)
        # The old mapping enabled ntfy's SMTP server on the smarthost port
        self.assertNotIn("SMTP_SERVER", unit)
        # The state directory holds agent files and must not be exposed
        self.assertNotIn("--volume ./:", unit)


class StartServicesActionTests(unittest.TestCase):
    def test_clears_start_limit_before_restarting_services(self):
        script = START_SERVICES.read_text(encoding="utf-8")

        reset = "systemctl --user reset-failed ntfy.service ntfy-app.service"
        restart = "systemctl --user restart ntfy-app.service"
        self.assertIn(reset, script)
        self.assertLess(script.index(reset), script.index(restart))


if __name__ == "__main__":
    unittest.main()
