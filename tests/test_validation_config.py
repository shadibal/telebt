import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from telebt.config import load_settings
from telebt.schedule import due_dates
from telebt.telegram_adapter import build_application
from telebt.validation import DEFAULT_VALIDATION_RULES, ValidationError, identifier, interval


UUID = "ABCDEF12-1234-1234-ABCD-123456789ABC"


class ValidationConfigurationTests(unittest.TestCase):
    def test_default_profile_preserves_project_spec_formats_and_365_day_limit(self):
        for kind in ("gaid", "idfa", "idfv", "singular"):
            with self.subTest(kind=kind):
                self.assertEqual(identifier(kind, f" {UUID} "), UUID.lower())
                for bad in (UUID.replace("-", "", 1), UUID + "x", "x" + UUID):
                    with self.assertRaises(ValidationError):
                        identifier(kind, bad)
        self.assertEqual(identifier("uid", " 1234567890123-1234567 "), "1234567890123-1234567")
        for bad in ("123456789012-1234567", "1234567890123-12345678", "x1234567890123-1234567"):
            with self.assertRaises(ValidationError):
                identifier("uid", bad)
        self.assertEqual(interval("365d"), timedelta(days=365))
        with self.assertRaises(ValidationError):
            interval("366d")

    def test_custom_profile_changes_each_identifier_and_interval_boundary(self):
        rules = replace(
            DEFAULT_VALIDATION_RULES,
            version="pilot-2",
            gaid_pattern=r"GA[0-9]{2}",
            idfa_pattern=r"IA[0-9]{2}",
            idfv_pattern=r"IV[0-9]{2}",
            uid_pattern=r"AF[0-9]{2}",
            singular_pattern=r"SI[0-9]{2}",
            max_interval_minutes=90,
        )
        for kind, good in (("gaid", "GA12"), ("idfa", "IA12"), ("idfv", "IV12"), ("uid", "AF12"), ("singular", "SI12")):
            with self.subTest(kind=kind):
                self.assertEqual(identifier(kind, good, rules), good)
                with self.assertRaises(ValidationError):
                    identifier(kind, UUID if kind != "uid" else "1234567890123-1234567", rules)
                with self.assertRaises(ValidationError):
                    identifier(kind, "x" + good, rules)
        self.assertEqual(interval("90m", rules), timedelta(minutes=90))
        with self.assertRaises(ValidationError):
            interval("91m", rules)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.assertRaises(ValidationError):
            due_dates("multiple", [{"id": "1", "interval": "91m"}], start, rules=rules)

    def test_environment_profile_reaches_ui_and_services(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "TELEGRAM_BOT_TOKEN": "123456:TEST",
                "DEV_MODE": "true",
                "DEV_TELEGRAM_USER_IDS": "7",
                "VALIDATION_RULES_VERSION": "pilot-2",
                "VALIDATION_PATTERN_GAID": r"GA[0-9]{2}",
                "VALIDATION_PATTERN_IDFA": r"IA[0-9]{2}",
                "VALIDATION_PATTERN_IDFV": r"IV[0-9]{2}",
                "VALIDATION_PATTERN_UID": r"AF[0-9]{2}",
                "VALIDATION_PATTERN_SINGULAR": r"SI[0-9]{2}",
                "VALIDATION_MAX_INTERVAL_MINUTES": "90",
            }
            with patch.dict(os.environ, env, clear=True):
                settings = load_settings(Path(directory))
            self.assertEqual(settings.validation_rules.version, "pilot-2")
            for kind, good in (("gaid", "GA12"), ("idfa", "IA12"), ("idfv", "IV12"), ("uid", "AF12"), ("singular", "SI12")):
                self.assertEqual(identifier(kind, good, settings.validation_rules), good)
            ui = build_application(settings).bot_data["ui"]
            services = ui.s
            services.users.language(7, "en")
            services.subscriptions.dev_state(7, "active")
            ui.click(7, "device:os:Android")
            ui.text(7, "Phone")
            self.assertEqual(ui.text(7, UUID).kind, "error")
            self.assertEqual(ui.sessions[7]["waiting"], "gaid")
            ui.text(7, "GA12")
            ui.click(7, "device:proxy:none")
            ui.click(7, "device:confirm")
            android = services.devices.list(7)[0]
            self.assertEqual(android["identifiers"]["gaid"], "GA12")
            appsflyer = services.devices.link_game(7, android["id"], "g1", "AF12")
            self.assertEqual(appsflyer["extra_id"], "AF12")
            self.assertEqual(services.devices.link_game(7, android["id"], "g3", "SI12")["extra_id"], "SI12")
            ios = services.devices.add(7, "iOS", "Tablet", "IA12", "IV12")
            self.assertEqual(ios["identifiers"], {"idfa": "IA12", "idfv": "IV12"})
            operation = [{"template": "tutorial_complete", "values": []}]
            with self.assertRaises(ValidationError):
                services.plans.add(7, android["id"], appsflyer["id"], "uniform", "91m", operation)
            self.assertEqual(len(services.plans.add(7, android["id"], appsflyer["id"], "uniform", "90m", operation)["operations"]), 1)

    def test_invalid_configuration_fails_at_load(self):
        with tempfile.TemporaryDirectory() as directory:
            for key, value in (
                ("VALIDATION_RULES_VERSION", ""),
                ("VALIDATION_PATTERN_IDFA", "["),
                ("VALIDATION_PATTERN_IDFV", ""),
                ("VALIDATION_MAX_INTERVAL_MINUTES", "0"),
                ("VALIDATION_MAX_INTERVAL_MINUTES", "-1"),
                ("VALIDATION_MAX_INTERVAL_MINUTES", "1.5"),
                ("VALIDATION_MAX_INTERVAL_MINUTES", "999999999999999999999"),
            ):
                with self.subTest(key=key, value=value), patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:TEST", key: value}, clear=True):
                    with self.assertRaisesRegex(ValueError, key):
                        load_settings(Path(directory))

    def test_invalid_values_stay_validation_errors_with_permissive_regex_and_huge_count(self):
        rules = replace(DEFAULT_VALIDATION_RULES, gaid_pattern=r".*")
        with self.assertRaises(ValidationError):
            identifier("gaid", "   ", rules)
        with self.assertRaises(ValidationError):
            interval("9" * 5000 + "d", rules)

    def test_configured_large_interval_cannot_overflow_schedule(self):
        rules = replace(DEFAULT_VALIDATION_RULES, max_interval_minutes=200000000000)
        with self.assertRaises(ValidationError):
            due_dates("multiple", [{"id": "1", "interval": "99999999d"}], datetime(2026, 1, 1, tzinfo=timezone.utc), rules=rules)


if __name__ == "__main__":
    unittest.main()
