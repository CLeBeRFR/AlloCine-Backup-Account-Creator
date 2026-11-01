"""Graphical user interface, built with CustomTkinter.

Usage:
    python -m allocine_backup.gui
"""

import math
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import requests

from allocine_backup.scraper import (
    MEDIA_TYPES,
    MIN_REQUEST_INTERVAL,
    SENSCRITIQUE_STEP,
    ExportResult,
    export_profile,
    parse_member_id,
)

APP_NAME = "AlloCiné Backup"
# Resolved through the package (not __file__) so that it also works when
# this module is the entry point of the PyInstaller executable
ASSETS_DIR = Path(str(resources.files("allocine_backup") / "assets"))
EVENT_POLL_DELAY = 100  # milliseconds
STEPS = (*MEDIA_TYPES, SENSCRITIQUE_STEP)
STEP_LABELS = {
    "films": "Films",
    "series": "Séries",
    SENSCRITIQUE_STEP: "SensCritique",
}

# Palette derived from the logo: midnight blue background, gold accent.
# Text colours have a contrast ratio of at least 4.5:1 on CARD_BG.
WINDOW_BG = "#111A30"
CARD_BG = "#1B2645"
INPUT_BG = "#111A30"
# Decorative borders (cards, progress bar tracks)
SUBTLE_BORDER = "#2E3B63"
# Borders of interactive elements, 3:1 contrast on CARD_BG
INPUT_BORDER = "#5D6FA3"
TEXT = "#F8FAFC"
TEXT_MUTED = "#A3B1CC"
TEXT_DISABLED = "#6B7899"
ACCENT = "#FFC83D"
ACCENT_HOVER = "#FFD76E"
ACCENT_BUSY = "#9C7C2C"
TEXT_ON_ACCENT = "#16203A"
ERROR = "#F87171"
SUCCESS = "#4ADE80"

WINDOW_PADDING = 28
CARD_PADDING = 24
CARD_RADIUS = 16
INPUT_RADIUS = 10
INPUT_HEIGHT = 42

URL_HINT = "Copie l'adresse de ta page de profil sur allocine.fr"
OUTPUT_DIR_HINT = "Les fichiers CSV y seront créés."
SENSCRITIQUE_LABEL = "Créer aussi un fichier d'import SensCritique"
SENSCRITIQUE_HINT = "Plus long : l'année de chaque titre est récupérée."
SAVE_LABEL = "Sauvegarder mes notes"


def default_output_dir() -> Path:
    """Return the user's Documents folder, or their home folder."""
    documents = Path.home() / "Documents"
    return documents if documents.is_dir() else Path.home()


def format_count(count: int) -> str:
    """Format a number the French way, e.g. ``1 013``."""
    return f"{count:,}".replace(",", " ")


def format_remaining_time(remaining_requests: int) -> str:
    """Estimate the remaining time from the fixed request rate."""
    seconds = remaining_requests * MIN_REQUEST_INTERVAL
    if seconds < 60:
        return "Encore quelques secondes…"
    return f"Encore environ {math.ceil(seconds / 60)} min…"


def open_in_file_manager(folder: Path) -> None:
    """Open a folder in the system file manager."""
    if sys.platform == "win32":
        os.startfile(folder)
        return
    env = dict(os.environ)
    # The PyInstaller bootloader overrides LD_LIBRARY_PATH, which can break
    # system programs started from the executable
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(folder)], env=env)


def app_font(size: int, bold: bool = False) -> ctk.CTkFont:
    """Return the application font at the given size."""
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")


def create_secondary_button(
    parent: ctk.CTkFrame,
    text: str,
    command: Callable[[], None],
    **options,
) -> ctk.CTkButton:
    """Create an outlined button for secondary actions."""
    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        corner_radius=INPUT_RADIUS,
        fg_color="transparent",
        hover_color=SUBTLE_BORDER,
        border_width=1,
        border_color=INPUT_BORDER,
        text_color=TEXT,
        text_color_disabled=TEXT_DISABLED,
        font=app_font(13),
        **options,
    )


class LabeledEntry(ctk.CTkFrame):
    """Text entry with a label above and a hint or error message below.

    Column 1 of the second row is left free for a button next to the entry.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        label: str,
        hint: str,
        placeholder: str | None = None,
    ) -> None:
        super().__init__(parent, fg_color="transparent")
        self._hint = hint
        self.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text=label, font=app_font(13, bold=True), text_color=TEXT
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.entry = ctk.CTkEntry(
            self,
            height=INPUT_HEIGHT,
            corner_radius=INPUT_RADIUS,
            border_width=1,
            border_color=INPUT_BORDER,
            fg_color=INPUT_BG,
            text_color=TEXT,
            placeholder_text=placeholder,
            placeholder_text_color=TEXT_MUTED,
            font=app_font(13),
        )
        self.entry.grid(row=1, column=0, sticky="ew", pady=(6, 4))
        self._message = ctk.CTkLabel(
            self,
            text=hint,
            font=app_font(12),
            text_color=TEXT_MUTED,
            anchor="w",
            height=18,
        )
        self._message.grid(row=2, column=0, columnspan=2, sticky="ew")

    @property
    def value(self) -> str:
        """Entered text, stripped of surrounding whitespace."""
        return self.entry.get().strip()

    def set_value(self, text: str) -> None:
        """Replace the entered text."""
        self.entry.delete(0, tk.END)
        self.entry.insert(0, text)

    def show_error(self, message: str) -> None:
        """Highlight the entry and display an error below it."""
        self.entry.configure(border_color=ERROR)
        self._message.configure(text=message, text_color=ERROR)

    def clear_error(self) -> None:
        """Restore the normal look and the hint."""
        self.entry.configure(border_color=INPUT_BORDER)
        self._message.configure(text=self._hint, text_color=TEXT_MUTED)


class StepProgressRow(ctk.CTkFrame):
    """Progress of one export step: label, status and progress bar.

    Profile pages are counted for the media types, titles for the
    SensCritique step.
    """

    def __init__(self, parent: ctk.CTkFrame, step: str) -> None:
        super().__init__(parent, fg_color="transparent")
        self.columnconfigure(0, weight=1)
        self._unit = "Titre" if step == SENSCRITIQUE_STEP else "Page"
        self._done_unit = "titres" if step == SENSCRITIQUE_STEP else "notes"
        ctk.CTkLabel(
            self, text=STEP_LABELS[step], font=app_font(14, bold=True),
            text_color=TEXT,
        ).grid(row=0, column=0, sticky="w")
        self._status = ctk.CTkLabel(self, font=app_font(13))
        self._status.grid(row=0, column=1, sticky="e")
        self._bar = ctk.CTkProgressBar(
            self, height=6, corner_radius=3, fg_color=SUBTLE_BORDER
        )
        self._bar.grid(row=1, column=0, columnspan=2, sticky="ew",
                       pady=(4, 0))
        self.is_done = False
        self.reset()

    def reset(self, status: str = "En attente") -> None:
        """Show the waiting state, or another idle status."""
        self.is_done = False
        self._status.configure(text=status, text_color=TEXT_MUTED)
        # CustomTkinter draws a small segment even at 0: hide it
        self._bar.configure(progress_color=SUBTLE_BORDER)
        self._bar.set(0)

    def update_progress(self, done: int, total: int) -> None:
        """Show how many items of the step have been processed."""
        self._status.configure(
            text=f"{self._unit} {format_count(done)} sur "
            f"{format_count(total)}",
            text_color=TEXT_MUTED,
        )
        self._bar.configure(progress_color=ACCENT)
        self._bar.set(done / total if total else 1)

    def mark_done(self, count: int) -> None:
        """Show the number of exported items."""
        self.is_done = True
        self._status.configure(
            text=f"✓ {format_count(count)} {self._done_unit}",
            text_color=SUCCESS,
        )
        self._bar.configure(progress_color=SUCCESS)
        self._bar.set(1)


class App(ctk.CTk):
    """Main window.

    The export runs in a worker thread. Tkinter is not thread-safe, so the
    worker only posts events to a queue that the UI thread polls.
    """

    def __init__(self) -> None:
        super().__init__(fg_color=WINDOW_BG)
        self.title(APP_NAME)
        self.resizable(False, False)
        self._logo = tk.PhotoImage(file=str(ASSETS_DIR / "logo-64.png"))
        if sys.platform == "win32":
            # Calling iconbitmap stops CustomTkinter from replacing the icon
            self.iconbitmap(str(ASSETS_DIR / "logo.ico"))
        else:
            self.iconphoto(True, self._logo)

        self._events: queue.Queue = queue.Queue()
        self._is_running = False
        self._output_dir: Path | None = None

        self._build_header()
        self._build_form()
        self._build_progress()
        ctk.CTkLabel(
            self,
            text="Tes notes sont enregistrées uniquement sur ton ordinateur.",
            font=app_font(12),
            text_color=TEXT_MUTED,
        ).pack(pady=(0, WINDOW_PADDING - 8))

        self.bind("<Return>", lambda _event: self._start_export())
        self._url_entry.entry.focus()

    # Layout

    def _build_header(self) -> None:
        """Logo, name and tagline."""
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, padx=WINDOW_PADDING,
                    pady=(WINDOW_PADDING, 20))
        # A plain Tk label: CTkImage would require Pillow
        tk.Label(
            header, image=self._logo, bg=WINDOW_BG, borderwidth=0
        ).pack(side=tk.LEFT, padx=(0, 16))
        texts = ctk.CTkFrame(header, fg_color="transparent")
        texts.pack(side=tk.LEFT, fill=tk.X)
        ctk.CTkLabel(
            texts, text=APP_NAME, font=app_font(24, bold=True),
            text_color=TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            texts,
            text="Exporte toutes tes notes de films et séries en CSV.",
            font=app_font(14),
            text_color=TEXT_MUTED,
        ).pack(anchor="w")

    def _add_card(self) -> ctk.CTkFrame:
        """Add a rounded card to the window and return its content frame."""
        card = ctk.CTkFrame(
            self,
            fg_color=CARD_BG,
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=SUBTLE_BORDER,
        )
        card.pack(fill=tk.X, padx=WINDOW_PADDING, pady=(0, 16))
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill=tk.X, padx=CARD_PADDING, pady=CARD_PADDING)
        content.columnconfigure(0, weight=1)
        return content

    def _build_form(self) -> None:
        """Profile URL, output folder, SensCritique option and main button."""
        card = self._add_card()
        self._url_entry = LabeledEntry(
            card,
            "Profil AlloCiné",
            URL_HINT,
            placeholder="https://www.allocine.fr/membre-…",
        )
        self._url_entry.grid(row=0, column=0, sticky="ew")

        self._dir_entry = LabeledEntry(
            card, "Dossier de destination", OUTPUT_DIR_HINT
        )
        self._dir_entry.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self._dir_entry.set_value(str(default_output_dir()))
        create_secondary_button(
            self._dir_entry,
            "Parcourir",
            self._browse_output_dir,
            width=110,
            height=INPUT_HEIGHT,
        ).grid(row=1, column=1, padx=(8, 0), pady=(6, 4))

        self._senscritique_checkbox = ctk.CTkCheckBox(
            card,
            text=SENSCRITIQUE_LABEL,
            font=app_font(13),
            text_color=TEXT,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            border_color=INPUT_BORDER,
            checkmark_color=TEXT_ON_ACCENT,
            corner_radius=6,
            border_width=2,
            command=self._show_idle_progress,
        )
        self._senscritique_checkbox.grid(row=2, column=0, sticky="w",
                                         pady=(16, 0))
        ctk.CTkLabel(
            card,
            text=SENSCRITIQUE_HINT,
            font=app_font(12),
            text_color=TEXT_MUTED,
            anchor="w",
            height=18,
        ).grid(row=3, column=0, sticky="w", padx=(32, 0), pady=(2, 0))

        self._save_button = ctk.CTkButton(
            card,
            text=SAVE_LABEL,
            height=46,
            corner_radius=INPUT_RADIUS,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_ON_ACCENT,
            text_color_disabled=TEXT_ON_ACCENT,
            font=app_font(15, bold=True),
            command=self._start_export,
        )
        self._save_button.grid(row=4, column=0, sticky="ew", pady=(20, 0))

    def _build_progress(self) -> None:
        """One progress row per export step, then a status line."""
        card = self._add_card()
        self._progress_rows: dict[str, StepProgressRow] = {}
        for index, step in enumerate(STEPS):
            row = StepProgressRow(card, step)
            row.grid(row=index, column=0, sticky="ew",
                     pady=(0 if index == 0 else 14, 0))
            self._progress_rows[step] = row
        self._show_idle_progress()

        # Always displayed, so the window never changes size
        status_line = ctk.CTkFrame(card, fg_color="transparent")
        status_line.grid(row=len(STEPS), column=0, sticky="ew",
                         pady=(18, 0))
        status_line.columnconfigure(0, weight=1)
        self._status = ctk.CTkLabel(status_line, font=app_font(13),
                                    anchor="w")
        self._status.grid(row=0, column=0, sticky="ew")
        self._open_button = create_secondary_button(
            status_line,
            "Ouvrir le dossier",
            self._open_output_dir,
            width=140,
            height=36,
        )
        self._open_button.grid(row=0, column=1, padx=(12, 0))
        self._set_status("Prêt à sauvegarder", TEXT_MUTED)

    @property
    def _senscritique_requested(self) -> bool:
        return bool(self._senscritique_checkbox.get())

    def _show_idle_progress(self) -> None:
        """Reset the progress rows, except while an export is running."""
        if self._is_running:
            return
        for step, row in self._progress_rows.items():
            if step == SENSCRITIQUE_STEP and not self._senscritique_requested:
                row.reset("Non demandé")
            else:
                row.reset()

    def _set_status(self, message: str, color: str) -> None:
        """Update the status line; the open button needs a finished export."""
        self._status.configure(text=message, text_color=color)
        has_output = self._output_dir is not None
        self._open_button.configure(
            state=tk.NORMAL if has_output else tk.DISABLED,
            border_color=INPUT_BORDER if has_output else SUBTLE_BORDER,
        )

    # Actions

    def _browse_output_dir(self) -> None:
        """Let the user pick the output folder."""
        folder = filedialog.askdirectory(
            parent=self,
            initialdir=self._dir_entry.value,
            title="Dossier de destination",
        )
        if folder:
            self._dir_entry.set_value(folder)
            self._dir_entry.clear_error()

    def _open_output_dir(self) -> None:
        """Open the folder of the last export."""
        if self._output_dir is not None:
            open_in_file_manager(self._output_dir)

    def _validate_inputs(self) -> bool:
        """Check both entries and show an error below each invalid one."""
        is_valid = True
        self._url_entry.clear_error()
        self._dir_entry.clear_error()
        try:
            parse_member_id(self._url_entry.value)
        except ValueError:
            self._url_entry.show_error(
                "Adresse invalide : elle doit contenir « membre-… »"
            )
            is_valid = False
        if not Path(self._dir_entry.value).is_dir():
            self._dir_entry.show_error("Ce dossier n'existe pas")
            is_valid = False
        return is_valid

    def _start_export(self) -> None:
        """Start the export in a worker thread if the inputs are valid."""
        if self._is_running or not self._validate_inputs():
            return
        self._show_idle_progress()
        self._is_running = True
        self._save_button.configure(
            state=tk.DISABLED, text="Sauvegarde en cours…",
            fg_color=ACCENT_BUSY,
        )
        self._senscritique_checkbox.configure(state=tk.DISABLED)
        self._output_dir = None
        self._set_status("Récupération de tes notes…", TEXT_MUTED)
        threading.Thread(
            target=self._run_export,
            args=(
                self._url_entry.value,
                Path(self._dir_entry.value),
                self._senscritique_requested,
            ),
            daemon=True,
        ).start()
        self.after(EVENT_POLL_DELAY, self._process_events)

    def _run_export(
        self, profile_url: str, output_dir: Path, senscritique: bool
    ) -> None:
        """Export the ratings. Runs in the worker thread."""
        def post_progress(step: str, done: int, total: int) -> None:
            self._events.put(("progress", (step, done, total)))

        try:
            results = export_profile(
                profile_url,
                output_dir,
                senscritique=senscritique,
                log=lambda _message: None,
                on_progress=post_progress,
            )
        # Any error must reach the UI thread, otherwise the window would
        # wait forever
        except Exception as error:
            self._events.put(("error", error))
        else:
            self._events.put(("done", results))

    def _process_events(self) -> None:
        """Apply the events posted by the worker thread."""
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "progress":
                    step, done, total = payload
                    self._progress_rows[step].update_progress(done, total)
                    if step == SENSCRITIQUE_STEP:
                        self._set_status(format_remaining_time(total - done),
                                         TEXT_MUTED)
                elif kind == "error":
                    self._on_export_failed(payload)
                    return
                elif kind == "done":
                    self._on_export_done(payload)
                    return
        except queue.Empty:
            pass
        self.after(EVENT_POLL_DELAY, self._process_events)

    def _enable_save_button(self) -> None:
        """Make the main button usable again."""
        self._is_running = False
        self._save_button.configure(
            state=tk.NORMAL, text=SAVE_LABEL, fg_color=ACCENT
        )
        self._senscritique_checkbox.configure(state=tk.NORMAL)

    def _on_export_done(self, results: list[ExportResult]) -> None:
        """Show the outcome of a successful export."""
        self._enable_save_button()
        for result in results:
            self._progress_rows[result.step].mark_done(result.count)
        total = sum(
            result.count for result in results if result.step in MEDIA_TYPES
        )
        self._output_dir = results[0].path.parent
        self._set_status(f"{format_count(total)} notes sauvegardées", SUCCESS)

    def _on_export_failed(self, error: Exception) -> None:
        """Show the error next to the input that caused it, if any."""
        self._enable_save_button()
        for step, row in self._progress_rows.items():
            if not row.is_done:
                is_skipped = (step == SENSCRITIQUE_STEP
                              and not self._senscritique_requested)
                row.reset("Non demandé" if is_skipped else "En attente")
        if (isinstance(error, requests.HTTPError)
                and error.response is not None
                and error.response.status_code == 404):
            self._url_entry.show_error(
                "Profil introuvable, vérifie l'adresse"
            )
            message = "Profil introuvable sur AlloCiné."
        elif isinstance(error, requests.RequestException):
            message = "AlloCiné est injoignable, vérifie ta connexion."
        elif isinstance(error, OSError):
            self._dir_entry.show_error("Impossible d'écrire dans ce dossier")
            message = "Les fichiers n'ont pas pu être enregistrés."
        else:
            message = f"Erreur inattendue ({type(error).__name__})."
        self._set_status(message, ERROR)


def main() -> None:
    """Start the graphical interface."""
    if sys.platform != "win32":
        # The default Linux drawing method (a font of shapes) renders
        # rounded corners incorrectly
        ctk.DrawEngine.preferred_drawing_method = "circle_shapes"
    ctk.set_appearance_mode("dark")
    App().mainloop()


if __name__ == "__main__":
    main()
