"""Command line interface.

Usage:
    python -m allocine_backup <profile URL> [-d DOSSIER] [--senscritique]
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import requests

from allocine_backup.scraper import export_profile


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="allocine-backup",
        description="Sauvegarde au format CSV des notes de films et séries "
        "d'un profil AlloCiné.",
    )
    parser.add_argument(
        "profile_url",
        metavar="url_profil",
        help="URL du profil, ex. https://www.allocine.fr/membre-XXXX/",
    )
    parser.add_argument(
        "-d",
        "--dossier",
        dest="output_dir",
        metavar="DOSSIER",
        type=Path,
        default=Path.cwd(),
        help="dossier où écrire les fichiers CSV (par défaut : dossier "
        "courant)",
    )
    parser.add_argument(
        "--senscritique",
        action="store_true",
        help="crée aussi un fichier à importer sur SensCritique (une "
        "requête de plus par titre pour obtenir l'année de sortie)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line interface and return the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    # Checked upfront to avoid downloading every page for nothing
    if not args.output_dir.is_dir():
        parser.error(f"le dossier {args.output_dir} n'existe pas")

    try:
        results = export_profile(
            args.profile_url, args.output_dir, senscritique=args.senscritique
        )
    except ValueError as error:
        parser.error(str(error))
    except requests.RequestException as error:
        print(f"Erreur de connexion à AlloCiné : {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Erreur d'écriture du fichier : {error}", file=sys.stderr)
        return 1

    print("Fichiers sauvegardés :")
    for result in results:
        print(f"  {result.path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
