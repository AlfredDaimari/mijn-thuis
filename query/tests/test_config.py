import tempfile
import unittest
from pathlib import Path

from query_service.config import load_settings


class SettingsTests(unittest.TestCase):
    def test_loads_email_and_app_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            values = Path(directory) / "values.yaml"
            values.write_text("email: person@yahoo.com\npassword: app-password\n")
            settings = load_settings(values)

        self.assertEqual(settings.email, "person@yahoo.com")
        self.assertEqual(settings.password, "app-password")

    def test_rejects_missing_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            values = Path(directory) / "values.yaml"
            values.write_text("email: person@yahoo.com\n")
            with self.assertRaisesRegex(ValueError, "password"):
                load_settings(values)

    def test_uses_configured_database_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            values = Path(directory) / "values.yaml"
            values.write_text(
                "email: person@yahoo.com\npassword: app-password\n"
                "staging_database: state/staging.sqlite3\n"
                "listings_database: state/listings.sqlite3\n"
            )
            settings = load_settings(values)

        self.assertEqual(settings.staging_database, "state/staging.sqlite3")
        self.assertEqual(settings.listings_database, "state/listings.sqlite3")
