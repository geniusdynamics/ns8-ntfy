#
# Copyright (C) 2026 Nethesis S.r.l.
# Copyright (C) 2026 Genius Dynamics
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Adapted from Platypuschan/ns8-ntfy-reworked (GPL-3.0).
#

"""Shared helpers for the ntfy module actions and scripts.

All paths are relative to the module state directory, the working directory
of NS8 actions, update-module.d scripts and the systemd units.
"""

import contextlib
import fcntl
import json
import os
import re
import tempfile


CONFIG_DIRECTORY = "config"
CONFIG_PATH = os.path.join(CONFIG_DIRECTORY, "server.yml")
SECRETS_FILE = "passwords.env"
DATA_MOUNT = "/var/lib/ntfy"


def yaml_string(value):
    """Return a YAML-compatible, safely quoted string."""
    return json.dumps(str(value), ensure_ascii=False)


def default_config(host):
    """Return the server.yml used for new installations."""
    return "\n".join((
        f"base-url: {yaml_string('https://' + host)}",
        f'cache-file: "{DATA_MOUNT}/cache.db"',
        f'attachment-cache-dir: "{DATA_MOUNT}/attachments"',
        f'auth-file: "{DATA_MOUNT}/auth.db"',
        'auth-default-access: "deny-all"',
        "enable-login: true",
        "enable-signup: false",
        "",
    ))


def write_server_config(content):
    """Atomically persist server.yml with permissions suitable for secrets."""
    os.makedirs(CONFIG_DIRECTORY, mode=0o700, exist_ok=True)
    os.chmod(CONFIG_DIRECTORY, 0o700)
    fd, temporary_path = tempfile.mkstemp(
        dir=CONFIG_DIRECTORY,
        prefix=".server.yml.",
        text=True,
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            if content and not content.endswith("\n"):
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def read_server_config():
    """Return the server.yml text, or an empty string if it is missing."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as stream:
            return stream.read()
    except FileNotFoundError:
        return ""


# These values are maintained outside the editor. Keep existing installations'
# values in the same private, backed-up server.yml; never send them to the UI.
with open(os.path.join(os.path.dirname(__file__), "ntfy_config_policy.json"), encoding="utf-8") as policy_file:
    CONFIG_POLICY = json.load(policy_file)
MANAGED_KEYS = frozenset(CONFIG_POLICY["managed_keys"])


def managed_key(key):
    key = key.replace("_", "-").lower()
    return (key in MANAGED_KEYS or key.startswith(tuple(CONFIG_POLICY["managed_prefixes"]))
            or bool(re.search(CONFIG_POLICY["secret_pattern"], key)))


def parse_config(content):
    """Load unambiguous YAML. Reject duplicates, aliases and merge overrides."""
    import yaml

    class UniqueLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise ValueError("YAML aliases are not supported")
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    raise ValueError("YAML merge keys are not supported")
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str):
                    raise ValueError("YAML keys must be strings")
                if key in result:
                    raise ValueError("Duplicate YAML key")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    try:
        parsed = yaml.load(content, Loader=UniqueLoader)
    except yaml.YAMLError:
        # PyYAML exceptions include source lines, possibly containing secrets.
        raise ValueError("Invalid YAML syntax") from None
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError("The top level must be a mapping")
    return parsed


def dump_config(config):
    import yaml
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True) if config else ""


def validate_server_config(content):
    try:
        parsed = parse_config(content)
        if any(not re.fullmatch(CONFIG_POLICY["option_pattern"], key) for key in parsed):
            return "Use canonical lowercase YAML option names with hyphens"
        if any(managed_key(key) for key in parsed):
            return "The YAML contains settings managed outside the editor"
    except ValueError as error:
        return str(error)
    return None


def editable_config(content):
    # Re-serialize to avoid leaking secrets in comments, aliases or inline text.
    return dump_config({key: value for key, value in parse_config(content).items()
                        if not managed_key(key)})


DISABLED_UPSTREAM = "x-ns8-disabled-upstream-base-url"


def set_ios_push(hidden, enabled):
    """Enable or disable upstream forwarding in a dict of managed options."""
    # Preserve the target in private module metadata when disabled. Otherwise
    # re-enabling a custom upstream could send its token to the default host.
    if enabled is True:
        hidden["upstream-base-url"] = (hidden.get("upstream-base-url")
                                       or hidden.get(DISABLED_UPSTREAM)
                                       or "https://ntfy.sh")
        hidden.pop(DISABLED_UPSTREAM, None)
    elif enabled is False and hidden.get("upstream-base-url"):
        hidden[DISABLED_UPSTREAM] = hidden.pop("upstream-base-url")
    return hidden


def merge_configuration(content, previous, ios_push=None):
    error = validate_server_config(content)
    if error:
        raise ValueError(error)
    hidden = {key: value for key, value in parse_config(previous).items()
              if managed_key(key)}
    set_ios_push(hidden, ios_push)
    if not hidden:
        return content
    return dump_config({**parse_config(content), **hidden})


def _root_key_pattern(key):
    return re.compile(
        r"^[\"']?" + re.escape(key) + r"[\"']?[ \t]*:(?P<value>.*)$"
    )


def root_scalar(content, key):
    """Return the unquoted scalar value of a top-level (unindented) key.

    Return None if the key is missing or its value is empty.
    """
    pattern = _root_key_pattern(key)
    for line in content.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        value = match.group("value").strip()
        if value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            return value[1:end] or None if end > 0 else None
        return value.split(" #", 1)[0].strip() or None
    return None


def auth_enabled(content):
    """True if ntfy keeps users, tokens and ACLs (auth-file or database-url)."""
    return bool(root_scalar(content, "auth-file") or root_scalar(content, "database-url"))


@contextlib.contextmanager
def state_lock(directory=None):
    """Coordinate account writes with stopped-database backup snapshots."""
    directory = directory or os.environ.get("AGENT_STATE_DIR", ".")
    descriptor = os.open(os.path.join(directory, ".ntfy-admin.lock"),
                         os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


#
# Secrets: state/passwords.env (mode 0600), never the module environment,
# which NS8 copies to Redis where every module of the node can read it.
#

# configure-module input key -> passwords.env key
SECRET_INPUTS = {
    "upstream_access_token": "NTFY_UPSTREAM_ACCESS_TOKEN",
    "web_push_public_key": "NTFY_WEB_PUSH_PUBLIC_KEY",
    "web_push_private_key": "NTFY_WEB_PUSH_PRIVATE_KEY",
}


def legacy_value(value):
    """Normalize a value written by an older release.

    Release 1.0.0 stored missing inputs as the string "None"; later releases
    stored them as empty strings. Both mean "not set".
    """
    value = "" if value is None else str(value).strip()
    return "" if value == "None" else value


def secrets_path(directory=None):
    return os.path.join(directory or os.environ.get("AGENT_STATE_DIR", "."), SECRETS_FILE)


def read_secrets(directory=None):
    values = {}
    try:
        with open(secrets_path(directory), "r", encoding="utf-8") as stream:
            for line in stream:
                line = line.rstrip("\n")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key] = value
    except FileNotFoundError:
        pass
    return values


def write_secrets(updates, directory=None):
    """Merge updates into passwords.env. Empty values remove the key.

    Unset values are left out of the file instead of being written empty, so
    ntfy does not receive an empty variable that hides a server.yml setting.
    """
    path = secrets_path(directory)
    current = read_secrets(directory)
    current.update(updates)
    current = {key: legacy_value(value) for key, value in current.items()}
    current = {key: value for key, value in current.items() if value}
    for key, value in current.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"invalid newline in {key}")
    fd, temporary_path = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".", prefix=".passwords.env.", text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            for key in sorted(current):
                stream.write(f"{key}={current[key]}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


#
# Settings of releases up to 1.x: NTFY_* variables in the module environment
# and the matching configure-module input keys. They are now kept in
# server.yml; the keys are still accepted by configure-module.
#

# configure-module input key -> (environment variable, server.yml option, kind)
LEGACY_OPTIONS = {
    "base_url": ("NTFY_BASE_URL", "base-url", "url"),
    "auth_default_access": ("NTFY_AUTH_DEFAULT_ACCESS", "auth-default-access", "permission"),
    "enable_login": ("NTFY_ENABLE_LOGIN", "enable-login", "bool"),
    "enable_signup": ("NTFY_ENABLE_SIGNUP", "enable-signup", "bool"),
    "web_push_email_address": ("NTFY_WEB_PUSH_EMAIL_ADDRESS", "web-push-email-address", "str"),
    "cache_file": ("NTFY_CACHE_FILE", "cache-file", "str"),
    "attachment_cache_dir": ("NTFY_ATTACHMENT_CACHE_DIR", "attachment-cache-dir", "str"),
    "auth_file": ("NTFY_AUTH_FILE", "auth-file", "str"),
    "web_push_file": ("NTFY_WEB_PUSH_FILE", "web-push-file", "str"),
}
# Releases up to 1.x wrote the upstream URL to this misspelled variable,
# which ntfy ignored.
LEGACY_UPSTREAM_TYPO = "NTFY_UPSTREAM_BASE_URE"
LEGACY_ENVIRONMENT = sorted(
    [env for env, _, _ in LEGACY_OPTIONS.values()]
    + ["NTFY_BEHIND_PROXY", "NTFY_UPSTREAM_BASE_URL", LEGACY_UPSTREAM_TYPO]
)
PERMISSIONS = {"read-write", "rw", "read-only", "read", "ro", "write-only",
               "write", "wo", "deny-all", "deny", "none"}
# Go strconv.ParseBool, used by ntfy for environment variables
GO_TRUE = {"1", "t", "T", "TRUE", "true", "True"}
GO_FALSE = {"0", "f", "F", "FALSE", "false", "False"}


def as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "t", "true", "yes", "on")


def valid_url(value):
    return bool(re.fullmatch(r"https?://[^\s/]+", value or ""))


def has_legacy_environment(environment):
    return any(key in environment for key in LEGACY_ENVIRONMENT)


def legacy_environment_was_usable(environment):
    """Return True if ntfy could start with these legacy variables.

    The variables were passed to ntfy even when empty. An empty or "None"
    auth-default-access or an unparsable boolean made ntfy exit at startup,
    so such an instance never served requests.
    """
    access = environment.get("NTFY_AUTH_DEFAULT_ACCESS")
    if access is not None and access.lower() not in PERMISSIONS:
        return False
    for key in ("NTFY_BEHIND_PROXY", "NTFY_ENABLE_LOGIN", "NTFY_ENABLE_SIGNUP"):
        value = environment.get(key)
        if value is not None and value != "" and value not in GO_TRUE | GO_FALSE:
            return False
    login = environment.get("NTFY_ENABLE_LOGIN", "") in GO_TRUE
    signup = environment.get("NTFY_ENABLE_SIGNUP", "") in GO_TRUE
    if (login or signup) and not legacy_value(environment.get("NTFY_AUTH_FILE")):
        return False
    return True


def config_from_legacy_environment(environment, host):
    """Translate NTFY_* variables of an older release into server.yml text.

    A working instance keeps its effective settings. An instance whose
    variables kept ntfy from starting gets the defaults of a new installation.
    """
    if legacy_environment_was_usable(environment):
        config = {}
        base_url = legacy_value(environment.get("NTFY_BASE_URL"))
        config["base-url"] = base_url if valid_url(base_url) else "https://" + host
        for _, (env, option, kind) in LEGACY_OPTIONS.items():
            value = legacy_value(environment.get(env))
            if option == "base-url" or not value:
                continue
            if kind == "bool":
                config[option] = value in GO_TRUE
            elif kind == "permission":
                config[option] = value.lower()
            else:
                config[option] = value
    else:
        config = parse_config(default_config(host))

    upstream = legacy_value(environment.get("NTFY_UPSTREAM_BASE_URL"))
    disabled = legacy_value(environment.get(LEGACY_UPSTREAM_TYPO))
    if valid_url(upstream) and "base-url" in config:
        config["upstream-base-url"] = upstream
    elif valid_url(disabled):
        # Never effective: keep it for the iOS switch instead of enabling it.
        config[DISABLED_UPSTREAM] = disabled
    return dump_config(config)


def apply_legacy_options(content, data):
    """Apply configure-module input keys of older releases to server.yml text."""
    config = parse_config(content)
    changed = False
    for key, (_, option, kind) in LEGACY_OPTIONS.items():
        if key not in data:
            continue
        changed = True
        value = data[key]
        if kind == "bool":
            if value is None or value == "":
                config.pop(option, None)
            else:
                config[option] = as_bool(value)
            continue
        value = legacy_value(value)
        if not value:
            config.pop(option, None)
        elif kind == "permission":
            if value.lower() not in PERMISSIONS:
                raise ValueError(f"invalid {key}")
            config[option] = value.lower()
        elif kind == "url":
            if not valid_url(value):
                raise ValueError(f"invalid {key}")
            config[option] = value
        else:
            config[option] = value
    if "upstream_base_url" in data:
        changed = True
        upstream = legacy_value(data["upstream_base_url"])
        if upstream:
            if not valid_url(upstream):
                raise ValueError("invalid upstream_base_url")
            config["upstream-base-url"] = upstream
            config.pop(DISABLED_UPSTREAM, None)
        else:
            set_ios_push(config, False)
    return dump_config(config) if changed else content


def unset_legacy_environment(agent):
    """Remove NTFY_* settings that would override server.yml."""
    for key in LEGACY_ENVIRONMENT:
        agent.unset_env(key)


def migrate_legacy_environment(environment, host):
    """Create server.yml from legacy variables if it does not exist yet.

    Return True if server.yml was written.
    """
    if os.path.exists(CONFIG_PATH) or not has_legacy_environment(environment):
        return False
    write_server_config(config_from_legacy_environment(environment, host))
    return True


#
# Data that releases up to 1.x kept in the state directory, which they
# mounted as /var/lib/ntfy.
#

# Files of NS8 and of this module that live in the state directory
STATE_FILES = frozenset({
    "environment", "agent.env", SECRETS_FILE, "smarthost.env", CONFIG_DIRECTORY,
    "restic_restore.json", "ui_name.dump", "ui_note.dump", "CACHEDIR.TAG",
})
DATA_OPTIONS = ("cache-file", "auth-file", "web-push-file", "attachment-cache-dir")
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def legacy_data_entries(content):
    """Return the top-level /var/lib/ntfy entries named by server.yml."""
    names = []
    for option in DATA_OPTIONS:
        value = root_scalar(content, option)
        if not value:
            continue
        path = os.path.normpath(value)
        prefix = DATA_MOUNT + "/"
        if not path.startswith(prefix):
            continue
        name = path[len(prefix):].split("/", 1)[0]
        if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", name) or name in STATE_FILES:
            continue
        candidates = [name]
        if option != "attachment-cache-dir" and "/" not in path[len(prefix):]:
            candidates += [name + suffix for suffix in SQLITE_SIDECARS]
        for candidate in candidates:
            if candidate not in names and candidate not in STATE_FILES:
                names.append(candidate)
    return names
