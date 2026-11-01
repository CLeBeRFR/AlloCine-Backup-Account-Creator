"""Tests of the scraper, run offline against HTML shaped like AlloCiné's."""

import base64
import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests
from bs4 import BeautifulSoup

from allocine_backup import scraper
from allocine_backup.scraper import RatedTitle

MEMBER_ID = "membre-Z20240115093042"
PROFILE_URL = f"https://www.allocine.fr/{MEMBER_ID}/"


def obfuscate(path: str) -> str:
    """Hide a link the way AlloCiné does, in a CSS class."""
    encoded = base64.b64encode(path.encode()).decode()
    return f"ACr{encoded[:2]}ACr{encoded[2:]}"


def make_card(title: str, rating_class: str | None,
              page_path: str | None = None, obfuscated: bool = False) -> str:
    """Return the HTML of a profile card, as served by AlloCiné."""
    stars = (
        '<div class="stareval stareval-medium">'
        f'<div class="rating-mdl {rating_class} stareval-stars">'
        '<div class="star icon"></div></div></div>'
        if rating_class else ""
    )
    link = ""
    if page_path and obfuscated:
        link = (f'<span class="{obfuscate(page_path)} meta-title '
                f'meta-title-link">{title}</span>')
    elif page_path:
        link = (f'<a class="meta-title meta-title-link" href="{page_path}">'
                f'{title}</a>')
    return (
        '<div class="card entity-card-simple '
        'userprofile-entity-card-simple">'
        f'<figure class="thumbnail">{stars}'
        f'<img class="thumbnail-img" src="x.jpg" alt="poster de {title}" />'
        f'</figure><div class="meta">{link}</div></div>'
    )


def make_profile_page(cards: list[str], last_page: int | None = None) -> str:
    """Return a profile page, with a pagination up to ``last_page``."""
    pagination = ""
    if last_page is not None:
        items = "".join(
            f'<span class="button button-md item">{number}</span>'
            for number in (1, 2, last_page)
        )
        pagination = (
            '<nav class="pagination cf"><div class="pagination-item-holder">'
            f'{items}<span class="button">...</span></div></nav>'
        )
    return f"<html><body>{''.join(cards)}{pagination}</body></html>"


def make_title_page(release_info: str = "",
                    production_year: str | None = None) -> str:
    """Return a title page with its release information."""
    production = (
        '<div class="item"><span class="what light">Année de production'
        f'</span> <span class="that">{production_year}</span></div>'
        if production_year else ""
    )
    return (
        '<div class="meta-body"><div class="meta-body-item meta-body-info">'
        f'{release_info}</div></div>{production}'
    )


def parse(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error",
                                     response=self)


class FakeSession:
    """Serves pages from a mapping, and 404 for anything else.

    Profile pages are keyed by (media type, page number), title pages by
    their path. A value can also be an exception to raise.
    """

    def __init__(self, pages: dict) -> None:
        self.pages = pages

    def get(self, url: str, params: dict | None = None,
            timeout: int | None = None) -> FakeResponse:
        if params is None:
            key = url.removeprefix(scraper.ALLOCINE_URL)
        else:
            key = (url.rstrip("/").rsplit("/", 1)[1], params["page"])
        page = self.pages.get(key)
        if isinstance(page, Exception):
            raise page
        if page is None:
            return FakeResponse("", status_code=404)
        return FakeResponse(page)

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc_info) -> None:
        pass


def read_csv(path: Path, encoding: str, delimiter: str) -> list[list[str]]:
    with open(path, encoding=encoding, newline="") as file:
        return list(csv.reader(file, delimiter=delimiter))


class TemporaryDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_dir.cleanup)
        self.output_dir = Path(temporary_dir.name)


class CreateSessionTest(unittest.TestCase):
    def test_retries_throttled_requests(self) -> None:
        with scraper.create_session() as session:
            retries = session.get_adapter(scraper.ALLOCINE_URL).max_retries
        self.assertIn(429, retries.status_forcelist)
        self.assertTrue(retries.respect_retry_after_header)

    def test_spaces_out_requests(self) -> None:
        session = scraper.ThrottledSession(min_interval=10)
        self.addCleanup(session.close)
        clock = [100.0]
        with mock.patch.object(scraper.time, "monotonic",
                               side_effect=lambda: clock[0]), \
                mock.patch.object(scraper.time, "sleep") as sleep, \
                mock.patch.object(requests.Session, "request"):
            session.request("GET", "https://example.org/1")
            clock[0] += 3
            session.request("GET", "https://example.org/2")
        sleep.assert_called_once_with(7)


class ParseMemberIdTest(unittest.TestCase):
    def test_extracts_id_from_profile_url(self) -> None:
        self.assertEqual(scraper.parse_member_id(PROFILE_URL), MEMBER_ID)

    def test_extracts_id_from_subpage_url(self) -> None:
        self.assertEqual(
            scraper.parse_member_id(f"{PROFILE_URL}films/?page=3"), MEMBER_ID
        )

    def test_rejects_url_without_member_id(self) -> None:
        with self.assertRaises(ValueError):
            scraper.parse_member_id("https://www.allocine.fr/film/")


class CountPagesTest(unittest.TestCase):
    def test_reads_last_page_from_pagination(self) -> None:
        page = parse(make_profile_page([], last_page=29))
        self.assertEqual(scraper.count_pages(page), 29)

    def test_defaults_to_one_without_pagination(self) -> None:
        self.assertEqual(scraper.count_pages(parse(make_profile_page([]))), 1)


class ParseRatingsTest(unittest.TestCase):
    def test_parses_title_rating_and_page_path(self) -> None:
        page = parse(make_profile_page([
            make_card("Limitless", "n40", "/film/fichefilm_gen_cfilm=1.html"),
            make_card("L&#039;Accusé", "n35",
                      "/film/fichefilm_gen_cfilm=2.html", obfuscated=True),
            make_card("La Zone d'intérêt", "n05"),
        ]))
        self.assertEqual(scraper.parse_ratings(page, "films"), [
            RatedTitle("films", "Limitless", 8,
                       "/film/fichefilm_gen_cfilm=1.html"),
            RatedTitle("films", "L'Accusé", 7,
                       "/film/fichefilm_gen_cfilm=2.html"),
            RatedTitle("films", "La Zone d'intérêt", 1, None),
        ])

    def test_keeps_title_without_rating(self) -> None:
        page = parse(make_profile_page([make_card("Sans note", None)]))
        self.assertEqual(scraper.parse_ratings(page, "series"),
                         [RatedTitle("series", "Sans note", None, None)])

    def test_ignores_unrelated_css_classes(self) -> None:
        page = parse(make_profile_page([make_card("Bizarre", "n99")]))
        self.assertIsNone(scraper.parse_ratings(page, "films")[0].half_stars)


class DecodeObfuscatedLinkTest(unittest.TestCase):
    def test_decodes_allocine_link(self) -> None:
        self.assertEqual(
            scraper.decode_obfuscated_link(
                "ACrL2ZACrpbG0vZmljaGVmaWxtX2dlbl9jZmlsbT0yOTQyODEuaHRtbA=="
            ),
            "/film/fichefilm_gen_cfilm=294281.html",
        )

    def test_ignores_regular_classes(self) -> None:
        self.assertIsNone(scraper.decode_obfuscated_link("meta-title"))
        self.assertIsNone(scraper.decode_obfuscated_link("ACr!!!"))


class FormatAllocineRatingTest(unittest.TestCase):
    def test_formats_half_stars_with_decimal_comma(self) -> None:
        self.assertEqual(scraper.format_allocine_rating(1), "0,5")
        self.assertEqual(scraper.format_allocine_rating(8), "4,0")
        self.assertEqual(scraper.format_allocine_rating(10), "5,0")
        self.assertEqual(scraper.format_allocine_rating(None), "")


class ParseReleaseYearTest(unittest.TestCase):
    def test_prefers_production_year_for_films(self) -> None:
        html = make_title_page(
            '<span class="date"> 21 janvier 1976 </span> en salle', "1975"
        )
        self.assertEqual(scraper.parse_release_year(html, "films"), 1975)

    def test_falls_back_to_release_date_for_films(self) -> None:
        html = make_title_page('<span class="date"> 8 juin 2011 </span>')
        self.assertEqual(scraper.parse_release_year(html, "films"), 2011)

    def test_uses_first_broadcast_year_for_series(self) -> None:
        html = make_title_page("2013 - 2020 | 52 min", "2012")
        self.assertEqual(scraper.parse_release_year(html, "series"), 2013)

    def test_returns_none_when_unknown(self) -> None:
        html = make_title_page("Date de sortie inconnue")
        self.assertIsNone(scraper.parse_release_year(html, "films"))


class WriteCsvTest(TemporaryDirTestCase):
    RATINGS = [
        RatedTitle("films", "Un", 2, "/film/1"),
        RatedTitle("films", "Deux, le retour; encore", 9, "/film/2"),
        RatedTitle("series", "Trois", None, "/series/3"),
        RatedTitle("series", "Quatre", 10, None),
    ]

    def test_allocine_csv_matches_previous_versions(self) -> None:
        path = self.output_dir / "notes.csv"
        scraper.write_allocine_csv(path, self.RATINGS)

        content = path.read_bytes()
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM")
        self.assertNotIn(b"\r\n", content)
        self.assertEqual(read_csv(path, "utf-8-sig", ";"), [
            ["Nom", "Note"],
            ["Un", "1,0"],
            ["Deux, le retour; encore", "4,5"],
            ["Trois", ""],
            ["Quatre", "5,0"],
        ])

    def test_senscritique_csv_follows_import_format(self) -> None:
        path = self.output_dir / "import.csv"
        years = {"/film/1": 1999, "/film/2": 2004, "/series/3": None}

        count = scraper.write_senscritique_csv(path, self.RATINGS, years)

        self.assertEqual(count, 4)
        self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(read_csv(path, "utf-8", ","), [
            ["universe", "title", "release_date", "rating",
             "is_wishlisted", "is_recommended", "is_done"],
            ["movie", "Un", "1999", "2", "false", "false", "true"],
            ["movie", "Deux, le retour; encore", "2004", "9",
             "false", "false", "true"],
            ["tvshow", "Trois", "", "", "false", "false", "true"],
            ["tvshow", "Quatre", "", "10", "false", "false", "true"],
        ])

    def test_senscritique_csv_truncates_long_titles(self) -> None:
        path = self.output_dir / "import.csv"
        scraper.write_senscritique_csv(
            path, [RatedTitle("films", "x" * 300, 6, None)], {}
        )
        self.assertEqual(len(read_csv(path, "utf-8", ",")[1][1]), 255)


class ExportProfileTest(TemporaryDirTestCase):
    PAGES = {
        ("films", 1): make_profile_page(
            [make_card("Un", "n10", "/film/1")], last_page=3
        ),
        ("films", 2): make_profile_page(
            [make_card("Deux", "n20", "/film/2", obfuscated=True)]
        ),
        ("films", 3): make_profile_page(
            [make_card("Trois", "n50", "/film/3")]
        ),
        ("series", 1): make_profile_page(
            [make_card("Série", "n45", "/series/1")]
        ),
        "/film/1": make_title_page("", "2001"),
        "/film/2": make_title_page("", "2002"),
        # "/film/3" is missing: AlloCiné removed the page
        "/series/1": make_title_page("2010 - 2015"),
    }

    def export(self, pages: dict, senscritique: bool,
               progress: list | None = None) -> list[scraper.ExportResult]:
        progress = [] if progress is None else progress
        with mock.patch.object(scraper, "create_session",
                               return_value=FakeSession(pages)):
            return scraper.export_profile(
                PROFILE_URL, self.output_dir, senscritique=senscritique,
                log=lambda _message: None,
                on_progress=lambda *args: progress.append(args),
            )

    def test_writes_one_backup_file_per_media_type(self) -> None:
        progress = []
        results = self.export(self.PAGES, senscritique=False,
                              progress=progress)

        self.assertEqual(results, [
            scraper.ExportResult(
                "films", self.output_dir / "liste_notes_films.csv", 3),
            scraper.ExportResult(
                "series", self.output_dir / "liste_notes_series.csv", 1),
        ])
        self.assertEqual(progress, [("films", 1, 3), ("films", 2, 3),
                                    ("films", 3, 3), ("series", 1, 1)])
        self.assertEqual(
            read_csv(results[0].path, "utf-8-sig", ";")[1:],
            [["Un", "1,0"], ["Deux", "2,0"], ["Trois", "5,0"]],
        )
        self.assertFalse(
            (self.output_dir / scraper.SENSCRITIQUE_FILE_NAME).exists()
        )

    def test_writes_senscritique_file_with_release_years(self) -> None:
        progress = []
        results = self.export(self.PAGES, senscritique=True,
                              progress=progress)

        self.assertEqual(results[-1], scraper.ExportResult(
            "senscritique",
            self.output_dir / scraper.SENSCRITIQUE_FILE_NAME,
            4,
        ))
        self.assertEqual(progress[-1], ("senscritique", 4, 4))
        rows = read_csv(results[-1].path, "utf-8", ",")[1:]
        self.assertEqual([row[:4] for row in rows], [
            ["movie", "Un", "2001", "2"],
            ["movie", "Deux", "2002", "4"],
            ["movie", "Trois", "", "10"],
            ["tvshow", "Série", "2010", "9"],
        ])

    def test_keeps_backup_files_when_year_lookup_fails(self) -> None:
        pages = dict(self.PAGES)
        pages["/film/2"] = requests.ConnectionError("connexion perdue")

        with self.assertRaises(requests.ConnectionError):
            self.export(pages, senscritique=True)

        self.assertTrue((self.output_dir / "liste_notes_films.csv").exists())
        self.assertTrue((self.output_dir / "liste_notes_series.csv").exists())
        self.assertFalse(
            (self.output_dir / scraper.SENSCRITIQUE_FILE_NAME).exists()
        )

    def test_writes_nothing_when_a_profile_page_fails(self) -> None:
        pages = dict(self.PAGES)
        del pages[("films", 2)]

        with self.assertRaises(requests.HTTPError):
            self.export(pages, senscritique=False)

        self.assertFalse((self.output_dir / "liste_notes_films.csv").exists())


if __name__ == "__main__":
    unittest.main()
