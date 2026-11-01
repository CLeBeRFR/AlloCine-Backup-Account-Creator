"""Sauvegarde au format CSV des notes de films et séries d'un profil AlloCiné.

Utilisation :
    python allocine_backup_account_creator.py <URL du profil utilisateur>
"""

import argparse
import csv
import re
import sys
from typing import List, Tuple

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit(
        "Modules manquants, installez-les avec : "
        "pip install -r requirements.txt"
    )

URL_ALLOCINE = "https://www.allocine.fr"
TYPES_MEDIA = ("films", "series")
DELAI_MAXIMUM_REQUETE = 30  # secondes

# AlloCiné préfixe le texte alternatif des affiches par "poster de "
PREFIXE_AFFICHE = "poster de "
REGEX_IDENTIFIANT = re.compile(r"membre-[A-Z0-9]+")
# La note est portée par une classe CSS du type "n35" pour 3,5 étoiles
REGEX_NOTE = re.compile(r"^n([0-5])([05])$")

Note = Tuple[str, str]


def extraire_identifiant(url_profil: str) -> str:
    """Retourne l'identifiant "membre-XXX" contenu dans l'URL du profil."""
    correspondance = REGEX_IDENTIFIANT.search(url_profil)
    if correspondance is None:
        raise ValueError(
            "l'identifiant utilisateur n'a pas pu être récupéré, "
            "vérifiez l'URL du profil"
        )
    return correspondance.group(0)


def recuperer_page(
    session: requests.Session,
    identifiant: str,
    type_media: str,
    numero_page: int,
) -> BeautifulSoup:
    """Télécharge et analyse une page de la liste des médias notés."""
    reponse = session.get(
        f"{URL_ALLOCINE}/{identifiant}/{type_media}/",
        params={"page": numero_page},
        timeout=DELAI_MAXIMUM_REQUETE,
    )
    reponse.raise_for_status()
    return BeautifulSoup(reponse.text, "html.parser")


def compter_pages(page: BeautifulSoup) -> int:
    """Lit le numéro de la dernière page dans la pagination."""
    numeros = [
        int(texte)
        for texte in (
            element.get_text(strip=True)
            for element in page.select(".pagination-item-holder .item")
        )
        if texte.isdigit()
    ]
    return max(numeros, default=1)


def extraire_note(carte: BeautifulSoup) -> str:
    """Retourne la note d'une carte au format "3,5", ou "" si absente."""
    etoiles = carte.select_one(".rating-mdl.stareval-stars")
    if etoiles is not None:
        for classe in etoiles.get("class", []):
            correspondance = REGEX_NOTE.match(classe)
            if correspondance:
                return ",".join(correspondance.groups())
    return ""


def extraire_notes(page: BeautifulSoup) -> List[Note]:
    """Retourne les couples (nom, note) des médias d'une page."""
    notes = []
    for carte in page.select("div.userprofile-entity-card-simple"):
        nom = carte.find("img")["alt"]
        if nom.startswith(PREFIXE_AFFICHE):
            nom = nom[len(PREFIXE_AFFICHE):]
        notes.append((nom, extraire_note(carte)))
    return notes


def sauvegarder_notes(
    session: requests.Session, identifiant: str, type_media: str
) -> None:
    """Récupère toutes les notes d'un type de média et les écrit en CSV."""
    print(
        "Démarrage de la récupération des notes pour le type de média "
        f"{type_media}"
    )
    premiere_page = recuperer_page(session, identifiant, type_media, 1)
    nombre_de_pages = compter_pages(premiere_page)
    print(f"Nombre de pages : {nombre_de_pages}")
    print("Extraction des pages, le processus peut être long...")

    notes = extraire_notes(premiere_page)
    for numero_page in range(2, nombre_de_pages + 1):
        page = recuperer_page(session, identifiant, type_media, numero_page)
        notes.extend(extraire_notes(page))

    nom_fichier = f"liste_notes_{type_media}.csv"
    with open(nom_fichier, "w", encoding="utf-8", newline="") as fichier:
        ecrivain = csv.writer(fichier, delimiter=";", lineterminator="\n")
        ecrivain.writerow(("Nom", "Note"))
        ecrivain.writerows(notes)
    print(
        f"Fin de la récupération des notes, {len(notes)} {type_media} "
        "sauvegardés"
    )


def main() -> int:
    """Point d'entrée du script."""
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument(
        "url_profil",
        help="URL du profil, ex. https://www.allocine.fr/membre-XXXX/",
    )
    arguments = analyseur.parse_args()

    try:
        identifiant = extraire_identifiant(arguments.url_profil)
    except ValueError as erreur:
        analyseur.error(str(erreur))

    with requests.Session() as session:
        try:
            for type_media in TYPES_MEDIA:
                sauvegarder_notes(session, identifiant, type_media)
        except requests.RequestException as erreur:
            print(
                f"Erreur de connexion à AlloCiné : {erreur}", file=sys.stderr
            )
            return 1

    print(
        "Les fichiers ont été sauvegardés dans le répertoire courant "
        "au format CSV"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
