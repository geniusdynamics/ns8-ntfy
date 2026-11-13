"""Optional real ntfy CLI/HTTP/SMTP test; set NTFY_TEST_BINARY to the ntfy binary.

Only Podman/systemd are substituted. NS8 lifecycle and task/UI wiring still
require an NS8 test. No credentials or raw command output are printed.
"""
import contextlib
import json
import os
from pathlib import Path
import smtplib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'imageroot/pypkg'))
import ntfy_accounts as accounts
import ntfy_config

BINARY = os.environ.get('NTFY_TEST_BINARY')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(BINARY, 'Set NTFY_TEST_BINARY to the ntfy executable matching NTFY_VERSION in build-images.sh')
class AccountRuntimeTests(unittest.TestCase):
    def test_account_token_acl_and_authenticated_mail_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            http, smtp = free_port(), free_port()
            conf = root / 'server.yml'
            conf.write_text(f'''base-url: http://127.0.0.1:{http}
listen-http: 127.0.0.1:{http}
auth-file: {root / 'auth.db'}
auth-default-access: deny-all
cache-file: {root / 'cache.db'}
smtp-server-listen: 127.0.0.1:{smtp}
smtp-server-domain: push.example.test
x-ns8-disabled-upstream-base-url: https://unused.example.test
''')
            with (root / 'server.log').open('w') as log:
                server = subprocess.Popen([BINARY, 'serve', '--config', str(conf)], stdout=log, stderr=log)
                try:
                    base = f'http://127.0.0.1:{http}'
                    for _ in range(100):
                        try:
                            urlopen(base + '/v1/health', timeout=0.2).close()
                            break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        self.fail('ntfy did not start')
                    run = subprocess.run

                    def boundary(command, **kwargs):
                        if command[0] == 'systemctl':
                            return subprocess.CompletedProcess(command, 0, '', '')
                        self.assertEqual(command[:2], ['podman', 'exec'])
                        index = command.index('ntfy-app')
                        args = command[index + 2:]
                        return run([BINARY, args[0], '--config', str(conf), *args[1:]], **kwargs)

                    def publish(token=None, topic='alerts'):
                        request = Request(base + '/' + topic, data=b'test')
                        if token:
                            request.add_header('Authorization', 'Bearer ' + token)
                        try:
                            with urlopen(request) as response:
                                return response.status
                        except HTTPError as error:
                            return error.code

                    with patch.object(accounts.subprocess, 'run', side_effect=boundary), \
                            patch.object(ntfy_config, 'read_server_config', return_value=conf.read_text()):
                        admin = accounts._manage_account({'operation': 'add-user', 'username': 'admin', 'role': 'admin'})
                        self.assertIn('password', admin)
                        worker = accounts._manage_account({'operation': 'add-user', 'username': 'worker'})
                        self.assertIn('password', worker)
                        accounts._manage_account({'operation': 'set-access', 'username': 'worker', 'topic': 'alerts', 'permission': 'write-only'})
                        token = accounts._manage_account({'operation': 'add-token', 'username': 'worker', 'label': 'monitoring', 'expires': '30d'})['token']
                        listed = accounts.list_accounts()
                        self.assertNotIn(token, json.dumps(listed))
                        self.assertNotIn(worker['password'], json.dumps(listed))
                        self.assertEqual(publish(), 403)
                        self.assertEqual(publish(token), 200)
                        self.assertEqual(publish(token, 'private'), 403)
                        with contextlib.closing(smtplib.SMTP('127.0.0.1', smtp, timeout=5)) as client:
                            with self.assertRaises(smtplib.SMTPDataError):
                                client.sendmail('sender@example.test', 'alerts@push.example.test', 'Subject: blocked\n\nbody')
                        with contextlib.closing(smtplib.SMTP('127.0.0.1', smtp, timeout=5)) as client:
                            self.assertEqual(client.sendmail('sender@example.test', f'alerts+{token}@push.example.test', 'Subject: accepted\n\nbody'), {})
                        with contextlib.closing(smtplib.SMTP('127.0.0.1', smtp, timeout=5)) as client:
                            client.login('worker', worker['password'])
                            self.assertEqual(client.sendmail('sender@example.test', 'alerts@push.example.test', 'Subject: authenticated\n\nbody'), {})
                        accounts._manage_account({'operation': 'reset-password', 'username': 'worker'})
                        self.assertEqual(publish(token), 200)
                        accounts._manage_account({'operation': 'remove-token', 'username': 'worker', 'id': listed['tokens'][0]['id']})
                        self.assertIn(publish(token), (401, 403))
                        accounts._manage_account({'operation': 'remove-access', 'username': 'worker', 'topic': 'alerts'})
                        accounts._manage_account({'operation': 'remove-user', 'username': 'worker'})
                        self.assertEqual([u['username'] for u in accounts.list_accounts()['users'] if u['username'] != '*'], ['admin'])
                finally:
                    server.terminate()
                    server.wait(timeout=10)
