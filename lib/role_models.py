"""Resolve and validate a playbook's worker model assignments and owner overrides."""
import re


VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:_/-]*")


def check_overrides(package, overrides, enabled, lead_role=None):
    """Validate persistent owner overrides without consulting a live model catalog."""
    if not isinstance(overrides, dict):
        raise ValueError("role_overrides must be an object")
    for role, row in overrides.items():
        if role not in package.roles:
            raise ValueError(f"role override {role!r} is not a role of {package.name}")
        if role == lead_role:
            raise ValueError(f"the {role!r} role is the T3 team lead; change its model with t3-boat setup")
        if not isinstance(row, dict):
            raise ValueError(f"role override {role!r} must be an object")
        selector = {"provider", "family", "effort"} if "family" in row else {"provider", "model", "effort"}
        if set(row) != selector:
            raise ValueError(f"role override {role!r} must have provider, effort and exactly one of family or model")
        if row["provider"] not in enabled:
            raise ValueError(f"role override {role!r} provider must be one of {', '.join(enabled)}")
        for key, value in row.items():
            if not isinstance(value, str) or not VALUE.fullmatch(value):
                raise ValueError(f"role override {role!r} has an invalid {key}")
    return overrides


def effective(package, model_set, overrides=None):
    """Return each role's effective entries and their source, without resolving model families."""
    overrides = overrides or {}
    base = package.model_set(model_set)["roles"] if model_set else {}
    result = {}
    for role in package.roles:
        if role in overrides:
            result[role] = {"entries": [dict(overrides[role])], "source": "owner override"}
        elif role in base:
            result[role] = {"entries": [dict(row) for row in base[role]], "source": f"model set {model_set}"}
        else:
            result[role] = {"entries": [], "source": "team lead choice"}
    return result


def selector(entry):
    """A compact human-readable provider/model selector."""
    key = "model" if "model" in entry else "family"
    return f"{entry['provider']} / {key}:{entry[key]} / {entry['effort']}"


def rule_violations(package, assignments, resolver=None):
    """Return model-rule violations in the effective assignment.

    `resolver`, when supplied, maps a family or exact selector to an exact model row. Rules whose entries cannot
    be resolved are compared by their stored selector instead, so an explicit identical override is still caught.
    """
    def key(row):
        resolved = resolver(row) if resolver else None
        if resolved:
            return resolved["provider"], resolved["model"], resolved["effort"]
        kind = "model" if "model" in row else "family"
        return row["provider"], kind + ":" + row[kind], row["effort"]

    violations = []
    for rule in package.model_rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("roles"), list) \
                or not isinstance(rule.get("must_differ_from"), str):
            continue
        reference = rule["must_differ_from"]
        reference_keys = {key(row) for row in assignments.get(reference, {}).get("entries", [])}
        if not reference_keys:
            continue
        for role in rule["roles"]:
            if not isinstance(role, str):
                continue
            overlap = reference_keys & {key(row) for row in assignments.get(role, {}).get("entries", [])}
            if overlap:
                violations.append(f"{role} must differ from {reference}")
    return violations
