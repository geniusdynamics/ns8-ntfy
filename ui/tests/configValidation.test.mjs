import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { validateServerConfig } from "../src/configValidation.mjs";

const policy = JSON.parse(
  readFileSync(
    new URL("../../imageroot/pypkg/ntfy_config_policy.json", import.meta.url)
  )
);

test("valid public YAML including an empty editor", () => {
  for (const source of [
    "",
    "# comment",
    "enable-login: true",
    "auth-default-access: deny-all",
    "base-url: https://ntfy.example.test",
  ]) {
    assert.equal(validateServerConfig(source, policy), true);
  }
});

test("reject duplicates and indirect overrides before task submission", () => {
  for (const source of [
    "x: 1\nx: 2",
    "x: {a: 1, a: 2}",
    "x: &x 1\ny: *x",
    "<<: {x: 1}",
    "- not-a-mapping",
    "x: [unclosed",
    "? [a,b]\n: x",
  ]) {
    assert.equal(validateServerConfig(source, policy), false);
  }
});

test("reject credentials and module-owned options before task submission", () => {
  for (const source of [
    "smtp-sender-pass: secret",
    "upstream-access-token: secret",
    "upstream-base-url: https://ntfy.sh",
    "auth-users: []",
    "auth-tokens: []",
    "auth-access: []",
    "web-push-private-key: secret",
    "stripe-webhook-key: secret",
    "behind-proxy: false",
    "listen-http: :8080",
    "web-push-public-key: BPublic",
    "x-ns8-disabled-upstream-base-url: https://unused.example.test",
    "enable_login: false",
  ]) {
    assert.equal(validateServerConfig(source, policy), false);
  }
});
