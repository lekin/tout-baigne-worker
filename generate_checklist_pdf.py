#!/usr/bin/env python3
"""Generate a PDF checklist from an Airtable Event record."""

import argparse
import hashlib
import os
import re
import sys
import tempfile
import unicodedata

import requests
from fpdf import FPDF
from src.airtable_client import AirtableClient
from src.config import settings


EVENT_TABLE = "Events"
EVENT_CHECKLIST_TABLE = "Event Checklist"


# ── Airtable helpers ──────────────────────────────────────────────────

def list_events(client: AirtableClient, upcoming_only: bool = False) -> list[dict]:
    """List events, optionally filtering to upcoming only."""
    table = client.get_table(EVENT_TABLE)
    formula = "{Upcoming or past} = 'Upcoming'" if upcoming_only else None
    return table.all(formula=formula, sort=["Date"])


def fetch_event(client: AirtableClient, identifier: str) -> dict | None:
    """Fetch a single event by record ID or name search."""
    table = client.get_table(EVENT_TABLE)

    # Direct record ID
    if identifier.startswith("rec"):
        try:
            return table.get(identifier)
        except Exception:
            pass

    # Search by Name
    records = table.all(
        formula=f"FIND('{identifier}', {{Name}})", max_records=1
    )
    return records[0] if records else None


def fetch_checklist_items(client: AirtableClient, checklist_ids: list[str]) -> list[dict]:
    """Fetch Event Checklist records by their IDs."""
    table = client.get_table(EVENT_CHECKLIST_TABLE)
    items = []
    for cid in checklist_ids:
        try:
            rec = table.get(cid)
            items.append(rec)
        except Exception:
            continue
    return items


# ── Phase sorting ─────────────────────────────────────────────────────

PHASE_ORDER = {
    "1. Pre": 1,
    "2. Setup": 2,
    "3. In-Progress": 3,
    "4. Breakdown": 4,
    "5. Post": 5,
}


def phase_sort_key(phase: str) -> int:
    """Return a numeric sort key for a phase string."""
    return PHASE_ORDER.get(phase, 99)


def group_by_phase(items: list[dict]) -> dict[str, list[dict]]:
    """Group checklist items by phase, sorted by phase order."""
    excluded_phases = {"1. Pre"}
    groups: dict[str, list[dict]] = {}
    for item in items:
        fields = item.get("fields", {})
        phase_list = fields.get("Phase (from Checklist item)", [])
        phase = phase_list[0] if phase_list else None
        if not phase:
            continue
        if phase in excluded_phases:
            continue
        groups.setdefault(phase, []).append(item)
    # Sort groups by phase order
    return dict(sorted(groups.items(), key=lambda kv: phase_sort_key(kv[0])))


# ── PDF generation ────────────────────────────────────────────────────

UNICODE_FONT_PATH = "/Library/Fonts/Arial Unicode.ttf"

# Phase badge colors
PHASE_COLORS = {
    "1. Pre":         (220, 230, 255),
    "2. Setup":       (220, 245, 220),
    "3. In-Progress": (255, 243, 210),
    "4. Breakdown":   (255, 220, 220),
    "5. Post":        (235, 225, 245),
}


class ChecklistPDF(FPDF):
    """Custom PDF for event checklists."""

    def __init__(
        self,
        event_name: str,
        event_date: str = "",
        venue: str = "",
        checklist_subtitle: str = "Checklist",
    ):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.event_name = event_name
        self.event_date = event_date
        self.venue = venue
        self.checklist_subtitle = checklist_subtitle
        self.set_auto_page_break(auto=True, margin=20)
        # Register Unicode font
        self.add_font("ArialUni", "", UNICODE_FONT_PATH)
        self.add_font("ArialUni", "B", UNICODE_FONT_PATH)
        self.add_font("ArialUni", "I", UNICODE_FONT_PATH)
        self.add_font("ArialUni", "BI", UNICODE_FONT_PATH)

    def header(self):
        # Top-right page numbering for every page
        self.set_font("ArialUni", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="L", new_x="LMARGIN", new_y="NEXT")

        if self.page_no() > 1:
            self.ln(4)
            return

        self.ln(2)
        # Title: Date - Event Type - Venue
        self.set_font("ArialUni", "B", 16)
        self.set_text_color(30, 30, 30)
        title = self.event_name
        self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="L")

        # Subtitle
        self.set_font("ArialUni", "B", 12)
        self.set_text_color(50, 50, 50)
        self.cell(0, 8, self.checklist_subtitle, new_x="LMARGIN", new_y="NEXT", align="L")
        self.ln(2)

        # Separator line
        self.set_draw_color(200, 200, 200)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self):
        # Intentionally empty: page numbering is rendered in header.
        return

    def draw_checkbox(self, x: float, y: float, size: float = 4.0):
        """Draw an empty checkbox square."""
        self.set_draw_color(120, 120, 120)
        self.set_line_width(0.3)
        self.rect(x, y, size, size, "D")

    def draw_phase_header(self, phase: str, count: int, min_next_row_h: float = 0.0):
        """Draw a phase section header with badge."""
        y = self.get_y()
        required_h = 14 + min_next_row_h
        phase_label = re.sub(r"^\d+\.\s*", "", phase or "")

        # Keep the phase header with at least the first row of the section.
        if y + required_h > self.page_break_trigger:
            self.add_page()
            y = self.get_y()

        self.ln(3)
        y = self.get_y()

        # Phase badge
        r, g, b = PHASE_COLORS.get(phase, (235, 235, 235))
        self.set_fill_color(r, g, b)
        self.set_font("ArialUni", "B", 10)
        badge_w = self.get_string_width(phase_label) + 8
        badge_h = 7
        self.rect(self.l_margin, y, badge_w, badge_h, "F",
                  round_corners=True, corner_radius=badge_h / 2)
        self.set_text_color(40, 40, 40)
        self.set_xy(self.l_margin, y)
        self.cell(badge_w, badge_h, phase_label, align="C")

        self.ln(badge_h + 2)

        # Table header
        self._draw_table_header()

    def _estimate_row_height(
        self,
        name: str,
        instructions: str,
        image_paths: list[str] | None = None,
    ) -> float:
        """Estimate row height to improve page break decisions."""
        line_h = 4.0
        widths = self._col_widths()
        img_h = 54

        self.set_font("ArialUni", "", 8)
        name_lines = self.multi_cell(
            widths[0] - 2, line_h, name or "", dry_run=True, output="LINES"
        )
        instr_lines = self.multi_cell(
            widths[1] - 2, line_h, instructions or "", dry_run=True, output="LINES"
        )
        name_h = len(name_lines) * line_h
        instr_h = len(instr_lines) * line_h

        valid_images = []
        if image_paths:
            from PIL import Image as PILImage

            for img_path in image_paths:
                if not img_path or not os.path.exists(img_path):
                    continue
                try:
                    with PILImage.open(img_path) as im:
                        iw, ih = im.size
                    aspect = iw / ih
                    display_w = img_h * aspect
                    valid_images.append((img_path, display_w))
                except Exception:
                    pass

        img_rows: list[list[tuple[str, float]]] = []
        if valid_images:
            avail_w = widths[1] - 2
            gap = 2
            current_row: list[tuple[str, float]] = []
            current_w = 0.0
            for img_path, display_w in valid_images:
                needed = display_w + (gap if current_row else 0)
                if current_row and current_w + needed > avail_w:
                    img_rows.append(current_row)
                    current_row = [(img_path, display_w)]
                    current_w = display_w
                else:
                    current_row.append((img_path, display_w))
                    current_w += needed
            if current_row:
                img_rows.append(current_row)

        img_gap_top = 1 if img_rows else 0
        img_total_h = (len(img_rows) * img_h + img_gap_top) if img_rows else 0
        text_h = max(name_h, instr_h, 7)
        return text_h + img_total_h

    def _draw_table_header(self):
        """Draw the column headers for the checklist table."""
        y = self.get_y()
        self.set_font("ArialUni", "", 7)
        self.set_text_color(130, 130, 130)

        col_x = self._col_positions()
        headers = ["Name", "Instructions", "Check"]
        widths = self._col_widths()

        for i, hdr in enumerate(headers):
            self.set_xy(col_x[i], y)
            self.cell(widths[i], 5, hdr, align="L" if i < 2 else "C")

        self.ln(6)
        # Thin separator
        self.set_draw_color(220, 220, 220)
        self.set_line_width(0.15)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(1)

    def _col_widths(self) -> list[float]:
        """Return column widths: [Name, Instructions, Checkbox]."""
        usable = self.w - self.l_margin - self.r_margin
        check_w = 12
        name_w = usable * 0.28
        instr_w = usable - name_w - check_w
        return [name_w, instr_w, check_w]

    def _col_positions(self) -> list[float]:
        """Return x positions for each column."""
        widths = self._col_widths()
        positions = [self.l_margin]
        for w in widths[:-1]:
            positions.append(positions[-1] + w)
        return positions

    def draw_checklist_row(self, name: str, instructions: str, row_idx: int,
                           image_paths: list[str] | None = None):
        """Draw a single checklist row with name, instructions, photos, and checkbox at end."""
        line_h = 4.0
        col_x = self._col_positions()
        widths = self._col_widths()
        checkbox_size = 4.0
        img_h = 54  # height for thumbnail images in mm

        # Measure text heights
        self.set_font("ArialUni", "", 8)
        name_lines = self.multi_cell(
            widths[0] - 2, line_h, name or "", dry_run=True, output="LINES"
        )
        instr_lines = self.multi_cell(
            widths[1] - 2, line_h, instructions or "", dry_run=True, output="LINES"
        )
        name_h = len(name_lines) * line_h
        instr_h = len(instr_lines) * line_h

        # Compute image layout: calculate actual widths from aspect ratios
        valid_images = []
        if image_paths:
            from PIL import Image as PILImage
            for img_path in image_paths:
                if not img_path or not os.path.exists(img_path):
                    continue
                try:
                    with PILImage.open(img_path) as im:
                        iw, ih = im.size
                    aspect = iw / ih
                    display_w = img_h * aspect
                    valid_images.append((img_path, display_w))
                except Exception:
                    pass

        has_images = len(valid_images) > 0
        # Layout images into rows that fit within the instructions column
        img_rows: list[list[tuple[str, float]]] = []
        if has_images:
            avail_w = widths[1] - 2
            gap = 2
            current_row: list[tuple[str, float]] = []
            current_w = 0.0
            for img_path, display_w in valid_images:
                needed = display_w + (gap if current_row else 0)
                if current_row and current_w + needed > avail_w:
                    img_rows.append(current_row)
                    current_row = [(img_path, display_w)]
                    current_w = display_w
                else:
                    current_row.append((img_path, display_w))
                    current_w += needed
            if current_row:
                img_rows.append(current_row)

        img_gap_top = 1 if img_rows else 0
        img_total_h = (len(img_rows) * img_h + img_gap_top) if img_rows else 0
        text_h = max(name_h, instr_h, 7)
        row_h = text_h + img_total_h

        y_start = self.get_y()

        # Page break check
        if y_start + row_h > self.page_break_trigger:
            self.add_page()
            self._draw_table_header()
            y_start = self.get_y()

        # Alternating row background
        if row_idx % 2 == 0:
            self.set_fill_color(248, 248, 248)
            self.rect(self.l_margin, y_start,
                      self.w - self.l_margin - self.r_margin, row_h, "F")

        # Name column
        self.set_font("ArialUni", "", 8)
        self.set_text_color(30, 30, 30)
        self.set_xy(col_x[0], y_start)
        self.multi_cell(widths[0] - 2, line_h, name or "", new_x="RIGHT", new_y="TOP")

        # Instructions column
        self.set_font("ArialUni", "", 8)
        self.set_text_color(60, 60, 60)
        y_offset = (text_h - instr_h) / 2
        self.set_xy(col_x[1], y_start + y_offset)
        self.multi_cell(widths[1] - 2, line_h, instructions or "", new_x="RIGHT", new_y="TOP")

        # Images below instructions, packed tightly with row wrapping
        if img_rows:
            gap = 2
            img_y = y_start + text_h + 1
            for row_imgs in img_rows:
                img_x = col_x[1]
                for img_path, display_w in row_imgs:
                    try:
                        self.image(img_path, x=img_x, y=img_y, h=img_h)
                    except Exception:
                        pass
                    img_x += display_w + gap
                img_y += img_h

        # Checkbox column (centered, vertically centered in text area)
        cb_y = y_start + (text_h - checkbox_size) / 2
        cb_x = col_x[2] + (widths[2] - checkbox_size) / 2
        self.draw_checkbox(cb_x, cb_y, checkbox_size)

        # Bottom separator
        self.set_draw_color(230, 230, 230)
        self.set_line_width(0.1)
        self.line(self.l_margin, y_start + row_h,
                  self.w - self.r_margin, y_start + row_h)

        self.set_y(y_start + row_h)


def download_image(url: str, cache_dir: str) -> str | None:
    """Download an image to a local temp file. Returns the file path or None."""
    try:
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


def extract_visual_urls(item: dict) -> list[str]:
    """Extract thumbnail URLs from a checklist item's visual instructions."""
    fields = item.get("fields", {})
    visuals = fields.get("Visual instruction (from Checklist item)", [])
    urls = []
    if not isinstance(visuals, list):
        return urls

    for att in visuals:
        if not isinstance(att, dict):
            continue
        # Prefer large thumbnail for reasonable size
        thumbs = att.get("thumbnails", {})
        large = thumbs.get("large", {})
        url = large.get("url") if large else att.get("url")
        if not url:
            url = att.get("url")
        if url:
            urls.append(url)
    return urls


def _normalize_text(value: str) -> str:
    """Normalize text for case/accent-insensitive comparisons."""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_like.casefold().strip()


def extract_item_tags(item: dict) -> list[str]:
    """Extract tag values from checklist item fields."""
    fields = item.get("fields", {})
    candidates = [
        fields.get("Tags (from Checklist item)"),
        fields.get("Tags"),
        fields.get("Tag"),
    ]

    tags: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            for value in candidate:
                if isinstance(value, str):
                    cleaned = value.strip()
                    if cleaned:
                        tags.append(cleaned)
        elif isinstance(candidate, str):
            cleaned = candidate.strip()
            if cleaned:
                tags.append(cleaned)
    return tags


def extract_item_staff_types(item: dict) -> list[str]:
    fields = item.get("fields", {})
    candidates = [
        fields.get("Staff type (from Checklist item)"),
        fields.get("Staff type"),
        fields.get("Staff types"),
    ]

    def _flatten_values(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else []
        if isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str):
                cleaned = name.strip()
                return [cleaned] if cleaned else []
            return []
        if isinstance(value, list):
            out: list[str] = []
            for v in value:
                out.extend(_flatten_values(v))
            return out
        return []

    staff_types: list[str] = []
    for candidate in candidates:
        staff_types.extend(_flatten_values(candidate))
    return staff_types


def _parse_filter_values(value: str | list[str] | None) -> list[str]:
    """Parse a filter value into a list of normalized terms.

    Accepts a single string, a list of strings, or comma-separated values.
    """
    if not value:
        return []

    raw_values: list[str] = []
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, list):
        raw_values = value

    terms: list[str] = []
    for raw in raw_values:
        for part in (raw or "").split(","):
            normalized = _normalize_text(part)
            if normalized:
                terms.append(normalized)
    return terms


def _display_filter_value(value: str | list[str] | None) -> str:
    """Return a human-readable display string for filter value(s)."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return ", ".join(str(v) for v in value if v)


def filter_checklist_items_by_tag(items: list[dict], tag: str | list[str] | None) -> list[dict]:
    """Filter checklist items by tag value(s) (case/accent-insensitive)."""
    wanted = _parse_filter_values(tag)
    if not wanted:
        return items

    filtered: list[dict] = []
    for item in items:
        item_tags = extract_item_tags(item)
        normalized_tags = {_normalize_text(item_tag) for item_tag in item_tags}
        if any(w in normalized_tags for w in wanted):
            filtered.append(item)
    return filtered


def filter_checklist_items_by_staff_type(items: list[dict], staff_type: str | list[str] | None) -> list[dict]:
    """Filter checklist items by staff type value(s) (case/accent-insensitive)."""
    wanted = _parse_filter_values(staff_type)
    if not wanted:
        return items

    filtered: list[dict] = []
    for item in items:
        item_staff_types = extract_item_staff_types(item)
        normalized_staff_types = {_normalize_text(v) for v in item_staff_types}
        if any(w in normalized_staff_types for w in wanted):
            filtered.append(item)
    return filtered


def exclude_checklist_items_by_tag(items: list[dict], tag: str | list[str] | None) -> list[dict]:
    """Exclude checklist items that have any of the given tag(s) (case/accent-insensitive)."""
    unwanted = _parse_filter_values(tag)
    if not unwanted:
        return items

    filtered: list[dict] = []
    for item in items:
        item_tags = extract_item_tags(item)
        normalized_tags = {_normalize_text(item_tag) for item_tag in item_tags}
        if not any(u in normalized_tags for u in unwanted):
            filtered.append(item)
    return filtered


def exclude_checklist_items_by_staff_type(items: list[dict], staff_type: str | list[str] | None) -> list[dict]:
    """Exclude checklist items that have any of the given staff type(s) (case/accent-insensitive)."""
    unwanted = _parse_filter_values(staff_type)
    if not unwanted:
        return items

    filtered: list[dict] = []
    for item in items:
        item_staff_types = extract_item_staff_types(item)
        normalized_staff_types = {_normalize_text(v) for v in item_staff_types}
        if not any(u in normalized_staff_types for u in unwanted):
            filtered.append(item)
    return filtered


def generate_checklist_pdf(
    event_record: dict,
    checklist_items: list[dict],
    output_path: str,
    checklist_subtitle: str = "Checklist",
):
    """Generate the checklist PDF for an event."""
    fields = event_record.get("fields", {})

    # Build title
    event_name = fields.get("Name", "Event")

    pdf = ChecklistPDF(event_name=event_name, checklist_subtitle=checklist_subtitle)
    pdf.alias_nb_pages()
    pdf.add_page()

    # Download visual instruction images to temp dir
    img_cache = tempfile.mkdtemp(prefix="checklist_img_")
    print("🖼️  Téléchargement des images...")
    item_images: dict[str, list[str | None]] = {}
    for item in checklist_items:
        urls = extract_visual_urls(item)
        paths = [download_image(u, img_cache) for u in urls] if urls else []
        item_images[item["id"]] = paths

    # Group items by phase
    grouped = group_by_phase(checklist_items)

    for phase, items in grouped.items():
        first_item = items[0] if items else None
        first_row_h = 0.0
        if first_item:
            first_fields = first_item.get("fields", {})
            first_name = first_fields.get("Name", "")
            first_instr_list = first_fields.get("Instructions (from Checklist item)", [])
            if isinstance(first_instr_list, list) and first_instr_list:
                first_instructions = first_instr_list[0] or ""
            elif isinstance(first_instr_list, str):
                first_instructions = first_instr_list
            else:
                first_instructions = ""
            first_instructions = first_instructions.replace("**", "").replace("__", "")
            first_instructions = first_instructions.replace("\t", "    ")
            first_img_paths = item_images.get(first_item["id"], [])
            first_row_h = pdf._estimate_row_height(
                first_name,
                first_instructions,
                image_paths=first_img_paths,
            )

        pdf.draw_phase_header(phase, len(items), min_next_row_h=first_row_h)

        for idx, item in enumerate(items):
            f = item.get("fields", {})
            name = f.get("Name", "")
            # Instructions can come from lookup field (list) or direct
            instr_list = f.get("Instructions (from Checklist item)", [])
            if isinstance(instr_list, list) and instr_list:
                instructions = instr_list[0] or ""
            elif isinstance(instr_list, str):
                instructions = instr_list
            else:
                instructions = ""
            # Clean up markdown bold markers and tabs for PDF
            instructions = instructions.replace("**", "").replace("__", "")
            instructions = instructions.replace("\t", "    ")
            # No truncation — show full instructions

            img_paths = item_images.get(item["id"], [])
            pdf.draw_checklist_row(name, instructions, idx, image_paths=img_paths)

        pdf.ln(2)

    pdf.output(output_path)
    return output_path


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Génère un PDF checklist à partir d'un événement Airtable."
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- list command ---
    list_parser = subparsers.add_parser("list", help="Lister les événements")
    list_parser.add_argument(
        "--upcoming", action="store_true",
        help="Afficher uniquement les événements à venir",
    )

    # --- generate command ---
    gen_parser = subparsers.add_parser("generate", help="Générer le PDF checklist d'un événement")
    gen_parser.add_argument(
        "id",
        help="Record ID Airtable de l'événement (ou recherche par nom)",
    )
    gen_parser.add_argument(
        "-o", "--output",
        help="Chemin du fichier PDF de sortie (défaut: output/<nom>.pdf)",
        default=None,
    )
    gen_parser.add_argument(
        "--tag", "--tags",
        dest="tag",
        default=None,
        action="append",
        help="Filtrer les items checklist par Tag (répétable ou séparé par des virgules)",
    )
    gen_parser.add_argument(
        "--exclude-tag", "--exclude-tags",
        dest="exclude_tag",
        default=None,
        action="append",
        help="Exclure les items checklist avec ce Tag (répétable ou séparé par des virgules)",
    )
    gen_parser.add_argument(
        "--staff-type",
        dest="staff_type",
        default=None,
        action="append",
        help="Filtrer les items checklist par Staff type (répétable ou séparé par des virgules)",
    )
    gen_parser.add_argument(
        "--exclude-staff-type", "--exclude-staff-types",
        dest="exclude_staff_type",
        default=None,
        action="append",
        help="Exclure les items checklist avec ce Staff type (répétable ou séparé par des virgules)",
    )

    # --- generate-all command ---
    all_parser = subparsers.add_parser(
        "generate-all",
        help="Générer les PDFs checklist de tous les événements à venir",
    )
    all_parser.add_argument(
        "-o", "--output-dir",
        help="Dossier de sortie (défaut: output/checklists)",
        default=None,
    )
    all_parser.add_argument(
        "--tag", "--tags",
        dest="tag",
        default=None,
        action="append",
        help="Filtrer les items checklist par Tag (répétable ou séparé par des virgules)",
    )
    all_parser.add_argument(
        "--exclude-tag", "--exclude-tags",
        dest="exclude_tag",
        default=None,
        action="append",
        help="Exclure les items checklist avec ce Tag (répétable ou séparé par des virgules)",
    )
    all_parser.add_argument(
        "--staff-type",
        dest="staff_type",
        default=None,
        action="append",
        help="Filtrer les items checklist par Staff type (répétable ou séparé par des virgules)",
    )
    all_parser.add_argument(
        "--exclude-staff-type", "--exclude-staff-types",
        dest="exclude_staff_type",
        default=None,
        action="append",
        help="Exclure les items checklist avec ce Staff type (répétable ou séparé par des virgules)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = AirtableClient()

    if args.command == "list":
        events = list_events(client, upcoming_only=args.upcoming)
        label = "à venir" if args.upcoming else "disponibles"
        print(f"📋 Événements {label} :\n")
        for ev in events:
            f = ev.get("fields", {})
            name = f.get("Name", "(sans nom)")
            checklist_count = len(f.get("Event Checklist", []))
            print(f"  {ev['id']}  |  {name}  |  {checklist_count} items")
        print(f"\n  Total : {len(events)} événements")
        return

    if args.command == "generate":
        _generate_single(
            client,
            args.id,
            args.output,
            tag_filter=args.tag,
            staff_type_filter=args.staff_type,
            exclude_tag=args.exclude_tag,
            exclude_staff_type=args.exclude_staff_type,
        )
        return

    if args.command == "generate-all":
        output_dir = args.output_dir or os.path.join("output", "checklists")
        os.makedirs(output_dir, exist_ok=True)

        events = list_events(client, upcoming_only=True)
        if not events:
            print("❌ Aucun événement à venir trouvé.")
            sys.exit(1)

        print(f"📋 {len(events)} événements à venir trouvés.\n")
        for ev in events:
            f = ev.get("fields", {})
            name = f.get("Name", "event")
            safe_name = name.replace("/", "-").replace("\\", "-").replace(":", "-")
            output_path = os.path.join(output_dir, f"{safe_name} - Checklist.pdf")
            _generate_single(
                client,
                ev["id"],
                output_path,
                tag_filter=args.tag,
                staff_type_filter=args.staff_type,
                exclude_tag=args.exclude_tag,
                exclude_staff_type=args.exclude_staff_type,
            )
            print()


def _generate_single(
    client: AirtableClient,
    identifier: str,
    output_path: str | None,
    tag_filter: str | list[str] | None = None,
    staff_type_filter: str | list[str] | None = None,
    exclude_tag: str | list[str] | None = None,
    exclude_staff_type: str | list[str] | None = None,
):
    """Generate a single event checklist PDF."""
    print(f"🔍 Recherche de l'événement '{identifier}'...")
    event = fetch_event(client, identifier)
    if not event:
        print(f"❌ Événement introuvable : {identifier}")
        sys.exit(1)

    fields = event.get("fields", {})
    name = fields.get("Name", "event")
    checklist_ids = fields.get("Event Checklist", [])
    print(f"✅ Événement trouvé : {name} ({len(checklist_ids)} checklist items)")

    if not checklist_ids:
        print("⚠️  Aucun item de checklist pour cet événement.")
        return

    print("📥 Récupération des items de checklist...")
    items = fetch_checklist_items(client, checklist_ids)
    print(f"   {len(items)} items récupérés")

    if staff_type_filter:
        filtered_items = filter_checklist_items_by_staff_type(items, staff_type_filter)
        print(
            f"👥 Filtre staff type '{_display_filter_value(staff_type_filter)}': "
            f"{len(filtered_items)}/{len(items)} items conservés"
        )
        items = filtered_items

    if tag_filter:
        filtered_items = filter_checklist_items_by_tag(items, tag_filter)
        print(
            f"🏷️  Filtre tag '{_display_filter_value(tag_filter)}': "
            f"{len(filtered_items)}/{len(items)} items conservés"
        )
        items = filtered_items

    if exclude_staff_type:
        filtered_items = exclude_checklist_items_by_staff_type(items, exclude_staff_type)
        print(
            f"🚫 Exclusion staff type '{_display_filter_value(exclude_staff_type)}': "
            f"{len(filtered_items)}/{len(items)} items conservés"
        )
        items = filtered_items

    if exclude_tag:
        filtered_items = exclude_checklist_items_by_tag(items, exclude_tag)
        print(
            f"🚫 Exclusion tag '{_display_filter_value(exclude_tag)}': "
            f"{len(filtered_items)}/{len(items)} items conservés"
        )
        items = filtered_items

    if not items:
        if staff_type_filter and tag_filter:
            print(
                f"⚠️  Aucun item avec le staff type '{_display_filter_value(staff_type_filter)}' "
                f"et le tag '{_display_filter_value(tag_filter)}' pour cet événement."
            )
        elif staff_type_filter:
            print(
                f"⚠️  Aucun item avec le staff type '{_display_filter_value(staff_type_filter)}' "
                f"pour cet événement."
            )
        elif tag_filter:
            print(f"⚠️  Aucun item avec le tag '{_display_filter_value(tag_filter)}' pour cet événement.")
        elif exclude_staff_type or exclude_tag:
            print("⚠️  Aucun item de checklist après application des exclusions.")
        else:
            print("⚠️  Aucun item de checklist pour cet événement.")
        return

    # Determine output path
    if not output_path:
        os.makedirs("output", exist_ok=True)
        safe_name = name.replace("/", "-").replace("\\", "-").replace(":", "-")
        output_path = os.path.join("output", f"{safe_name} - Checklist.pdf")

    checklist_subtitle = "Checklist"

    print(f"📄 Génération du PDF...")
    generate_checklist_pdf(event, items, output_path, checklist_subtitle=checklist_subtitle)
    print(f"✅ PDF généré : {output_path}")


if __name__ == "__main__":
    main()
