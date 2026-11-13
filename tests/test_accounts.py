import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'imageroot/pypkg'))
import ntfy_accounts as accounts
import ntfy_config as config

USERS = '''user admin (role: admin, tier: none)
- read-write access to all topics (admin role)
user worker (role: user, tier: none)
- write-only access to topic alerts
user declarative (role: user, tier: none, server config)
- read-only access to topic reports (server config)
user * (role: anonymous, tier: none)
- no access to any (other) topics (server config)
'''
TOKEN = 'tk_' + 'a' * 29
TOKENS = f'''user worker
- {TOKEN} (monitoring), never expires, accessed from 127.0.0.1 at 01 Jan 26 12:00 UTC
'''


class ConfigProtectionTests(unittest.TestCase):
    def test_secrets_hidden_but_preserved_on_save(self):
        source = '''# upstream token in an old comment: secret-in-comment
base-url: https://ntfy.example.org
upstream-base-url: https://custom-upstream.example.org
upstream-access-token: secret-upstream
smtp-sender-pass: secret-mail
smtp-sender-addr: smtp.example.org:587
auth-users: ['admin:secret-hash:admin']
auth-tokens: ['admin:secret-token']
auth-access: ['admin:topic:rw']
web-push-private-key: secret-vapid
'''
        public = config.editable_config(source)
        self.assertNotIn('secret', public)
        self.assertNotIn('smtp-sender', public)
        self.assertNotIn('upstream', public)
        self.assertNotIn('auth-users', public)
        merged = config.parse_config(config.merge_configuration(public + 'enable-login: true\n', source))
        previous = config.parse_config(source)
        for key, value in previous.items():
            self.assertEqual(merged[key], value)
        self.assertTrue(merged['enable-login'])

    def test_reject_duplicates_merges_aliases_and_managed_overrides(self):
        for source in [
            'enable-login: true\nenable-login: false',
            'x: {a: 1, a: 2}',
            'base-url: a\n"base-url": b',
            '<<: {enable-login: true}',
            'x: &x 1\ny: *x',
            'upstream-access-token: secret',
            'smtp-sender-addr: smtp.example.org',
            'auth-users: []',
            'behind-proxy: false',
            'listen-http: :8080',
            'web-push-public-key: BPublic',
            'smtp_sender_pass: secret',
            'enable_login: false',
            'x: [unclosed secret',
        ]:
            with self.subTest(source=source):
                error = config.validate_server_config(source)
                self.assertIsNotNone(error)
                self.assertNotIn('secret', error)

    def test_ios_preserves_custom_upstream_and_token(self):
        existing = 'upstream-base-url: https://custom.example.org\nupstream-access-token: secret'
        enabled = config.parse_config(config.merge_configuration('', existing, True))
        self.assertEqual(enabled['upstream-base-url'], 'https://custom.example.org')
        disabled = config.parse_config(config.merge_configuration('', existing, False))
        self.assertNotIn('upstream-base-url', disabled)
        self.assertEqual(disabled['upstream-access-token'], 'secret')
        reenabled = config.parse_config(config.merge_configuration('', config.dump_config(disabled), True))
        self.assertEqual(reenabled['upstream-base-url'], 'https://custom.example.org')
        self.assertEqual(reenabled['upstream-access-token'], 'secret')
        self.assertNotIn('x-ns8-disabled-upstream-base-url', reenabled)
        self.assertNotIn('custom.example.org', config.editable_config(config.dump_config(disabled)))
        self.assertIsNotNone(config.validate_server_config('x-ns8-disabled-upstream-base-url: https://wrong.example.org'))
        fresh = config.parse_config(config.merge_configuration('', '', True))
        self.assertEqual(fresh['upstream-base-url'], 'https://ntfy.sh')


class StateLockTests(unittest.TestCase):
    def test_lock_excludes_concurrent_operations_and_releases_on_error(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'test-error'):
                with config.state_lock(directory):
                    with open(Path(directory) / '.ntfy-admin.lock', 'w') as other:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    raise RuntimeError('test-error')
            with open(Path(directory) / '.ntfy-admin.lock', 'w') as other:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual((Path(directory) / '.ntfy-admin.lock').stat().st_mode & 0o777, 0o600)


class AccountTests(unittest.TestCase):
    def setUp(self):
        # Account actions read server.yml to check that auth is enabled
        patcher = patch.object(config, 'read_server_config', return_value='auth-file: "/var/lib/ntfy/auth.db"\n')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_auth_disabled_is_reported_without_calling_the_cli(self):
        with patch.object(config, 'read_server_config', return_value='base-url: https://ntfy.example.org\n'), \
                patch.object(accounts, 'cli') as cli:
            self.assertEqual(accounts.list_accounts(), {'auth_enabled': False, 'users': [], 'tokens': []})
            with self.assertRaisesRegex(accounts.AccountError, '^auth_not_configured$'):
                accounts._manage_account({'operation': 'add-user', 'username': 'newuser'})
        cli.assert_not_called()

    def test_lists_never_contain_tokens_or_passwords(self):
        with patch.object(accounts, 'cli', side_effect=[USERS, TOKENS]):
            result = accounts.list_accounts()
        self.assertTrue(result['auth_enabled'])
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn('password', json.dumps(result))
        self.assertEqual(result['tokens'][0]['id'], accounts.token_id(TOKEN))
        self.assertEqual(result['users'][2]['access'][0]['provisioned'], True)
        self.assertEqual(result['users'][1]['access'][0]['permission'], 'write-only')

    def test_provisioned_token_is_marked(self):
        output = TOKENS.rstrip() + ' (server config)\n'
        self.assertTrue(accounts.parse_tokens(output)[0]['provisioned'])

    def test_unknown_output_fails_closed_without_echoing_token(self):
        with self.assertRaisesRegex(accounts.AccountError, '^account_output_invalid$'):
            accounts.parse_tokens(TOKEN)

    def test_unsafe_input_never_reaches_cli(self):
        for data in [
            {'operation': 'add-user', 'username': '--role=admin'},
            {'operation': 'add-user', 'username': 'x; echo secret'},
            {'operation': 'set-access', 'username': 'worker', 'topic': '--reset', 'permission': 'rw'},
            {'operation': 'add-token', 'username': 'worker', 'label': 'x\ny'},
            {'operation': 'remove-token', 'username': 'worker', 'id': TOKEN},
        ]:
            with self.subTest(data=data), patch.object(accounts, 'cli') as cli:
                with self.assertRaises(accounts.AccountError):
                    accounts._manage_account(data)
                cli.assert_not_called()

    def test_last_admin_and_provisioned_account_are_protected(self):
        for operation, username, error in [('remove-user', 'admin', 'last_admin'), ('reset-password', 'declarative', 'provisioned_account')]:
            with patch.object(accounts, 'cli', return_value=USERS) as cli:
                with self.assertRaisesRegex(accounts.AccountError, error):
                    accounts._manage_account({'operation': operation, 'username': username})
                self.assertEqual(cli.call_count, 1)

    def test_generated_password_only_in_creation_result(self):
        with patch.object(accounts, 'cli', side_effect=[USERS, '']) as cli, patch.object(accounts.subprocess, 'run'):
            result = accounts._manage_account({'operation': 'add-user', 'username': 'newuser'})
        self.assertGreaterEqual(len(result['password']), 24)
        self.assertEqual(cli.call_args.kwargs['password'], result['password'])
        self.assertNotIn(result['password'], cli.call_args.args)

    def test_cli_errors_do_not_disclose_command_output(self):
        failure = subprocess.CompletedProcess([], 1, TOKEN, 'secret-password')
        with patch.object(accounts.subprocess, 'run', return_value=failure):
            with self.assertRaisesRegex(accounts.AccountError, '^account_command_failed$'):
                accounts.cli('token', 'list')

    def test_revoke_uses_opaque_id_and_checks_owner(self):
        with patch.object(accounts, 'cli', side_effect=[USERS, TOKENS, '']) as cli, patch.object(accounts.subprocess, 'run'):
            accounts._manage_account({'operation': 'remove-token', 'username': 'worker', 'id': accounts.token_id(TOKEN)})
        self.assertEqual(cli.call_args.args, ('token', 'remove', 'worker', TOKEN))
        with patch.object(accounts, 'cli', side_effect=[USERS, TOKENS]):
            with self.assertRaisesRegex(accounts.AccountError, 'token_not_found'):
                accounts._manage_account({'operation': 'remove-token', 'username': 'admin', 'id': accounts.token_id(TOKEN)})

    def test_restart_failure_still_returns_new_credential(self):
        with patch.object(accounts, 'cli', side_effect=[USERS, '']), patch.object(accounts.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['systemctl'])):
            result = accounts._manage_account({'operation': 'reset-password', 'username': 'worker'})
        self.assertTrue(result['restart_failed'])
        self.assertIn('password', result)

    def test_token_limit_does_not_silently_prune_existing_tokens(self):
        with patch.object(accounts, 'cli', side_effect=[USERS, TOKENS]), patch.object(accounts, 'parse_tokens', return_value=[{'username': 'worker'}] * 60):
            with self.assertRaisesRegex(accounts.AccountError, 'token_limit'):
                accounts._manage_account({'operation': 'add-token', 'username': 'worker'})
