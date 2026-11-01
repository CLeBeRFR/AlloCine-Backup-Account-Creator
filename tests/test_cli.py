"""Tests of the command line interface, with the export mocked out."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import requests

from allocine_backup import cli
from allocine_backup.scraper import ExportResult

PROFILE_URL = "https://www.allocine.fr/membre-Z20240115093042/"


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_dir.cleanup)
        self.output_dir = Path(temporary_dir.name)
        patcher = mock.patch.object(cli, "export_profile")
        self.export_profile = patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        """Run the CLI and return its exit code, stdout and stderr."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = cli.main(list(args))
            except SystemExit as exit_:
                code = exit_.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_prints_created_files(self) -> None:
        path = self.output_dir / "liste_notes_films.csv"
        self.export_profile.return_value = [ExportResult("films", path, 2)]

        code, stdout, _ = self.run_cli(PROFILE_URL, "-d", str(self.output_dir))

        self.assertEqual(code, 0)
        self.assertIn(str(path.resolve()), stdout)
        self.export_profile.assert_called_once_with(
            PROFILE_URL, self.output_dir, senscritique=False
        )

    def test_passes_senscritique_option(self) -> None:
        self.export_profile.return_value = []

        code, _, _ = self.run_cli(PROFILE_URL, "-d", str(self.output_dir),
                                  "--senscritique")

        self.assertEqual(code, 0)
        self.export_profile.assert_called_once_with(
            PROFILE_URL, self.output_dir, senscritique=True
        )

    def test_rejects_missing_output_dir_before_exporting(self) -> None:
        code, _, stderr = self.run_cli(
            PROFILE_URL, "-d", str(self.output_dir / "absent")
        )
        self.assertEqual(code, 2)
        self.assertIn("n'existe pas", stderr)
        self.export_profile.assert_not_called()

    def test_reports_invalid_url_as_usage_error(self) -> None:
        self.export_profile.side_effect = ValueError("URL invalide")
        code, _, stderr = self.run_cli("https://www.allocine.fr/film/",
                                       "-d", str(self.output_dir))
        self.assertEqual(code, 2)
        self.assertIn("URL invalide", stderr)

    def test_reports_network_error(self) -> None:
        self.export_profile.side_effect = requests.ConnectionError("hors")
        code, _, stderr = self.run_cli(PROFILE_URL, "-d",
                                       str(self.output_dir))
        self.assertEqual(code, 1)
        self.assertIn("Erreur de connexion", stderr)


if __name__ == "__main__":
    unittest.main()
