# ntfy administration and NS8 integration

This page describes how the module stores the ntfy configuration, manages
accounts, backs up data and updates older instances. It is adapted from
[Platypuschan/ns8-ntfy-reworked](https://github.com/Platypuschan/ns8-ntfy-reworked)
(GPL-3.0), which assessed and implemented these features against ntfy
v2.28.0; this module runs the ntfy version pinned in `build-images.sh`.

## Configuration ownership

| What | Where | In the backup | Shown in the UI |
|---|---|---|---|
| ntfy settings | `state/config/server.yml` (0600, directory 0700), mounted read-only at `/etc/ntfy` | yes, in the data snapshot | filtered |
| Upstream token, Web Push keys | `state/passwords.env` (0600) | yes | never |
| SMTP sender (NS8 smarthost) | `state/smarthost.env` (0600), rewritten before every start | no | never |
| Users, tokens, ACLs, messages, attachments | `ntfy-app` volume, mounted at `/var/lib/ntfy` | yes, in the data snapshot | through the account actions |
| Route (host, TLS) | module environment (`TRAEFIK_*`) | yes | yes |

`config/server.yml` is the source of truth for the ntfy settings. Secret and
module-managed options are filtered out of `get-configuration` and merged back
server-side by `configure-module`, so the editor never round-trips a masked
placeholder as a real credential. Invalid existing YAML fails closed; error
text never contains YAML source lines. YAML comments are not part of the
editable representation, since they can contain credentials too. The browser
applies the same policy (`imageroot/pypkg/ntfy_config_policy.json`) and rejects
invalid YAML before it creates a task, because task inputs are kept by NS8;
the backend validates again for API clients.

Reserved options:

- `smtp-sender-*`, `upstream-*`, `auth-users`, `auth-tokens`, `auth-access`,
  `database-url`, `firebase-key-file`, `stripe-webhook-key`, and every option
  whose name contains `password`, `pass`, `token`, `secret` or `private-key`:
  hidden and preserved. They can be maintained in `config/server.yml` on the
  node.
- `web-push-public-key`: kept with the private key in `passwords.env`.
- `listen-http`, `behind-proxy`, `proxy-forwarded-header`: enforced by the
  service unit (`:80`, `true`, `X-Forwarded-For`), since NS8 reaches ntfy only
  through Traefik on `127.0.0.1:TCP_PORT`.
- `x-ns8-*`: private module metadata.

Unknown spelling variants are not a second configuration path: new YAML must
use lowercase option names with hyphens.

ntfy gives `NTFY_*` environment variables precedence over `server.yml`. The
module therefore passes to the container only the secrets of `passwords.env`
(non-empty values only), the smarthost settings of `smarthost.env` and the
enforced options above.

## Account administration

`list-accounts` and `manage-account` invoke the CLI shipped with the pinned
ntfy image (`podman exec ntfy-app ntfy ...`), using argument arrays rather than
shell interpolation. Passwords are generated server-side and forwarded through
an inherited environment variable whose value is absent from argv. Raw CLI
errors and token-list output are captured and never forwarded to NS8 logs;
the actions report error codes only. Token list responses use SHA-256 opaque
identifiers; revocation resolves the identifier internally, checks the owner
and rejects provisioned tokens.

Both actions need a user database: `auth-file` (or `database-url`) in
`server.yml`. Without one, `list-accounts` returns `"auth_enabled": false` and
`manage-account` fails with `auth_not_configured`; the page explains how to
enable it.

Account mutations, backup snapshot creation and restore are serialized by a
shared module-local file lock (`state/.ntfy-admin.lock`), so account-related
service restarts cannot reopen the databases during a stopped-database
snapshot. The last admin cannot be deleted and exceeding ntfy's 60-token limit
is rejected rather than silently pruning an older token. Native CLI writes
avoid a second user database or direct edits of ntfy's SQLite schema. The
service is restarted after a mutation because the server may cache access
rules. New credentials are still returned if only that restart fails, with a
warning flag.

The creation response necessarily contains the new credential. NS8's task
interface can retain that response, so one-time display is an interface
behavior, not a guarantee that no administrator can retrieve the task output.
The page does not save credentials to localStorage or re-fetch them in lists.
Password replacement does not revoke existing tokens.

Existing declaratively provisioned objects (`auth-users`, `auth-tokens`,
`auth-access` in `server.yml`) stay read-only where ntfy disallows CLI changes.
The page is part of the NS8 administrative interface; ntfy accounts do not
authorize NS8 module actions.

## iOS instant delivery

The **iOS instant notifications** switch sets `upstream-base-url`. Enabling it
uses the previous upstream or `https://ntfy.sh`. Disabling it removes the
active URL without deleting the optional token, and keeps the previous target
in the private `x-ns8-disabled-upstream-base-url` key of the same file, so
re-enabling restores a custom target instead of sending its token to ntfy.sh.
ntfy ignores this key. The upstream receives `poll_request` messages, not the
notification contents.

## Backup and restore

NS8 runs `bin/module-dump-state` before Restic and `bin/module-cleanup-state`
after a successful upload. The dump stops ntfy, copies the closed SQLite
databases, the rest of the `ntfy-app` volume and `server.yml` into
`.ntfy-backup-snapshot` inside the volume, and starts ntfy again before Restic
runs, also when copying fails. Attachments in a top-level
`attachment-cache-dir` below `/var/lib/ntfy` are hard-linked instead of
copied, because ntfy never modifies an attachment after writing it. The volume
needs free space for a second copy of the databases. `etc/state-include.conf`
lists only the snapshot and `state/passwords.env`.

`restore-module` puts the snapshot back in place before `configure-module`
starts ntfy. A backup without a snapshot comes from a release up to 1.x, which
backed up only the module environment and `passwords.env`: the restore then
rebuilds `server.yml` from the `NTFY_*` variables of the backed-up environment
(see below) and moves secrets found there into `passwords.env`. A restored
`passwords.env` takes precedence. Those backups contain no ntfy data, since
older releases did not back it up.

## Updating from releases up to 1.x

Older releases kept the ntfy settings in `NTFY_*` variables of the module
environment, wrote empty strings (or, in 1.0.0, the string `None`) for every
setting the Settings page did not send, and mounted the state directory itself
as `/var/lib/ntfy`. ntfy refuses to start with an empty
`auth-default-access` or a `None` boolean, so instances configured from the
Settings page did not serve requests.

`update-module.d` migrates them in place, once:

1. `10move_secrets` moves `NTFY_UPSTREAM_ACCESS_TOKEN` and the Web Push keys
   from the environment to `passwords.env` and drops empty or `None` values (a
   `None` public key would enable Web Push and stop ntfy).
2. `15server_config` writes `config/server.yml` from the `NTFY_*` variables
   and removes them from the environment, since they would override the file.
   An instance whose variables let ntfy start keeps its effective settings
   (`base-url` defaults to `https://<host>`). An instance whose variables kept
   ntfy from starting gets the settings of a new installation: `deny-all`,
   login enabled, an auth database and a message cache. The misspelled
   `NTFY_UPSTREAM_BASE_URE` was never effective; its URL is kept for the iOS
   switch, which stays off.
3. `16data_volume` stops ntfy and moves the databases, their SQLite journals
   and the attachment directory named by `server.yml` from the state directory
   into the `ntfy-app` volume. Agent files are never moved, and an entry that
   already exists in the volume is left in place with a warning.
4. `20restart` reloads the unit files and restarts the enabled service.

`configure-module` still accepts the old input keys (`base_url`,
`auth_default_access`, `enable_login`, `enable_signup`, `upstream_base_url`,
`web_push_email_address`, `cache_file`, `attachment_cache_dir`, `auth_file`,
`web_push_file`, and the secrets `upstream_access_token`,
`web_push_public_key`, `web_push_private_key`). A key that is present updates
the matching option, an empty value removes it, an omitted key keeps it.
`behind_proxy` is ignored. `get-configuration` no longer returns the
individual settings or any secret.

## Mail-to-push on NS8

Not implemented. ntfy has a lightweight incoming SMTP server that turns mail
into a normal publish request with normal topic authorization, but this module
publishes only HTTP through Traefik: setting `smtp-server-*` options alone does
not make SMTP reachable. Releases up to 1.x set `smtp-server-listen` to the
smarthost port by mistake, which stopped ntfy when a smarthost was enabled;
that mapping is gone.

A future integration would keep public mail reception with the NS8 Mail
application, expose ntfy's SMTP listener on an allocated internal port, add a
recipient relay rule in Mail targeting that endpoint, and use a dedicated
integration user with write-only rights to selected topics. A relay rule
changes the next hop only; accepting arbitrary Internet mail for a new push
domain also needs domain and recipient routing in Mail. The listener port,
firewall and relay resources would need their own lifecycle (backup, restore,
clone and removal).

## Verification

- Python unit tests (`tests/test_*.py`) cover the configuration policy,
  duplicate/merge/alias rejection, hidden option preservation, the iOS switch,
  redacted account lists, credential generation, owner-bound revocation,
  provisioned accounts, token limits, last-admin protection, the backup
  snapshot scripts, the smarthost discovery and the migrations of older
  releases (settings, secrets, data move, restore of old backups).
- `tests/test_accounts_integration.py` runs the account actions against a real
  ntfy binary when `NTFY_TEST_BINARY` is set; it substitutes only the
  Podman/systemd process boundary.
- `ui/tests` covers the browser-side YAML validation (`yarn test`).
- The Robot suite (`tests/*.robot`) runs on an NS8 node in QEMU: install, or
  update from release 1.0.0, configuration, accounts, publish/subscribe
  through Traefik, the backup snapshot and removal.

Sources:

- [ntfy configuration](https://docs.ntfy.sh/config/)
- [ntfy serve options](https://github.com/binwiederhier/ntfy/blob/main/cmd/serve.go)
- [ntfy user, token and access CLI](https://github.com/binwiederhier/ntfy/tree/main/cmd)
- [NS8 module backup](https://nethserver.github.io/ns8-core/core/backup_restore/)
