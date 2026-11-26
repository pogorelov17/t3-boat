"""Read selectable model slugs and effort levels from the running local T3 server."""
import json
import re
import subprocess
from pathlib import Path

from boat_profile import OPTION, PROVIDERS

SNAPSHOT = Path(__file__).with_name("model_snapshot.mjs")
MODEL_ID = {
    "codex": re.compile(r"(?:gpt-|codex-|o(?=[0-9]))[a-z0-9]+(?:[.-][a-z0-9]+)*"),
    "claudeAgent": re.compile(r"claude-[a-z0-9]+(?:[.-][a-z0-9]+)*"),
}


def provider_snapshot(provider):
    try:
        token = subprocess.run(
            ["t3", "auth", "session", "issue", "--scope", "orchestration:read",
             "--ttl", "2m", "--token-only"],
            stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=20,
        ).stdout
        result = subprocess.run(
            ["node", str(SNAPSHOT), "3773", provider], input=token,
            capture_output=True, check=True, timeout=90,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError(f"Cannot refresh the T3 {provider} catalog. Check provider sign-in "
                         "and the running T3 server, then retry setup.") from None


def selectable_models(provider, snapshot):
    if (snapshot["driver"] != provider or not snapshot["enabled"] or not snapshot["installed"]
            or snapshot["status"] not in ("ready", "warning")
            or snapshot["auth"]["status"] != "authenticated"
            or snapshot.get("availability") == "unavailable" or snapshot.get("modelsRetained")):
        return {}
    blocked = {model["slug"] for model in snapshot.get("updateRequiredModels", [])}
    offline = {row["name"] for row in snapshot.get("subProviders", []) if row.get("unavailableReason")}
    models = {}
    for model in snapshot["models"]:
        slug = model["slug"]
        if (not MODEL_ID[provider].fullmatch(slug) or not re.search(r"\d", slug)
                or "latest" in slug.split("-") or slug in blocked or model.get("isCustom")
                or model.get("subProvider") in offline):
            continue
        for option in (model.get("capabilities") or {}).get("optionDescriptors", []):
            if option["id"] == OPTION[provider] and option["type"] == "select":
                choices = [row["id"] for row in option["options"] if isinstance(row["id"], str)
                           and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", row["id"])]
                if choices:
                    models[slug] = list(dict.fromkeys(choices))
                break
    return models


def catalog(profile):
    result = {}
    for provider in PROVIDERS[profile]:
        response = provider_snapshot(provider)
        try:
            snapshot = next(row for row in response["providers"] if row["instanceId"] == provider)
            models = selectable_models(provider, snapshot)
        except (AttributeError, KeyError, StopIteration, TypeError):
            models = {}
        if not models:
            raise ValueError(f"T3 reports no selectable {provider} models with supported effort levels. "
                             "Check provider sign-in and the installed CLI version, then retry setup.")
        result[provider] = models
    return result
