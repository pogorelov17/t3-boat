import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import model_catalog


def model(slug, option="reasoningEffort", values=("low", "high"), **fields):
    descriptor = {"type": "select", "id": option, "options": [{"id": value} for value in values]}
    return {"slug": slug, "capabilities": {"optionDescriptors": [descriptor]}, **fields}


def provider(instance, models, **fields):
    return {"instanceId": instance, "driver": instance, "enabled": True, "installed": True,
            "status": "ready", "auth": {"status": "authenticated"}, "models": models, **fields}


class CatalogTests(unittest.TestCase):
    def test_catalog_uses_only_reported_versions_with_usable_effort_choices(self):
        codex = provider("codex", [
            model("gpt-6.2-sol", values=["low", "high", "low"]), model("o3-mini"),
            model("gpt-6.2-newer"), model("gpt-6.2-no-effort", "serviceTier"),
            model("gpt-6.2-hidden", subProvider="signed-out"), model("gpt-6.2-latest"),
            model("gpt-6.2-custom", isCustom=True),
            {"slug": "gpt-6.2-unknown", "capabilities": None},
        ], updateRequiredModels=[{"slug": "gpt-6.2-newer"}],
           subProviders=[{"name": "signed-out", "unavailableReason": "Sign in first"}])
        claude = provider("claudeAgent", [
            model("claude-opus-5-5", "effort", ("medium", "max"), aliases=["opus"]),
            model("default", "effort"), model("opus", "effort"), model("claude-sonnet", "effort"),
            model("claude-haiku-5-5", "effort", ()), model("claude-opus-5-5-latest", "effort"),
        ])
        with patch.object(model_catalog, "provider_snapshot", return_value={"providers": [claude, codex]}):
            self.assertEqual(model_catalog.catalog("codex+claude"), {
                "codex": {"gpt-6.2-sol": ["low", "high"], "o3-mini": ["low", "high"]},
                "claudeAgent": {"claude-opus-5-5": ["medium", "max"]},
            })

    def test_disabled_unavailable_unauthenticated_and_failed_probes_do_not_supply_models(self):
        for change in ({"driver": "other"}, {"enabled": False}, {"installed": False},
                       {"status": "error"}, {"availability": "unavailable"},
                       {"auth": {"status": "unauthenticated"}}, {"modelsRetained": True}):
            snapshot = provider("codex", [model("gpt-6.2-sol")], **change)
            with self.subTest(change=change), patch.object(
                model_catalog, "provider_snapshot", return_value={"providers": [snapshot]}
            ) as read:
                with self.assertRaisesRegex(ValueError, "codex"):
                    model_catalog.catalog("codex-only")
                read.assert_called_once_with("codex")

    def test_no_substitution_when_a_provider_has_no_models_or_returns_invalid_data(self):
        for response in ({}, {"providers": None}, {"providers": []},
                         {"providers": [provider("codex", [])]}):
            with self.subTest(response=response), patch.object(
                model_catalog, "provider_snapshot", return_value=response
            ), self.assertRaisesRegex(ValueError, "codex"):
                model_catalog.catalog("codex-only")
