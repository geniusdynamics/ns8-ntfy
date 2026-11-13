# ns8-ntfy

This is a NethServer 8 module for [ntfy](https://github.com/binwiederhier/ntfy)
(<https://ntfy.sh/>).

ntfy (pronounced notify) is a simple HTTP-based pub-sub notification service.
It allows you to send notifications to your phone or desktop via scripts from
any computer, and/or using a REST API. It's infinitely flexible, and 100% free
software.

The module provides a Traefik route, user/token/topic administration, an
editor for the ntfy settings that keeps secrets out of the UI, backup and
restore of the ntfy data, and outgoing mail through the NS8 smarthost.

Administration features adapted from
[Platypuschan/ns8-ntfy-reworked](https://github.com/Platypuschan/ns8-ntfy-reworked),
GPL-3.0. See [administration details](docs/administration.md).

## Install

Instantiate the module with:

    add-module ghcr.io/geniusdynamics/ntfy:latest 1

The output of the command will return the instance name.
Output example:

    {"module_id": "ntfy1", "image_name": "ntfy", "image_url": "ghcr.io/geniusdynamics/ntfy:latest"}

## Configure

Let's assume that the ntfy instance is named `ntfy1`.

Launch `configure-module`, by setting the following parameters:
- `host`: a fully qualified domain name for the application
- `http2https`: enable or disable HTTP to HTTPS redirection (true/false)
- `lets_encrypt`: enable or disable Let's Encrypt certificate (true/false)
- `server_config`: optional, the editable ntfy settings as YAML
  ([options](https://docs.ntfy.sh/config/#config-options)); omitted, the
  current settings are kept
- `ios_push`: optional, forward poll requests to ntfy.sh (or the previous
  upstream) for iOS instant notifications; omitted, the current state is kept
- `upstream_access_token`, `web_push_public_key`, `web_push_private_key`:
  optional secrets, stored in `state/passwords.env` (mode 0600); an empty
  value removes the secret, an omitted one keeps it

Example:

```
api-cli run configure-module --agent module/ntfy1 --data - <<EOF
{
  "host": "ntfy.domain.com",
  "http2https": true,
  "lets_encrypt": false
}
EOF
```

The above command will:
- start and configure the ntfy instance
- configure a virtual host for Traefik to access the instance

A new instance starts with this `server.yml`: private topics (`deny-all`),
login enabled, signup disabled, a message cache and an attachment directory.

```yaml
base-url: "https://ntfy.domain.com"
cache-file: "/var/lib/ntfy/cache.db"
attachment-cache-dir: "/var/lib/ntfy/attachments"
auth-file: "/var/lib/ntfy/auth.db"
auth-default-access: "deny-all"
enable-login: true
enable-signup: false
```

The Settings page edits it under **Advanced**. It shows a filtered view of
`state/config/server.yml`: SMTP sender settings, upstream options, declarative
auth entries, private keys and other secrets are hidden and preserved when
saving, and cannot be added from the editor. `listen-http`, `behind-proxy` and
`proxy-forwarded-header` are enforced by the module, since ntfy is reached
only through Traefik. Duplicate keys, YAML aliases and merge keys are
rejected. Keep `cache-file`, `auth-file`, `web-push-file` and
`attachment-cache-dir` below `/var/lib/ntfy`, otherwise the data is neither
persistent nor backed up. Invalid ntfy option values are only detected by
ntfy itself: check the service log after changing advanced settings.

Configure from the command line with a local `server.yml`:

```bash
jq -n --arg host ntfy.domain.com --arg server_config "$(<server.yml)" \
  '{host: $host, http2https: true, lets_encrypt: false, server_config: $server_config}' |
api-cli run configure-module --agent module/ntfy1 --data -
```

The settings keys of older releases (`base_url`, `auth_default_access`,
`enable_login`, `enable_signup`, `upstream_base_url`,
`web_push_email_address`, `cache_file`, `attachment_cache_dir`, `auth_file`,
`web_push_file`) are still accepted and update the matching `server.yml`
options.

## Get the configuration

You can retrieve the configuration with

```
api-cli run get-configuration --agent module/ntfy1
```

It returns the route settings, the filtered `server_config`, `ios_push` and
`auth_enabled`. Secrets are never returned.

## Users, tokens and topic access

Open **Users and tokens** in the module UI. It lists users and roles, tokens
(label and expiry only) and topic permissions. It creates users with
generated passwords, generates replacement passwords, creates and revokes
tokens, and grants or removes topic permissions. A new password or token is
shown once, in the creation result. Existing passwords and tokens are never
returned. The last administrator cannot be deleted. Account changes briefly
restart ntfy. The page needs `auth-file` in `server.yml`.

The same actions are available from the command line:

```
api-cli run list-accounts --agent module/ntfy1
api-cli run manage-account --agent module/ntfy1 --data '{"operation":"add-user","username":"alice","role":"user"}'
api-cli run manage-account --agent module/ntfy1 --data '{"operation":"set-access","username":"alice","topic":"alerts","permission":"read-write"}'
api-cli run manage-account --agent module/ntfy1 --data '{"operation":"add-token","username":"alice","label":"monitoring","expires":"never"}'
```

Tokens inherit their user's permissions; an ntfy administrator can access all
topics. Changing a password does not revoke existing tokens. NS8 keeps task
results, so do not share the result of a creation task.

## Web Push

Generate a key pair inside the instance:

    runagent -m ntfy1 podman exec ntfy-app ntfy webpush keys

Store the keys with `configure-module` (they go to `state/passwords.env`) and
set the public options in `server_config`:

```
api-cli run configure-module --agent module/ntfy1 --data - <<EOF
{
  "host": "ntfy.domain.com",
  "http2https": true,
  "lets_encrypt": false,
  "web_push_public_key": "<public key>",
  "web_push_private_key": "<private key>"
}
EOF
```

```yaml
web-push-file: "/var/lib/ntfy/webpush.db"
web-push-email-address: "admin@domain.com"
```

## Data and backup

ntfy data (message cache, users, tokens and ACLs, Web Push subscriptions and
attachments) is stored in the `ntfy-app` Podman volume, mounted at
`/var/lib/ntfy`. `server.yml` is mounted read-only at `/etc/ntfy`.

Before a module backup, ntfy is stopped for the time it takes to copy its
closed databases, attachments and `server.yml` into a snapshot directory
inside the volume; then it is started again and Restic uploads the snapshot.
The copy is removed after a successful backup. Allow free space in the volume
for a second copy of the databases.

Backups made by releases up to 1.x can be restored: they contain the settings
and secrets, but no ntfy data, since those releases did not back it up.

## Updating from older releases

Releases up to 1.x kept the settings in `NTFY_*` environment variables and the
ntfy data in the state directory. An update moves the settings to
`state/config/server.yml`, the secrets to `state/passwords.env` and the data to
the `ntfy-app` volume. Instances configured from the old Settings page could
not start ntfy (it rejects the empty `auth-default-access` they wrote); they
receive the settings of a new installation, with private topics: create users
and topic permissions in **Users and tokens**, or set
`auth-default-access: read-write` under **Advanced** for a public server. See
[administration details](docs/administration.md#updating-from-releases-up-to-1x).

ntfy migrates its message cache schema when it starts with a newer version,
and an older ntfy cannot open the migrated cache. Create a module backup
before updating; to roll back, restore the backup with the previous module
version.

## Smarthost setting discovery

When the cluster smarthost is enabled, `bin/discover-smarthost` reads its host,
port, username and password from the local Redis replica before every start
and writes them to `state/smarthost.env` (mode 0600) as
`NTFY_SMTP_SENDER_ADDR`, `NTFY_SMTP_SENDER_USER` and `NTFY_SMTP_SENDER_PASS`.
The `smarthost-changed` event restarts ntfy, so changes apply without editing
`server.yml`. The sender address is `smtp-sender-from` from `server.yml`, an
email-shaped SMTP username, or `no-reply@<host>`.

If `server.yml` defines `smtp-sender-addr`, the NS8 smarthost is ignored.
ntfy supports plain SMTP and STARTTLS with certificate verification, not
implicit TLS (port 465): such a smarthost is skipped with a warning in the
service log.

## Uninstall

To uninstall the instance:

    remove-module --no-preserve ntfy1

## Debug

Inspect the module environment, the containers and the ntfy log:

    runagent -m ntfy1 env
    runagent -m ntfy1 podman ps
    runagent -m ntfy1 podman logs ntfy-app
    runagent -m ntfy1 cat config/server.yml

Run a shell inside the container:

    runagent -m ntfy1 podman exec -ti ntfy-app sh

Remove passwords, tokens and private keys before sharing configuration files
or environment dumps.

## Testing

The QEMU workflow (`.github/workflows/test-module.yml`) runs the Robot
Framework suite in `tests/` on an NS8 node: the install leg installs the image
under test, the update leg installs release 1.0.0 and updates it.

The Python unit tests need PyYAML, which the NS8 agent provides:

    python3 -m pip install PyYAML
    python3 -m unittest discover -s tests -p 'test_*.py' -v

Set `NTFY_TEST_BINARY` to an ntfy executable of the pinned version to run the
account actions against a real ntfy as well. The browser-side YAML validation
has its own tests:

    cd ui && yarn install && yarn test

## UI translation

Translated with [Weblate](https://hosted.weblate.org/projects/ns8/).

To setup the translation process:

- add [GitHub Weblate app](https://docs.weblate.org/en/latest/admin/continuous.html#github-setup) to your repository
- add your repository to [hosted.weblate.org](https://hosted.weblate.org) or ask a nethserver developer to add it to ns8 Weblate project

## Credits

Administration features (account and token management, the protected
settings editor, backup snapshots, smarthost handling and their tests) are
adapted from
[Platypuschan/ns8-ntfy-reworked](https://github.com/Platypuschan/ns8-ntfy-reworked),
GPL-3.0.
