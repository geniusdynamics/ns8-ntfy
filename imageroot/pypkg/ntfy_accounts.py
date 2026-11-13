#
# Copyright (C) 2026 Genius Dynamics
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Adapted from Platypuschan/ns8-ntfy-reworked (GPL-3.0).
#
"""Account administration using the CLI shipped with the pinned ntfy image.

Never forward command output/errors to NS8 tasks: token list includes secrets.
Only creation responses contain a new credential; list responses use opaque IDs.
"""
import hashlib
import os
import re
import secrets
import subprocess

import ntfy_config


class AccountError(ValueError):
    pass


def cli(*arguments, password=None):
    command = ["podman", "exec"]
    environment = os.environ.copy()
    if password is not None:
        environment["NTFY_PASSWORD"] = password
        # Pass only the variable name in argv, never its value.
        command += ["--env", "NTFY_PASSWORD"]
    command += ["ntfy-app", "ntfy", *arguments]
    try:
        result = subprocess.run(command, env=environment, capture_output=True,
                                text=True, timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise AccountError("account_command_failed") from None
    if result.returncode:
        raise AccountError("account_command_failed")
    return result.stdout


def parse_users(output):
    users = []
    current = None
    for line in output.splitlines():
        match = re.fullmatch(r"user (\S+) \(role: (\w+), tier: .+\)", line)
        if match:
            current = {"username": match[1], "role": match[2],
                       "provisioned": ", server config)" in line, "access": []}
            users.append(current)
        elif line.startswith("- ") and current:
            match = re.fullmatch(r"- (read-write|read-only|write-only|no) access to topic (\S+?)( \(server config\))?", line)
            if match:
                current["access"].append({"topic": match[2],
                    "permission": "deny" if match[1] == "no" else match[1],
                    "provisioned": bool(match[3])})
        elif line.strip():
            raise AccountError("account_output_invalid")
    if not users:
        raise AccountError("account_output_invalid")
    return users


def token_id(value):
    return hashlib.sha256(value.encode()).hexdigest()


def parse_tokens(output, private=False):
    tokens = []
    username = None
    for line in output.splitlines():
        if line == "no users with tokens":
            continue
        match = re.fullmatch(r"user (\S+)", line)
        if match:
            username = match[1]
            continue
        match = re.fullmatch(r"- (tk_[A-Za-z0-9]+)(?: \((.*)\))?, (never expires|expires .+?), accessed from (\S+) at (.+?)( \(server config\))?", line)
        if not match or not username:
            raise AccountError("account_output_invalid")
        token = {"id": token_id(match[1]), "username": username,
                 "label": match[2] or "", "expires": match[3],
                 "last_access": match[5], "provisioned": bool(match[6])}
        if private:
            token["value"] = match[1]
        tokens.append(token)
    return tokens


def require_auth():
    # Without auth-file (or database-url) ntfy has no user database and its
    # CLI fails; report that clearly instead of a generic command failure.
    if not ntfy_config.auth_enabled(ntfy_config.read_server_config()):
        raise AccountError("auth_not_configured")


def list_accounts():
    if not ntfy_config.auth_enabled(ntfy_config.read_server_config()):
        return {"auth_enabled": False, "users": [], "tokens": []}
    return {"auth_enabled": True,
            "users": parse_users(cli("user", "list")),
            "tokens": parse_tokens(cli("token", "list"))}


def validate_request(data):
    if not isinstance(data, dict):
        raise AccountError("invalid_account_request")
    operation = data.get("operation")
    if operation not in {"add-user", "reset-password", "remove-user", "add-token", "remove-token", "set-access", "remove-access"}:
        raise AccountError("invalid_account_request")
    username = data.get("username", "")
    anonymous = username == "*" and operation in {"set-access", "remove-access"}
    if not anonymous and (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}", username) or username == "everyone"):
        raise AccountError("invalid_username")
    if operation == "add-user" and data.get("role", "user") not in {"admin", "user"}:
        raise AccountError("invalid_account_request")
    if operation == "add-token":
        label = data.get("label", "")
        if not isinstance(label, str) or len(label) > 80 or any(ord(c) < 32 for c in label):
            raise AccountError("invalid_account_request")
        if data.get("expires", "never") not in {"1d", "7d", "30d", "90d", "365d", "never"}:
            raise AccountError("invalid_account_request")
    if operation == "remove-token" and not re.fullmatch(r"[a-f0-9]{64}", str(data.get("id", ""))):
        raise AccountError("invalid_account_request")
    if operation in {"set-access", "remove-access"}:
        if not re.fullmatch(r"[A-Za-z0-9_*][A-Za-z0-9_.*-]{0,63}", str(data.get("topic", ""))):
            raise AccountError("invalid_account_request")
        if operation == "set-access" and data.get("permission") not in {"read-write", "read-only", "write-only", "deny"}:
            raise AccountError("invalid_account_request")


def _manage_account(data):
    validate_request(data)
    require_auth()
    operation, username = data["operation"], data["username"]
    users = parse_users(cli("user", "list"))
    user = next((item for item in users if item["username"] == username), None)
    if operation == "add-user":
        if user:
            raise AccountError("user_exists")
    elif user is None:
        raise AccountError("user_not_found")
    elif user["provisioned"] and operation in {"reset-password", "remove-user"}:
        raise AccountError("provisioned_account")
    if operation == "remove-user" and user["role"] == "admin" and sum(u["role"] == "admin" for u in users) <= 1:
        raise AccountError("last_admin")
    response = {}
    if operation in {"add-user", "reset-password"}:
        password = secrets.token_urlsafe(24)
        args = ("user", "add", "--role", data.get("role", "user"), username) if operation == "add-user" else ("user", "change-pass", username)
        cli(*args, password=password)
        response = {"username": username, "password": password}
    elif operation == "remove-user":
        cli("user", "remove", username)
    elif operation == "add-token":
        # ntfy prunes old tokens above its hard limit. Do not silently revoke one.
        if sum(t["username"] == username for t in parse_tokens(cli("token", "list"))) >= 60:
            raise AccountError("token_limit")
        args = ["token", "add", "--label", data.get("label", "")]
        expiry = data.get("expires", "never")
        if expiry != "never":
            args += ["--expires", expiry]
        output = cli(*args, username)
        match = re.fullmatch(r"token (tk_[A-Za-z0-9]+) created for user \S+, .+\n?", output)
        if not match:
            raise AccountError("account_output_invalid")
        response = {"username": username, "token": match[1]}
    elif operation == "remove-token":
        tokens = parse_tokens(cli("token", "list"), private=True)
        token = next((t for t in tokens if t["username"] == username and secrets.compare_digest(t["id"], data["id"])), None)
        if not token:
            raise AccountError("token_not_found")
        if token["provisioned"]:
            raise AccountError("provisioned_account")
        cli("token", "remove", username, token["value"])
    else:
        grant = next((g for g in user["access"] if g["topic"] == data["topic"]), None)
        if grant and grant["provisioned"]:
            raise AccountError("provisioned_account")
        if operation == "set-access":
            cli("access", username, data["topic"], data["permission"])
        else:
            cli("access", "--reset", username, data["topic"])
    # The server may have auth-access-cache enabled. Reload after CLI writes.
    try:
        subprocess.run(["systemctl", "--user", "restart", "ntfy-app.service"],
                       check=True, capture_output=True, timeout=90)
    except (OSError, subprocess.SubprocessError):
        # Retain newly created credentials even if the service restart failed.
        response["restart_failed"] = True
    return response


def manage_account(data):
    # Serialize check-and-write operations and prevent a concurrent backup
    # snapshot from being interrupted by an account-related service restart.
    with ntfy_config.state_lock():
        return _manage_account(data)
