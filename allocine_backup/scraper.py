"""Fetch the ratings of an AlloCiné profile and export them to CSV files.

AlloCiné has no export feature nor public API, so ratings are scraped from
the profile pages ``/membre-<id>/films/`` and ``/membre-<id>/series/``.

Two formats are written:

* ``liste_notes_<type>.csv``: one file per media type, the backup format of
  this project, meant to be opened in a spreadsheet.
* ``import_senscritique.csv`` (optional): one file in the import format of
  SensCritique, described at
  https://www.senscritique.com/l-edito/post/comment_importer_ses_notes_sur_senscritique
"""

import base64
import binascii
import csv
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ALLOCINE_URL = "https://www.allocine.fr"
# URL path segments of the profile pages, also used in the CSV file names
MEDIA_TYPES = ("films", "series")
REQUEST_TIMEOUT = 30  # seconds
# AlloCiné (behind Cloudflare) blocks clients for a few minutes, with 429
# responses, after about 250 requests at 15 per second. Requests are sent
# one at a time and spaced out to stay well below that rate.
MIN_REQUEST_INTERVAL = 0.5  # seconds
# Should a block happen anyway, retry with an exponential backoff (10, 20,
# 40... seconds, at most 2 minutes) or the delay the server asks for
RETRY_POLICY = Retry(
    total=6,
    backoff_factor=5,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET",),
    respect_retry_after_header=True,
)

MEMBER_ID_PATTERN = re.compile(r"membre-[A-Z0-9]+")
# Each card holds the poster of the title, whose alt text is prefixed
POSTER_ALT_PREFIX = "poster de "
# The rating is encoded in a CSS class: "n35" means 3.5 stars
RATING_CLASS_PATTERN = re.compile(r"^n([0-5])([05])$")
# Some links are obfuscated: the URL is base64-encoded in a CSS class and
# interleaved with this marker
OBFUSCATED_LINK_MARKER = "ACr"

# Kept identical to the files produced by earlier versions. The BOM lets
# Excel detect UTF-8, and ";" with a decimal comma matches French locales.
ALLOCINE_CSV_HEADER = ("Nom", "Note")
ALLOCINE_CSV_ENCODING = "utf-8-sig"
ALLOCINE_CSV_DELIMITER = ";"

SENSCRITIQUE_FILE_NAME = "import_senscritique.csv"
SENSCRITIQUE_STEP = "senscritique"
SENSCRITIQUE_CSV_HEADER = (
    "universe", "title", "release_date", "rating",
    "is_wishlisted", "is_recommended", "is_done",
)
SENSCRITIQUE_UNIVERSES = {"films": "movie", "series": "tvshow"}
SENSCRITIQUE_MAX_TITLE_LENGTH = 255
# SensCritique dates titles by their original release. For films, the
# production year matches it more often than the French release date
# shown at the top of the page (e.g. 1975 rather than 1976 for "Les Dents de
# la mer"). For series, the broadcast years ("2013 - 2020") come first.
RELEASE_INFO_MARKER = 'class="meta-body-item meta-body-info"'
RELEASE_INFO_LENGTH = 600
PRODUCTION_YEAR_PATTERN = re.compile(
    r"Année de production</span>\s*<span[^>]*>\s*(\d{4})"
)
YEAR_PATTERN = re.compile(r"\b(1[89]\d\d|2\d{3})\b")

LogCallback = Callable[[str], None]
# Called with the current step (a media type or SENSCRITIQUE_STEP), the
# number of items processed and the total number of items of the step
ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class RatedTitle:
    """A title listed on a profile."""

    media_type: str
    title: str
    # Rating out of 10 (one point per half star), None if not rated
    half_stars: int | None
    # Path of the AlloCiné page of the title, e.g. "/film/fichefilm_...html"
    page_path: str | None


@dataclass(frozen=True)
class ExportResult:
    """A CSV file written by an export."""

    step: str
    path: Path
    count: int


def ignore_progress(step: str, done: int, total: int) -> None:
    """Default progress callback, which does nothing."""


class ThrottledSession(requests.Session):
    """HTTP session that waits a minimum interval between two requests.

    Not thread-safe: requests must be sent from a single thread.
    """

    def __init__(self, min_interval: float) -> None:
        super().__init__()
        self._min_interval = min_interval
        self._next_request_time = 0.0

    def request(self, *args, **kwargs) -> requests.Response:
        delay = self._next_request_time - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next_request_time = time.monotonic() + self._min_interval
        return super().request(*args, **kwargs)


def create_session() -> requests.Session:
    """Return a throttled session that retries failed requests."""
    session = ThrottledSession(MIN_REQUEST_INTERVAL)
    session.mount("https://", HTTPAdapter(max_retries=RETRY_POLICY))
    return session


def parse_member_id(profile_url: str) -> str:
    """Return the ``membre-<id>`` part of a profile URL.

    Raises ValueError if the URL does not contain a member id.
    """
    match = MEMBER_ID_PATTERN.search(profile_url)
    if match is None:
        raise ValueError(
            "l'identifiant utilisateur n'a pas pu être récupéré, "
            "vérifiez l'URL du profil"
        )
    return match.group(0)


def fetch_profile_page(
    session: requests.Session, member_id: str, media_type: str, page: int
) -> BeautifulSoup:
    """Download and parse one page of the rated titles of a profile."""
    response = session.get(
        f"{ALLOCINE_URL}/{member_id}/{media_type}/",
        params={"page": page},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def count_pages(page: BeautifulSoup) -> int:
    """Return the number of pages announced by the pagination.

    The first page always links to the last one. Profiles that fit on a
    single page have no pagination.
    """
    page_numbers = [
        int(text)
        for text in (
            item.get_text(strip=True)
            for item in page.select(".pagination-item-holder .item")
        )
        if text.isdigit()
    ]
    return max(page_numbers, default=1)


def parse_half_stars(card: Tag) -> int | None:
    """Return the rating of a card out of 10, or None if missing."""
    stars = card.select_one(".rating-mdl.stareval-stars")
    if stars is not None:
        for css_class in stars.get("class", []):
            match = RATING_CLASS_PATTERN.match(css_class)
            if match:
                return int(match.group(1)) * 2 + (match.group(2) == "5")
    return None


def decode_obfuscated_link(css_class: str) -> str | None:
    """Decode a link hidden in a CSS class, or return None."""
    if not css_class.startswith(OBFUSCATED_LINK_MARKER):
        return None
    try:
        return base64.b64decode(
            css_class.replace(OBFUSCATED_LINK_MARKER, ""), validate=True
        ).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def parse_page_path(card: Tag) -> str | None:
    """Return the path of the title page linked by a card."""
    link = card.select_one(".meta-title-link")
    if link is None:
        return None
    if link.has_attr("href"):
        return link["href"]
    for css_class in link.get("class", []):
        path = decode_obfuscated_link(css_class)
        if path is not None:
            return path
    return None


def parse_ratings(page: BeautifulSoup, media_type: str) -> list[RatedTitle]:
    """Return the titles listed on a profile page."""
    ratings = []
    for card in page.select("div.userprofile-entity-card-simple"):
        title = card.find("img")["alt"]
        if title.startswith(POSTER_ALT_PREFIX):
            title = title[len(POSTER_ALT_PREFIX):]
        ratings.append(RatedTitle(
            media_type, title, parse_half_stars(card), parse_page_path(card)
        ))
    return ratings


def fetch_ratings(
    session: requests.Session,
    member_id: str,
    media_type: str,
    log: LogCallback = print,
    on_progress: ProgressCallback = ignore_progress,
) -> list[RatedTitle]:
    """Download every rated title of one media type."""
    log(
        "Démarrage de la récupération des notes pour le type de média "
        f"{media_type}"
    )
    first_page = fetch_profile_page(session, member_id, media_type, 1)
    page_count = count_pages(first_page)
    log(f"Nombre de pages : {page_count}")
    log("Extraction des pages, le processus peut être long...")

    ratings = parse_ratings(first_page, media_type)
    on_progress(media_type, 1, page_count)
    for page_number in range(2, page_count + 1):
        page = fetch_profile_page(session, member_id, media_type, page_number)
        ratings.extend(parse_ratings(page, media_type))
        on_progress(media_type, page_number, page_count)
    log(
        f"Fin de la récupération des notes, {len(ratings)} {media_type} "
        "sauvegardés"
    )
    return ratings


def parse_production_year(html: str) -> int | None:
    """Return the production year of a title page, or None."""
    match = PRODUCTION_YEAR_PATTERN.search(html)
    return int(match.group(1)) if match else None


def parse_first_release_year(html: str) -> int | None:
    """Return the first year shown next to the title, or None.

    It is the French release date of a film, or the first broadcast year
    of a series.
    """
    start = html.find(RELEASE_INFO_MARKER)
    if start == -1:
        return None
    release_info = html[start:start + RELEASE_INFO_LENGTH]
    match = YEAR_PATTERN.search(re.sub(r"<[^>]*>", " ", release_info))
    return int(match.group(1)) if match else None


def parse_release_year(html: str, media_type: str) -> int | None:
    """Return the year SensCritique most likely uses for a title."""
    if media_type == "films":
        return parse_production_year(html) or parse_first_release_year(html)
    return parse_first_release_year(html) or parse_production_year(html)


def fetch_release_year(
    session: requests.Session, page_path: str, media_type: str
) -> int | None:
    """Return the release year of a title, or None if it is unknown.

    A missing page (404) is not an error: AlloCiné sometimes removes
    titles. Other failures, such as a lost connection, are raised.
    """
    response = session.get(f"{ALLOCINE_URL}{page_path}",
                           timeout=REQUEST_TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return parse_release_year(response.text, media_type)


def fetch_release_years(
    session: requests.Session,
    ratings: list[RatedTitle],
    on_progress: ProgressCallback = ignore_progress,
) -> dict[str, int | None]:
    """Fetch the release year of each title, keyed by page path."""
    media_types = {
        rating.page_path: rating.media_type
        for rating in ratings if rating.page_path
    }
    years: dict[str, int | None] = {}
    on_progress(SENSCRITIQUE_STEP, 0, len(media_types))
    for path, media_type in media_types.items():
        years[path] = fetch_release_year(session, path, media_type)
        on_progress(SENSCRITIQUE_STEP, len(years), len(media_types))
    return years


def format_allocine_rating(half_stars: int | None) -> str:
    """Format a rating as stars with a decimal comma, e.g. ``"3,5"``."""
    if half_stars is None:
        return ""
    return f"{half_stars // 2},{5 if half_stars % 2 else 0}"


def write_allocine_csv(path: Path, ratings: Iterable[RatedTitle]) -> None:
    """Write ratings in the backup format, overwriting any existing file."""
    with open(path, "w", encoding=ALLOCINE_CSV_ENCODING, newline="") as file:
        writer = csv.writer(
            file, delimiter=ALLOCINE_CSV_DELIMITER, lineterminator="\n"
        )
        writer.writerow(ALLOCINE_CSV_HEADER)
        writer.writerows(
            (rating.title, format_allocine_rating(rating.half_stars))
            for rating in ratings
        )


def write_senscritique_csv(
    path: Path,
    ratings: Iterable[RatedTitle],
    years: dict[str, int | None],
) -> int:
    """Write ratings in the SensCritique import format.

    Every title of the profile has been seen, hence ``is_done``. Returns
    the number of rows written.
    """
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(SENSCRITIQUE_CSV_HEADER)
        for rating in ratings:
            title = rating.title.strip()[:SENSCRITIQUE_MAX_TITLE_LENGTH]
            if not title:
                continue
            year = years.get(rating.page_path) if rating.page_path else None
            writer.writerow((
                SENSCRITIQUE_UNIVERSES[rating.media_type],
                title,
                year or "",
                rating.half_stars or "",
                "false",
                "false",
                "true",
            ))
            count += 1
    return count


def export_profile(
    profile_url: str,
    output_dir: Path,
    senscritique: bool = False,
    log: LogCallback = print,
    on_progress: ProgressCallback = ignore_progress,
) -> list[ExportResult]:
    """Export the film and series ratings of a profile.

    With ``senscritique``, the release year of every title is also fetched
    to write the SensCritique import file, which takes one request per
    title.

    Raises ValueError for an invalid profile URL,
    requests.RequestException for network errors and OSError when a file
    cannot be written. The backup files are written before the release
    years are fetched, so they are kept if that step fails.
    """
    member_id = parse_member_id(profile_url)
    results = []
    all_ratings: list[RatedTitle] = []
    with create_session() as session:
        for media_type in MEDIA_TYPES:
            ratings = fetch_ratings(
                session, member_id, media_type, log, on_progress
            )
            path = output_dir / f"liste_notes_{media_type}.csv"
            write_allocine_csv(path, ratings)
            results.append(ExportResult(media_type, path, len(ratings)))
            all_ratings.extend(ratings)

        if senscritique:
            log(
                "Récupération des années de sortie pour SensCritique "
                f"({len(all_ratings)} titres), le processus peut être "
                "long..."
            )
            years = fetch_release_years(session, all_ratings, on_progress)
            path = output_dir / SENSCRITIQUE_FILE_NAME
            count = write_senscritique_csv(path, all_ratings, years)
            missing = sum(
                1 for rating in all_ratings
                if years.get(rating.page_path) is None
            )
            log(
                f"Fichier SensCritique créé, {count} titres dont {missing} "
                "sans année de sortie"
            )
            results.append(ExportResult(SENSCRITIQUE_STEP, path, count))
    return results
