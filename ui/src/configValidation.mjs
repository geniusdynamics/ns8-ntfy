import { isMap, isScalar, parseDocument, visit } from "yaml";

// Share the reserved-option policy with Python; do not send protected YAML to
// an NS8 task just to discover a validation error in its persisted input.
export function validateServerConfig(source, policy) {
  try {
    const document = parseDocument(source, { uniqueKeys: true, merge: false });
    if (document.errors.length) return false;
    let valid = true;
    visit(document, {
      Alias() {
        valid = false;
      },
      Pair(key, pair) {
        if (
          !isScalar(pair.key) ||
          typeof pair.key.value !== "string" ||
          pair.key.value === "<<"
        )
          valid = false;
      },
    });
    const root = document.contents;
    if (!root || (isScalar(root) && root.value === null)) return valid;
    if (!isMap(root)) return false;
    const secret = new RegExp(policy.secret_pattern);
    const option = new RegExp(policy.option_pattern);
    for (const pair of root.items) {
      const key = pair.key.value;
      if (
        typeof key !== "string" ||
        !option.test(key) ||
        policy.managed_keys.includes(key) ||
        policy.managed_prefixes.some((prefix) => key.startsWith(prefix)) ||
        secret.test(key)
      )
        valid = false;
    }
    return valid;
  } catch {
    return false;
  }
}
