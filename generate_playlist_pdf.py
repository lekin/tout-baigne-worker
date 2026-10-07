#!/usr/bin/env python3
"""Generate a PDF tracklist from an Airtable 'Playlists (from events)' record."""

import argparse
import sys
import os
import tempfile
import re
from collections import Counter
from typing import Any

import requests
from fpdf import FPDF
from src.airtable_client import AirtableClient
from src.config import settings


PLAYLIST_TABLE = "Playlists (from events)"
TRACKS_TABLE = "Tracks"


def fetch_playlist(client: AirtableClient, record_id: str) -> dict:
    """Fetch a single playlist record by its Airtable record ID or ID field value."""
    table = client.get_table(PLAYLIST_TABLE)

    # If it looks like an Airtable record ID, fetch directly
    if record_id.startswith("rec"):
        try:
            return table.get(record_id)
        except Exception:
            pass

    # Otherwise search by the 'ID' field (which stores the record ID as text)
    records = table.all(formula=f"{{ID}} = '{record_id}'", max_records=1)
    if records:
        return records[0]

    # Fallback: try partial name match
    records = table.all(
        formula=f"FIND('{record_id}', {{Name}})", max_records=1
    )
    if records:
        return records[0]

    return None


def _extract_track_duration_seconds(fields: dict) -> float:
    candidates = [
        "Duration (Spotify)",
        "Duration",
        "Duration (s)",
        "Track duration",
    ]
    for field_name in candidates:
        value = fields.get(field_name)
        if isinstance(value, list) and value:
            value = value[0]
        try:
            seconds = float(value)
        except Exception:
            continue
        if seconds <= 0:
            continue
        return seconds
    return 0.0


def _extract_track_bpm(fields: dict) -> str:
    value = fields.get("Tempo (Spotify)")
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        bpm = float(value)
    except Exception:
        return ""
    if bpm <= 0:
        return ""
    return str(int(round(bpm)))


def _extract_track_label(fields: dict, fallback: str = "") -> str:
    artist = str(fields.get("Artist (string)") or "").strip()
    if not artist:
        artist_values = _normalize_cell_values(fields.get("Name (from Artist)"))
        artist = ", ".join(artist_values)

    title = str(fields.get("Title") or fields.get("Name") or "").strip()
    label = f"{artist} - {title}".strip(" -")
    return label or fallback


def fetch_tracks(client: AirtableClient, track_ids: list[str]) -> list[dict]:
    """Fetch track records by their Airtable record IDs, preserving order."""
    tracks_table = client.get_table(TRACKS_TABLE)
    tracks_map = {}

    for tid in track_ids:
        try:
            rec = tracks_table.get(tid)
            tracks_map[tid] = rec
        except Exception:
            tracks_map[tid] = None

    return [tracks_map[tid] for tid in track_ids if tracks_map.get(tid)]


def list_playlists(client: AirtableClient) -> list[dict]:
    """List all playlists with their ID and Name."""
    table = client.get_table(PLAYLIST_TABLE)
    return table.all(sort=["Name"])


def format_duration(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if not seconds or seconds <= 0:
        return ""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


THUMB_SIZE = 9  # mm in the PDF


def download_artwork(url: str, cache_dir: str) -> str | None:
    """Download an artwork image to a local temp file. Returns the file path or None."""
    try:
        # Use URL hash as filename to avoid re-downloading
        import hashlib
        fname = hashlib.md5(url.encode()).hexdigest() + ".jpg"
        path = os.path.join(cache_dir, fname)
        if os.path.exists(path):
            return path
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        with open(path, "wb") as f:
            f.write(resp.content)
        return path
    except Exception:
        return None


UNICODE_FONT_PATH = "/Library/Fonts/Arial Unicode.ttf"


class PlaylistPDF(FPDF):
    """Custom PDF class for playlist generation."""

    def __init__(self, playlist_name: str, event_date: str = "", venue: str = "", decades: list[str] | None = None):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.playlist_name = playlist_name
        self.event_date = event_date
        self.venue = venue
        self.decades = decades or []
        self.banger_symbol = "★"
        self.set_auto_page_break(auto=True, margin=20)
        # Register Unicode font
        self.add_font("ArialUni", "", UNICODE_FONT_PATH)
        self.add_font("ArialUni", "B", UNICODE_FONT_PATH)
        self.add_font("ArialUni", "I", UNICODE_FONT_PATH)
        self.add_font("ArialUni", "BI", UNICODE_FONT_PATH)

    # Decade badge colors (soft pastel rainbow)
    DECADE_COLORS = {
        "50s":   (255, 209, 209),
        "60s":   (255, 224, 195),
        "70s":   (255, 248, 200),
        "80s":   (208, 248, 208),
        "90s":   (200, 245, 245),
        "2000s": (200, 220, 255),
        "2010s": (225, 208, 255),
        "2020s": (255, 210, 240),
    }

    def header(self):
        if self.page_no() > 1:
            self.ln(10)
            return

        self.set_font("ArialUni", "B", 18)
        self.set_text_color(30, 30, 30)
        self.cell(0, 12, self.playlist_name, new_x="LMARGIN", new_y="NEXT", align="C")

        if self.venue:
            self.set_font("ArialUni", "I", 11)
            self.set_text_color(100, 100, 100)
            self.cell(0, 8, self.venue, new_x="LMARGIN", new_y="NEXT", align="C")

        if self.event_date:
            self.set_font("ArialUni", "I", 11)
            self.set_text_color(100, 100, 100)
            self.cell(0, 8, self.event_date, new_x="LMARGIN", new_y="NEXT", align="C")

        if self.decades:
            self.ln(2)
            self._draw_decade_badges(self.decades)

        self.ln(4)
        # Separator line
        self.set_draw_color(200, 200, 200)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def _draw_decade_badges(self, decades: list[str]):
        self.set_font("ArialUni", "", 5.5)
        gap = 1
        bh = 3.5
        badges = []
        for d in decades:
            label = d.strip()
            tw = self.get_string_width(label) + 4
            badges.append((label, tw))
        total_badges_w = sum(tw for _, tw in badges) + gap * (len(badges) - 1)
        bx = (self.w - total_badges_w) / 2
        by = self.get_y()
        for label, tw in badges:
            r, g, b = self.DECADE_COLORS.get(label, (235, 235, 235))
            self.set_fill_color(r, g, b)
            self.set_draw_color(r, g, b)
            self.set_line_width(0.01)
            self.rect(bx, by, tw, bh, "DF", round_corners=True, corner_radius=bh / 2)
            self.set_text_color(80, 80, 80)
            self.set_xy(bx, by)
            self.cell(tw, bh, label, align="C")
            bx += tw + gap
        self.set_y(by + bh)

    def footer(self):
        self.set_y(-15)
        self.set_font("ArialUni", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def _ensure_block_space(self, block_h: float):
        if self.get_y() + block_h > self.page_break_trigger:
            self.add_page()

    def _draw_horizontal_bar_chart(self, title: str, rows: list[tuple[str, int]], color: tuple[int, int, int]):
        if not rows:
            return

        chart_w = self.w - self.l_margin - self.r_margin
        label_w = 58
        value_w = 12
        bar_w = max(20, chart_w - label_w - value_w - 4)
        row_h = 5.5
        title_h = 6
        self._ensure_block_space(title_h + row_h + 2)

        self.set_font("ArialUni", "B", 9)
        self.set_text_color(55, 55, 55)
        self.cell(0, title_h, title, new_x="LMARGIN", new_y="NEXT", align="L")

        max_count = max(count for _, count in rows) or 1
        bar_x = self.l_margin + label_w + 2

        for label, count in rows:
            if self.get_y() + row_h > self.page_break_trigger:
                self.add_page()
                self.set_font("ArialUni", "B", 9)
                self.set_text_color(55, 55, 55)
                self.cell(0, title_h, f"{title} (suite)", new_x="LMARGIN", new_y="NEXT", align="L")
            y = self.get_y()

            display_label = label
            if len(display_label) > 32:
                display_label = display_label[:29] + "..."

            self.set_font("ArialUni", "", 8)
            self.set_text_color(35, 35, 35)
            self.set_xy(self.l_margin, y)
            self.cell(label_w, row_h, display_label, new_x="RIGHT", new_y="TOP", align="L")

            self.set_fill_color(236, 236, 236)
            self.rect(bar_x, y + 1.2, bar_w, row_h - 2.2, "F")

            fill_w = (bar_w * count) / max_count
            self.set_fill_color(*color)
            self.rect(bar_x, y + 1.2, fill_w, row_h - 2.2, "F")

            self.set_font("ArialUni", "", 8)
            self.set_text_color(65, 65, 65)
            self.set_xy(bar_x + bar_w + 2, y)
            self.cell(value_w, row_h, str(count), new_x="LMARGIN", new_y="NEXT", align="R")

    def _draw_year_bars(self, title: str, rows: list[tuple[str, int]]):
        if not rows:
            return

        chart_w = self.w - self.l_margin - self.r_margin
        title_h = 6
        chart_h = 30
        label_h = 6
        block_h = title_h + chart_h + label_h + 3
        self._ensure_block_space(block_h)

        self.set_font("ArialUni", "B", 9)
        self.set_text_color(55, 55, 55)
        self.cell(0, title_h, title, new_x="LMARGIN", new_y="NEXT", align="L")

        x0 = self.l_margin
        y0 = self.get_y()
        n = len(rows)
        gap = 1.5
        bar_w = (chart_w - gap * (n - 1)) / n if n > 0 else chart_w
        max_count = max(count for _, count in rows) or 1

        self.set_draw_color(220, 220, 220)
        self.set_line_width(0.2)
        self.line(x0, y0 + chart_h, x0 + chart_w, y0 + chart_h)

        for idx, (label, count) in enumerate(rows):
            bx = x0 + idx * (bar_w + gap)
            bh = (chart_h - 2) * count / max_count if max_count else 0
            by = y0 + chart_h - bh

            self.set_fill_color(191, 215, 255)
            self.rect(bx, by, bar_w, bh, "F")

            if count > 0 and n <= 12:
                self.set_font("ArialUni", "", 6.5)
                self.set_text_color(90, 90, 90)
                self.set_xy(bx, by - 3.6)
                self.cell(bar_w, 3.2, str(count), align="C")

            short_label = label[-2:] if len(label) == 4 and label.isdigit() else label
            self.set_font("ArialUni", "", 7)
            self.set_text_color(80, 80, 80)
            self.set_xy(bx, y0 + chart_h + 1)
            self.cell(bar_w, 4, short_label, align="C")

        self.set_y(y0 + chart_h + label_h)

    def draw_stats(self, style_rows: list[tuple[str, int]], year_rows: list[tuple[str, int]], years_title: str):
        if not style_rows and not year_rows:
            return

        self._ensure_block_space(12)
        self.set_font("ArialUni", "B", 11)
        self.set_text_color(35, 35, 35)
        self.cell(0, 7, "Statistiques de la tracklist", new_x="LMARGIN", new_y="NEXT", align="L")
        self.ln(1)

        if style_rows:
            self._draw_horizontal_bar_chart(
                title="Répartition des styles",
                rows=style_rows,
                color=(201, 228, 198),
            )
            self.ln(1)

        if year_rows:
            self._draw_year_bars(title=years_title, rows=year_rows)
            self.ln(2)

    def draw_unplayed_titles(
        self,
        unplayed_count: int,
        related_count: int,
        played_count: int,
        unplayed_total_duration: float = 0.0,
        note: str = "",
        has_rows: bool = False,
    ):
        self._ensure_block_space(12)
        self.set_font("ArialUni", "B", 11)
        self.set_text_color(35, 35, 35)
        self.cell(0, 7, "Titres non joués (même Event type)", new_x="LMARGIN", new_y="NEXT", align="L")

        self.set_font("ArialUni", "", 9)
        self.set_text_color(90, 90, 90)
        summary_parts = []
        if unplayed_count > 0:
            summary_parts.append(f"{unplayed_count} non joués")
        summary_parts.append(f"{played_count} joués")
        if related_count > 0:
            summary_parts.append(f"{related_count} titres liés")
        else:
            summary_parts.append("0 titre lié trouvé")
        if unplayed_total_duration and unplayed_total_duration > 0:
            summary_parts.append(f"Durée totale non jouée : {format_duration(unplayed_total_duration)}")
        self.cell(0, 5, " · ".join(summary_parts), new_x="LMARGIN", new_y="NEXT", align="L")
        self.ln(1)

        if note:
            self.set_font("ArialUni", "I", 8.5)
            self.set_text_color(120, 120, 120)
            self.multi_cell(0, 4.5, note, new_x="LMARGIN", new_y="NEXT")
            self.ln(1)

        if not has_rows:
            self.set_font("ArialUni", "I", 9)
            self.set_text_color(110, 110, 110)
            if related_count > 0:
                text = "Tous les titres liés à cet Event type ont été joués."
            else:
                text = "Aucun titre non joué à afficher pour cet Event type."
            self.cell(0, 5, text, new_x="LMARGIN", new_y="NEXT", align="L")
            self.ln(2)
        else:
            self.ln(1)

    def draw_played_outside_event_type(
        self,
        outside_count: int,
        related_count: int,
        played_count: int,
        outside_total_duration: float = 0.0,
        has_rows: bool = False,
    ):
        self._ensure_block_space(12)
        self.set_font("ArialUni", "B", 11)
        self.set_text_color(35, 35, 35)
        self.cell(0, 7, "Titres joués hors Event type", new_x="LMARGIN", new_y="NEXT", align="L")

        self.set_font("ArialUni", "", 9)
        self.set_text_color(90, 90, 90)
        summary_parts = [
            f"{outside_count} hors Event type",
            f"{played_count} joués",
            f"{related_count} titres liés à l'Event type",
        ]
        if outside_total_duration and outside_total_duration > 0:
            summary_parts.append(f"Durée totale hors Event type : {format_duration(outside_total_duration)}")
        summary = " · ".join(summary_parts)
        self.cell(0, 5, summary, new_x="LMARGIN", new_y="NEXT", align="L")
        self.ln(1)

        if not has_rows:
            self.set_font("ArialUni", "I", 9)
            self.set_text_color(110, 110, 110)
            self.cell(
                0,
                5,
                "Tous les titres joués appartiennent à l'Event type.",
                new_x="LMARGIN",
                new_y="NEXT",
                align="L",
            )
            self.ln(2)
        else:
            self.ln(1)


def _is_airtable_record_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("rec") and len(value) >= 10


def _normalize_cell_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        name = value.get("name") or value.get("Name")
        if name:
            text = str(name).strip()
            return [text] if text else []
        rid = value.get("id")
        if rid:
            text = str(rid).strip()
            return [text] if text else []
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_normalize_cell_values(item))
        return out
    text = str(value).strip()
    return [text] if text else []


def _resolve_event_type_values_to_match(client: AirtableClient | None, playlist_record: dict) -> list[str]:
    fields = playlist_record.get("fields", {}) or {}
    event_type_field_candidates = [
        "Event type",
        "Event type (from Event)",
        "Event Type",
        "Event type (string)",
        "Event Type (string)",
    ]
    event_type_values_raw: list[str] = []
    for field_name in event_type_field_candidates:
        event_type_values_raw.extend(_normalize_cell_values(fields.get(field_name)))
    event_type_values_raw = [v for v in dict.fromkeys(event_type_values_raw) if v]

    if not event_type_values_raw:
        return []

    event_type_ids = [v for v in event_type_values_raw if _is_airtable_record_id(v)]
    event_type_names = [v for v in event_type_values_raw if not _is_airtable_record_id(v)]

    if client:
        for event_type_id in event_type_ids:
            resolved_name = _lookup_record_name(
                client,
                event_type_id,
                table_candidates=["Event types", "Event Types", "Event type", "Event Type"],
                name_field_candidates=["Name", "Title", "Type"],
            )
            if resolved_name:
                event_type_names.append(resolved_name)

    return [v for v in dict.fromkeys(event_type_ids + event_type_names) if v]


def _airtable_formula_escape(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def _split_stat_tokens(values: list[str]) -> list[str]:
    out: list[str] = []
    for raw in values:
        chunks = re.split(r"[,;/|]", raw)
        for chunk in chunks:
            token = chunk.strip()
            if token:
                out.append(token)
    return out


def _extract_track_styles(fields: dict) -> list[str]:
    style_fields = ["Style", "Styles", "Style (manual)", "Styles (manual)"]
    genre_fields = ["Genre", "Genres"]

    for field_name in style_fields + genre_fields:
        values = _normalize_cell_values(fields.get(field_name))
        tokens = _split_stat_tokens(values)
        if tokens:
            return tokens
    return []


def _extract_track_tags(fields: dict) -> list[str]:
    raw_values = _normalize_cell_values(fields.get("Tags"))

    tags: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        chunks = re.split(r"[,;|\n]", raw)
        for chunk in chunks:
            tag = chunk.strip()
            if not tag:
                continue
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            tags.append(tag)
    return tags


def _extract_track_year(fields: dict) -> int | None:
    candidates = [
        "Year (manual)",
        "Year",
        "Release year",
        "Release Year",
        "Year (from Tracks)",
        "Release date",
        "Release Date",
    ]
    for field_name in candidates:
        values = _normalize_cell_values(fields.get(field_name))
        for value in values:
            match = re.search(r"(19\d{2}|20\d{2})", value)
            if not match:
                continue
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
    return None


def compute_style_distribution(tracks: list[dict], top_n: int | None = None) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for track in tracks:
        fields = track.get("fields", {}) or {}
        styles = _extract_track_styles(fields)
        if styles:
            counter.update(styles)
    if top_n is None:
        return counter.most_common()
    return counter.most_common(max(0, top_n))


def _parse_decade_start(decade_label: str) -> int | None:
    match = re.search(r"((?:19|20)\d0)s", str(decade_label or "").strip())
    if not match:
        return None
    return int(match.group(1))


def compute_year_distribution(tracks: list[dict], decades: list[str]) -> tuple[list[tuple[str, int]], str]:
    years: list[int] = []
    for track in tracks:
        fields = track.get("fields", {}) or {}
        year = _extract_track_year(fields)
        if year is not None:
            years.append(year)

    if not years:
        return [], ""

    decade_starts = []
    for decade in decades:
        start = _parse_decade_start(str(decade))
        if start is not None:
            decade_starts.append(start)

    if len(decade_starts) == 1:
        start = decade_starts[0]
        end = start + 9
        counter = Counter(year for year in years if start <= year <= end)
        rows = [(str(year), counter.get(year, 0)) for year in range(start, end + 1)]
        return rows, f"Répartition des années ({start}s)"

    counter = Counter(years)
    years_sorted = sorted(counter.keys())
    if len(years_sorted) <= 15:
        rows = [(str(year), counter[year]) for year in years_sorted]
        return rows, "Répartition des années"

    decade_counter: Counter[str] = Counter(f"{(year // 10) * 10}s" for year in years)
    rows = sorted(decade_counter.items(), key=lambda item: item[0])
    return rows, "Répartition des décennies"


def compute_unplayed_titles_for_event_type(
    client: AirtableClient | None,
    playlist_record: dict,
    played_track_ids: set[str],
) -> tuple[list[dict], set[str], set[str], str]:
    if not client:
        return [], set(), set(), "Client Airtable indisponible."

    values_to_match = _resolve_event_type_values_to_match(client, playlist_record)
    if not values_to_match:
        return [], set(), set(), "Impossible de résoudre une valeur exploitable pour Event type."

    try:
        playlists_table = client.get_table(PLAYLIST_TABLE)
        tracks_table = client.get_table(TRACKS_TABLE)
    except Exception:
        return [], set(), set(), "Impossible d'accéder aux tables Airtable nécessaires pour calculer les non joués."

    related_playlists_by_id: dict[str, dict] = {}
    playlist_query_succeeded = False
    playlist_formula_templates = [
        "FIND('{value}', ARRAYJOIN({{Event type}}))",
        "FIND('{value}', ARRAYJOIN({{Event type (from Event)}}))",
        "FIND('{value}', {{Event type}})",
        "FIND('{value}', {{Event type (from Event)}})",
    ]

    for template in playlist_formula_templates:
        conds = [
            template.format(value=_airtable_formula_escape(raw_value))
            for raw_value in values_to_match
        ]
        formula = conds[0] if len(conds) == 1 else f"OR({', '.join(conds)})"

        try:
            rows = playlists_table.all(formula=formula)
            playlist_query_succeeded = True
        except Exception:
            continue

        for row in rows:
            row_id = str(row.get("id") or "").strip()
            if row_id:
                related_playlists_by_id[row_id] = row

    playlist_related_track_ids: set[str] = set()
    for playlist_row in related_playlists_by_id.values():
        playlist_fields = playlist_row.get("fields", {}) or {}
        for raw_track in _normalize_cell_values(playlist_fields.get("Tracks")):
            if _is_airtable_record_id(raw_track):
                playlist_related_track_ids.add(raw_track)

    direct_related_tracks_by_id: dict[str, dict] = {}
    track_formula_templates = [
        "FIND('{value}', ARRAYJOIN({{Event type}}))",
        "FIND('{value}', ARRAYJOIN({{Name (from Event type)}}))",
        "FIND('{value}', {{Event type}})",
        "FIND('{value}', {{Name (from Event type)}})",
    ]
    track_query_succeeded = False

    for template in track_formula_templates:
        conds = [
            template.format(value=_airtable_formula_escape(raw_value))
            for raw_value in values_to_match
        ]
        formula = conds[0] if len(conds) == 1 else f"OR({', '.join(conds)})"

        try:
            rows = tracks_table.all(formula=formula)
            track_query_succeeded = True
        except Exception:
            continue

        for row in rows:
            row_id = str(row.get("id") or "").strip()
            if row_id:
                direct_related_tracks_by_id[row_id] = row

    direct_related_track_ids = set(direct_related_tracks_by_id.keys())
    related_track_ids = playlist_related_track_ids | direct_related_track_ids

    note_parts: list[str] = []
    if playlist_related_track_ids:
        note_parts.append("tracks issus des playlists partageant ce(s) Event type")
    elif playlist_query_succeeded:
        note_parts.append("playlists liées trouvées sans tracks exploitables")

    if direct_related_track_ids:
        note_parts.append("filtrage direct de la table Tracks par Event type")

    if note_parts:
        note = "Comparaison basée sur " + " + ".join(note_parts) + "."
    else:
        note = ""

    if not related_track_ids:
        if playlist_query_succeeded or track_query_succeeded:
            return [], set(), set(), "Aucun titre lié trouvé pour ce(s) Event type dans la table Tracks."
        return [], set(), set(), "Impossible de filtrer les titres liés par Event type (champs/formules incompatibles)."

    unplayed_items: list[tuple[str, dict]] = []
    for track_id in sorted(related_track_ids):
        if track_id in played_track_ids:
            continue

        try:
            track = tracks_table.get(track_id)
            track_fields = track.get("fields", {}) or {}
            label = _extract_track_label(track_fields, fallback=track_id)
        except Exception:
            track = {"id": track_id, "fields": {"Title": track_id}}
            label = track_id

        unplayed_items.append((label.casefold(), track))

    unplayed_items.sort(key=lambda item: item[0])
    unplayed_tracks = [track for _, track in unplayed_items]
    return unplayed_tracks, related_track_ids, direct_related_track_ids, note


def _lookup_record_name(
    client: AirtableClient,
    record_id: str,
    table_candidates: list[str],
    name_field_candidates: list[str],
) -> str:
    for table_name in table_candidates:
        try:
            table = client.get_table(table_name)
            rec = table.get(record_id)
            record_fields = rec.get("fields", {}) if isinstance(rec, dict) else {}
            for field_name in name_field_candidates:
                value = record_fields.get(field_name)
                values = _normalize_cell_values(value)
                if values:
                    if not _is_airtable_record_id(values[0]):
                        return values[0]
            # Fallback: return first non-ID text field if available
            for value in record_fields.values():
                values = _normalize_cell_values(value)
                if values and not _is_airtable_record_id(values[0]):
                    return values[0]
        except Exception:
            continue
    return ""


def _resolve_first_relation_name(
    value: Any,
    client: AirtableClient | None,
    table_candidates: list[str],
    name_field_candidates: list[str],
) -> str:
    for raw in _normalize_cell_values(value):
        if _is_airtable_record_id(raw) and client:
            resolved = _lookup_record_name(client, raw, table_candidates, name_field_candidates)
            if resolved:
                return resolved
            continue
        return raw
    return ""


def resolve_playlist_title(fields: dict, client: AirtableClient | None = None) -> str:
    """Resolve playlist title from Event type relation names (not record IDs)."""
    title = _resolve_first_relation_name(
        fields.get("Event type", ""),
        client,
        table_candidates=["Event types", "Event Types", "Event type", "Event Types"],
        name_field_candidates=["Name", "Title", "Type"],
    )
    if title:
        return title

    fallback_name = str(fields.get("Name", "")).strip()
    if fallback_name and not _is_airtable_record_id(fallback_name):
        return fallback_name
    return "Playlist"


def resolve_venue_name(fields: dict, client: AirtableClient | None = None) -> str:
    """Resolve venue from relation names (not record IDs)."""
    venue = _resolve_first_relation_name(
        fields.get("Venue (from Event)", ""),
        client,
        table_candidates=["Venues", "Venue", "Events", "Event"],
        name_field_candidates=["Name", "Venue", "Title"],
    )
    return venue


def generate_pdf(
    playlist_record: dict,
    tracks: list[dict],
    output_path: str,
    client: AirtableClient = None,
    show_analytics: bool = False,
    artwork_cache_dir: str | None = None,
):
    """Generate the PDF file from playlist data."""
    fields = playlist_record.get("fields", {})

    title = resolve_playlist_title(fields, client=client)
    venue = resolve_venue_name(fields, client=client)

    event_date = ""
    date_list = fields.get("Date (DD/MM/YYYY) (from Event)", [])
    if date_list:
        event_date = date_list[0] if isinstance(date_list, list) else str(date_list)
    decades = fields.get("Decades", []) or []
    if isinstance(decades, str):
        decades = [decades]

    pdf = PlaylistPDF(playlist_name=title, event_date=event_date, venue=venue, decades=decades)
    banger_symbol = pdf.banger_symbol
    pdf.alias_nb_pages()
    pdf.add_page()

    # Summary info
    total_duration = fields.get("Duration (Spotify) Rollup (from Tracks)", 0)
    pdf.set_font("ArialUni", "", 10)
    pdf.set_text_color(80, 80, 80)
    summary_parts = [f"{len(tracks)} tracks"]
    if total_duration:
        summary_parts.append(f"Durée totale : {format_duration(total_duration)}")
    pdf.cell(0, 6, " · ".join(summary_parts), new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(2)

    # Download artworks to a temp directory (caller-provided cache shared
    # across playlists — event-type exports repeat the same tracks a lot).
    artwork_cache = artwork_cache_dir or tempfile.mkdtemp(prefix="playlist_art_")

    def get_track_artwork_path(track: dict) -> str | None:
        tf = track.get("fields", {}) or {}
        artwork_list = tf.get("Artwork (Spotify)", [])
        thumb_url = None
        if isinstance(artwork_list, list) and artwork_list:
            item = artwork_list[-1] if len(artwork_list) > 1 else artwork_list[0]
            if isinstance(item, dict):
                thumbs = item.get("thumbnails", {})
                small = thumbs.get("small", {}) or thumbs.get("large", {})
                thumb_url = small.get("url") if small else item.get("url")
                if not thumb_url:
                    thumb_url = item.get("url")
        return download_artwork(thumb_url, artwork_cache) if thumb_url else None

    artwork_paths: list[str | None] = []
    print("🖼️  Téléchargement des artworks...")
    for track in tracks:
        artwork_paths.append(get_track_artwork_path(track))

    # Build table data
    header = ["#", "Artiste", "Titre", "Année", "BPM", "Genre", "Tags", "Banger"]
    col_widths = [8, 31, 34, 12, 11, 25, 44, 15]
    tags_col_idx = header.index("Tags")
    bpm_col_idx = header.index("BPM")
    banger_col_idx = header.index("Banger")
    line_h = 3.5  # line height for multi_cell text
    min_row_h = THUMB_SIZE  # minimum row height = artwork size
    img_col_w = THUMB_SIZE  # square artwork column
    total_w = sum(col_widths) + img_col_w

    tag_palette = [
        (233, 245, 255),
        (231, 250, 236),
        (255, 244, 229),
        (244, 236, 255),
        (255, 236, 243),
        (236, 245, 245),
    ]
    genre_palette = [
        (238, 245, 255),
        (236, 248, 240),
        (255, 247, 234),
        (245, 241, 255),
        (255, 240, 246),
        (240, 247, 247),
    ]
    genre_col_idx = header.index("Genre")

    def _shorten_tag(tag: str, max_len: int = 20) -> str:
        text = str(tag or "").strip()
        if len(text) <= max_len:
            return text
        return text[: max_len - 1].rstrip() + "…"

    def _layout_tag_pills(tags: list[str], max_w: float) -> tuple[list[tuple[float, float, float, str]], float]:
        if not tags or max_w <= 1:
            return [], 0.0

        pdf.set_font("ArialUni", "", 6.3)
        pad_x = 1.4
        gap_x = 0.9
        gap_y = 0.8
        pill_h = 4.1

        x = 0.0
        y = 0.0
        layout: list[tuple[float, float, float, str]] = []
        for raw_tag in tags:
            label = _shorten_tag(raw_tag)
            if not label:
                continue
            pill_w = min(max_w, pdf.get_string_width(label) + 2 * pad_x)
            if x > 0 and x + pill_w > max_w:
                x = 0.0
                y += pill_h + gap_y
            layout.append((x, y, pill_w, label))
            x += pill_w + gap_x

        if not layout:
            return [], 0.0
        content_h = layout[-1][1] + pill_h
        return layout, content_h

    def _tag_color(tag: str) -> tuple[int, int, int]:
        idx = sum(ord(c) for c in tag.casefold()) % len(tag_palette)
        return tag_palette[idx]

    def _genre_color(genre: str) -> tuple[int, int, int]:
        idx = sum(ord(c) for c in genre.casefold()) % len(genre_palette)
        return genre_palette[idx]

    def build_track_row(index: int, track: dict) -> list[str]:
        tf = track.get("fields", {})
        artist = tf.get("Artist (string)", "")
        if not artist:
            artist_names = tf.get("Name (from Artist)", [])
            artist = ", ".join(artist_names) if isinstance(artist_names, list) else str(artist_names)
        title = tf.get("Title", "")
        year = tf.get("Year (manual)", "")
        if year:
            year = str(int(year)) if isinstance(year, (int, float)) else str(year)
        genre_values = _split_stat_tokens(_normalize_cell_values(tf.get("Genre")))
        genres: list[str] = []
        seen_genres: set[str] = set()
        for genre in genre_values:
            key = genre.casefold()
            if key in seen_genres:
                continue
            seen_genres.add(key)
            genres.append(genre)
        tags = _extract_track_tags(tf)
        bpm = _extract_track_bpm(tf)
        banger = banger_symbol if bool(tf.get("Banger")) else ""
        return [str(index), artist, title, year, bpm, genres, tags, banger]

    data_rows = [build_track_row(i, track) for i, track in enumerate(tracks, 1)]

    def measure_row_height(data_row):
        """Measure the height needed for a row by computing multi_cell heights."""
        max_h = min_row_h
        pdf.set_font("ArialUni", "", 7)
        for j, val in enumerate(data_row):
            w = col_widths[j] - 1
            if j == genre_col_idx:
                genres = val if isinstance(val, list) else _split_stat_tokens(_normalize_cell_values(val))
                _, genres_h = _layout_tag_pills(genres, w)
                cell_h = genres_h
            elif j == tags_col_idx:
                tags = val if isinstance(val, list) else _split_stat_tokens(_normalize_cell_values(val))
                _, tags_h = _layout_tag_pills(tags, w)
                cell_h = tags_h
            elif j == bpm_col_idx:
                text_val = val if isinstance(val, str) else str(val or "")
                cell_h = line_h if text_val else 0
            elif j == banger_col_idx:
                text_val = val if isinstance(val, str) else str(val or "")
                cell_h = line_h if text_val else 0
            else:
                text_val = val if isinstance(val, str) else str(val or "")
                if not text_val:
                    continue
                lines = pdf.multi_cell(w, line_h, text_val, dry_run=True, output="LINES")
                cell_h = len(lines) * line_h
            if cell_h > max_h:
                max_h = cell_h
        return max_h

    def draw_header_row():
        pdf.set_font("ArialUni", "B", 8)
        pdf.set_fill_color(50, 50, 50)
        pdf.set_text_color(255, 255, 255)
        y = pdf.get_y()
        x0 = pdf.l_margin
        for j, h in enumerate(header):
            pdf.set_xy(x0, y)
            pdf.cell(col_widths[j], min_row_h, h, new_x="END", new_y="TOP", fill=True)
            x0 += col_widths[j]
        pdf.set_xy(x0, y)
        pdf.cell(img_col_w, min_row_h, "", new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_draw_color(200, 200, 200)
        pdf.set_line_width(0.2)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + total_w, pdf.get_y())

    def draw_data_row(idx, data_row, img_path):
        row_h = measure_row_height(data_row)
        y_start = pdf.get_y()

        # Check page break
        if y_start + row_h > pdf.page_break_trigger:
            pdf.add_page()
            draw_header_row()
            y_start = pdf.get_y()

        # Alternating row background
        if idx % 2 == 1:
            pdf.set_fill_color(245, 245, 245)
            pdf.rect(pdf.l_margin, y_start, total_w, row_h, "F")

        pdf.set_font("ArialUni", "", 7)
        pdf.set_text_color(30, 30, 30)

        # Draw text cells with wrapping, vertically centered
        x0 = pdf.l_margin
        for j, val in enumerate(data_row):
            if j == genre_col_idx:
                genres = val if isinstance(val, list) else _split_stat_tokens(_normalize_cell_values(val))
                layout, genres_h = _layout_tag_pills(genres, col_widths[j] - 1)
                if layout and genres_h > 0:
                    y_offset = (row_h - genres_h) / 2
                    pdf.set_font("ArialUni", "", 6.3)
                    pdf.set_text_color(55, 55, 55)
                    for rel_x, rel_y, pill_w, label in layout:
                        px = x0 + rel_x
                        py = y_start + y_offset + rel_y
                        pdf.set_fill_color(*_genre_color(label))
                        pdf.rect(px, py, pill_w, 4.1, style="F", round_corners=True)
                        pdf.set_xy(px, py)
                        pdf.cell(pill_w, 4.1, label, align="C", new_x="LEFT", new_y="TOP")
            elif j == tags_col_idx:
                tags = val if isinstance(val, list) else _split_stat_tokens(_normalize_cell_values(val))
                layout, tags_h = _layout_tag_pills(tags, col_widths[j] - 1)
                if layout and tags_h > 0:
                    y_offset = (row_h - tags_h) / 2
                    pdf.set_font("ArialUni", "", 6.3)
                    pdf.set_text_color(55, 55, 55)
                    for rel_x, rel_y, pill_w, label in layout:
                        px = x0 + rel_x
                        py = y_start + y_offset + rel_y
                        pdf.set_fill_color(*_tag_color(label))
                        pdf.rect(px, py, pill_w, 4.1, style="F", round_corners=True)
                        pdf.set_xy(px, py)
                        pdf.cell(pill_w, 4.1, label, align="C", new_x="LEFT", new_y="TOP")
            elif j == bpm_col_idx:
                text_val = val if isinstance(val, str) else str(val or "")
                pdf.set_font("ArialUni", "", 7)
                pdf.set_text_color(30, 30, 30)
                y_offset = (row_h - line_h) / 2
                pdf.set_xy(x0, y_start + y_offset)
                pdf.cell(col_widths[j], line_h, text_val, align="C", new_x="RIGHT", new_y="TOP")
            elif j == banger_col_idx:
                text_val = val if isinstance(val, str) else str(val or "")
                pdf.set_font("ArialUni", "", 7)
                pdf.set_text_color(30, 30, 30)
                y_offset = (row_h - line_h) / 2
                pdf.set_xy(x0, y_start + y_offset)
                pdf.cell(col_widths[j], line_h, text_val, align="C", new_x="RIGHT", new_y="TOP")
            else:
                text_val = val if isinstance(val, str) else str(val or "")
                lines = pdf.multi_cell(col_widths[j] - 1, line_h, text_val, dry_run=True, output="LINES")
                text_h = len(lines) * line_h
                y_offset = (row_h - text_h) / 2
                pdf.set_font("ArialUni", "", 7)
                pdf.set_text_color(30, 30, 30)
                pdf.set_xy(x0, y_start + y_offset)
                pdf.multi_cell(col_widths[j], line_h, text_val, align="L", new_x="RIGHT", new_y="TOP")
            x0 += col_widths[j]

        # Draw artwork image vertically centered in last column
        if img_path and os.path.exists(img_path):
            img_size = min(min_row_h - 1, row_h - 1)
            img_y = y_start + (row_h - img_size) / 2
            img_x = x0 + (img_col_w - img_size) / 2
            pdf.image(img_path, x=img_x, y=img_y, w=img_size, h=img_size)

        # Separator line
        pdf.set_draw_color(220, 220, 220)
        pdf.set_line_width(0.1)
        pdf.line(pdf.l_margin, y_start + row_h, pdf.l_margin + total_w, y_start + row_h)

        pdf.set_y(y_start + row_h)

    # Render
    draw_header_row()
    for idx, data_row in enumerate(data_rows):
        draw_data_row(idx, data_row, artwork_paths[idx])

    if show_analytics:
        pdf.add_page()
        style_stats = compute_style_distribution(tracks, top_n=None)
        year_stats, years_title = compute_year_distribution(tracks, decades)
        pdf.draw_stats(style_stats, year_stats, years_title)

    if show_analytics:
        played_track_ids = {
            str(track.get("id") or "").strip()
            for track in tracks
            if str(track.get("id") or "").strip()
        }
        unplayed_tracks, related_track_ids, direct_related_track_ids, unplayed_note = compute_unplayed_titles_for_event_type(
            client=client,
            playlist_record=playlist_record,
            played_track_ids=played_track_ids,
        )
        related_count = len(related_track_ids)
        direct_related_count = len(direct_related_track_ids)
        played_outside_event_type_tracks = [
            track
            for track in tracks
            if str(track.get("id") or "").strip() and str(track.get("id") or "").strip() not in direct_related_track_ids
        ]

        outside_total_duration = 0.0
        for track in played_outside_event_type_tracks:
            tf = track.get("fields", {}) or {}
            outside_total_duration += _extract_track_duration_seconds(tf)

        unplayed_total_duration = 0.0
        for track in unplayed_tracks:
            tf = track.get("fields", {}) or {}
            unplayed_total_duration += _extract_track_duration_seconds(tf)

        pdf.add_page()
        pdf.draw_unplayed_titles(
            unplayed_count=len(unplayed_tracks),
            related_count=related_count,
            played_count=len(played_track_ids),
            unplayed_total_duration=unplayed_total_duration,
            note=unplayed_note,
            has_rows=bool(unplayed_tracks),
        )

        if unplayed_tracks:
            unplayed_data_rows = [build_track_row(i, track) for i, track in enumerate(unplayed_tracks, 1)]
            unplayed_artwork_paths = [get_track_artwork_path(track) for track in unplayed_tracks]
            draw_header_row()
            for idx, data_row in enumerate(unplayed_data_rows):
                draw_data_row(idx, data_row, unplayed_artwork_paths[idx])

        pdf.add_page()
        pdf.draw_played_outside_event_type(
            outside_count=len(played_outside_event_type_tracks),
            related_count=direct_related_count,
            played_count=len(played_track_ids),
            outside_total_duration=outside_total_duration,
            has_rows=bool(played_outside_event_type_tracks),
        )

        if played_outside_event_type_tracks:
            outside_data_rows = [build_track_row(i, track) for i, track in enumerate(played_outside_event_type_tracks, 1)]
            outside_artwork_paths = [get_track_artwork_path(track) for track in played_outside_event_type_tracks]
            draw_header_row()
            for idx, data_row in enumerate(outside_data_rows):
                draw_data_row(idx, data_row, outside_artwork_paths[idx])

    pdf.output(output_path)
    return output_path


EVENT_TYPE_PLAYLISTS_TABLE = "Playlists"


def list_event_type_playlists(client: AirtableClient, event_type: str) -> list[dict]:
    """Records from the 'Playlists' table matching an Event Type Criteria, sorted by name."""
    table = client.get_table(EVENT_TYPE_PLAYLISTS_TABLE)
    escaped = event_type.replace('"', '\\"')
    records = table.all(formula=f'FIND("{escaped}", {{Event Type Criteria (string)}})')
    playlists = []
    for rec in records:
        fields = rec.get("fields", {}) or {}
        if fields.get("Hidden"):
            continue
        if str(fields.get("Element type") or "").strip().lower() == "folder":
            continue
        playlists.append(rec)
    playlists.sort(key=lambda r: str((r.get("fields") or {}).get("Name") or "").lower())
    return playlists


def fetch_tracks_batched(client: AirtableClient, track_ids: list[str], chunk_size: int = 50) -> list[dict]:
    """Fetch track records in OR(RECORD_ID()=…) chunks — much faster than one GET per id."""
    tracks_table = client.get_table(TRACKS_TABLE)
    by_id: dict[str, dict] = {}
    for start in range(0, len(track_ids), chunk_size):
        chunk = track_ids[start : start + chunk_size]
        formula = "OR(" + ",".join(f"RECORD_ID()='{tid}'" for tid in chunk) + ")"
        for rec in tracks_table.all(formula=formula):
            by_id[rec["id"]] = rec
    return [by_id[tid] for tid in track_ids if tid in by_id]


def _normalize_decade_label(label: str) -> str:
    """Normalize '80's' / '2000's' to the badge color keys '80s' / '2000s'."""
    m = re.match(r"^\s*(\d{2,4})'?s\s*$", label, flags=re.IGNORECASE)
    if m:
        return f"{m.group(1)}s"
    return label.strip()


def adapt_event_type_playlist_record(record: dict) -> dict:
    """Make a 'Playlists' record look like a 'Playlists (from events)' one for generate_pdf."""
    fields = dict(record.get("fields", {}) or {})

    decade_labels = _split_stat_tokens(_normalize_cell_values(fields.get("Decade Criteria (string)")))
    fields["Decades"] = [_normalize_decade_label(d) for d in decade_labels]

    # 'Playlists' uses a differently-named rollup for the header summary.
    if "Duration (Spotify) Rollup (from Tracks)" not in fields and fields.get("Total playlist duration"):
        fields["Duration (Spotify) Rollup (from Tracks)"] = fields["Total playlist duration"]

    return {**record, "fields": fields}


def merge_pdfs(paths: list[str], output_path: str) -> str:
    """Concatenate per-playlist PDFs into a single document."""
    try:
        from pypdf import PdfWriter
    except ImportError:
        raise SystemExit(
            "❌ pypdf requis pour l'export multi-playlists : pip install pypdf"
        )
    writer = PdfWriter()
    for path in paths:
        writer.append(path)
    with open(output_path, "wb") as f:
        writer.write(f)
    return output_path


def generate_event_type_pdf(
    client: AirtableClient,
    event_type: str,
    output_path: str,
    show_analytics: bool = False,
) -> str:
    """Render every 'Playlists' record for an event type, merged into one PDF."""
    playlists = list_event_type_playlists(client, event_type)
    if not playlists:
        raise SystemExit(f"❌ Aucune playlist trouvée pour l'event type : {event_type}")

    print(f"📋 {len(playlists)} playlists trouvées pour '{event_type}'")
    # Persistent cache: artworks are keyed by URL hash and repeat across
    # playlists/runs — re-downloading ~1000 images is the bulk of the runtime.
    artwork_cache = os.path.join(tempfile.gettempdir(), "tbp_playlist_art")
    os.makedirs(artwork_cache, exist_ok=True)
    part_paths: list[str] = []

    with tempfile.TemporaryDirectory(prefix="playlist_parts_") as parts_dir:
        for i, playlist in enumerate(playlists, 1):
            fields = playlist.get("fields", {}) or {}
            name = str(fields.get("Name") or playlist.get("id") or f"playlist-{i}")
            track_ids = fields.get("Tracks") or []
            print(f"  [{i}/{len(playlists)}] {name} ({len(track_ids)} tracks)")

            tracks = fetch_tracks_batched(client, track_ids)
            adapted = adapt_event_type_playlist_record(playlist)
            part_path = os.path.join(parts_dir, f"part_{i:03d}.pdf")
            generate_pdf(
                adapted,
                tracks,
                part_path,
                client=client,
                show_analytics=show_analytics,
                artwork_cache_dir=artwork_cache,
            )
            part_paths.append(part_path)

        merge_pdfs(part_paths, output_path)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Génère un PDF de tracklist à partir d'une playlist Airtable."
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- list command ---
    subparsers.add_parser("list", help="Lister toutes les playlists disponibles")

    # --- generate command ---
    gen_parser = subparsers.add_parser("generate", help="Générer le PDF d'une playlist")
    gen_parser.add_argument(
        "id",
        help="ID de l'enregistrement (champ 'ID' ou Airtable record ID)",
    )
    gen_parser.add_argument(
        "-o", "--output",
        help="Chemin du fichier PDF de sortie (défaut: output/<nom>.pdf)",
        default=None,
    )
    gen_parser.add_argument(
        "--analytics",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Afficher/masquer toutes les pages analytics (stats, non joués, joués hors Event type)",
    )

    # --- generate-event-type command ---
    et_parser = subparsers.add_parser(
        "generate-event-type",
        help="Générer un PDF regroupant toutes les playlists 'Playlists' d'un event type",
    )
    et_parser.add_argument(
        "event_type",
        help="Nom de l'event type (ex. 'Le Grand Karaoké de l'Amour', 'GKDA')",
    )
    et_parser.add_argument(
        "-o", "--output",
        help="Chemin du fichier PDF de sortie (défaut: output/Playlists - <event type>.pdf)",
        default=None,
    )
    et_parser.add_argument(
        "--analytics",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Inclure les pages analytics par playlist",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = AirtableClient()

    if args.command == "list":
        print("📋 Playlists disponibles :\n")
        playlists = list_playlists(client)
        for p in playlists:
            f = p.get("fields", {})
            pid = f.get("ID", p["id"])
            name = f.get("Name", "(sans nom)")
            decade = f.get("Decade", "")
            date_list = f.get("Date (DD/MM/YYYY) (from Event)", [])
            date_str = date_list[0] if isinstance(date_list, list) and date_list else ""
            track_count = len(f.get("Tracks", []))
            print(f"  {pid}  |  {name}  |  {date_str}  |  {decade}  |  {track_count} tracks")
        print(f"\n  Total : {len(playlists)} playlists")
        return

    if args.command == "generate":
        print(f"🔍 Recherche de la playlist '{args.id}'...")
        playlist = fetch_playlist(client, args.id)
        if not playlist:
            print(f"❌ Playlist introuvable pour l'ID : {args.id}")
            sys.exit(1)

        fields = playlist.get("fields", {})
        name = resolve_playlist_title(fields, client=client)
        track_ids = fields.get("Tracks", [])
        print(f"✅ Playlist trouvée : {name} ({len(track_ids)} tracks)")

        print("📥 Récupération des tracks...")
        tracks = fetch_tracks(client, track_ids)
        print(f"   {len(tracks)} tracks récupérées")

        # Determine output path
        if args.output:
            output_path = args.output
        else:
            os.makedirs("output", exist_ok=True)
            venue_name = resolve_venue_name(fields, client=client)
            date_list = fields.get("Date (DD/MM/YYYY) (from Event)", [])
            event_date = date_list[0] if isinstance(date_list, list) and date_list else ""
            if not isinstance(event_date, str):
                event_date = str(event_date)

            file_parts = [part.strip() for part in [name, venue_name, event_date] if isinstance(part, str) and part.strip()]
            file_name = " - ".join(file_parts) if file_parts else "playlist"
            safe_name = file_name.replace("/", "-").replace("\\", "-").replace(":", "-")
            output_path = os.path.join("output", f"{safe_name}.pdf")

        print(f"📄 Génération du PDF...")
        generate_pdf(
            playlist,
            tracks,
            output_path,
            client=client,
            show_analytics=bool(args.analytics),
        )
        print(f"✅ PDF généré : {output_path}")

    if args.command == "generate-event-type":
        output_path = args.output
        if not output_path:
            os.makedirs("output", exist_ok=True)
            safe_name = args.event_type.replace("/", "-").replace("\\", "-").replace(":", "-")
            output_path = os.path.join("output", f"Playlists - {safe_name}.pdf")

        print(f"📄 Génération du PDF...")
        generate_event_type_pdf(
            client,
            args.event_type,
            output_path,
            show_analytics=bool(args.analytics),
        )
        print(f"✅ PDF généré : {output_path}")


if __name__ == "__main__":
    main()
