"""Playlist generator with energy model-based track arrangement.

Extracts general guidelines from the "We Are The 90's" event structure
and applies them to any decade/event. The energy model is:

  1. WARMUP        - Low energy, accessible tracks to ease the crowd in
  2. LAUNCH        - Iconic opener / first big genre block (Dance/EDM)
  3. GENRE ROTATION - Alternate genres every 15-20 min (~4-5 tracks) to
                      maintain constant energy and re-dynamize regularly
  4. BREATHING     - Anthems / sing-alongs to let the crowd breathe
                      (placed between intense blocks)
  5. PEAK RESERVE  - Keep peak-time tracks for late-night (after ~4h30)
  6. WIND DOWN     - Last 30 min: softer tracks, pop, câlins
  7. CLOSER        - Signature closing track

Each model is a list of PlaylistBlock definitions that the generator
fills from the available track pool.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
import random

import click


# ─────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────

@dataclass
class PlaylistBlock:
    """A block/phase in the playlist energy model."""
    name: str
    genres: list[str]
    tags: list[str] = field(default_factory=list)
    track_count: int = 5
    energy: str = "medium"  # low, medium, high, peak


# ─────────────────────────────────────────────────────────────────────
# Energy models
# ─────────────────────────────────────────────────────────────────────

# 2010s model — inspired by "We Are The 90's" structure, adapted to
# the dominant genres of the 2010s decade:
#   Pop (68), Dance (29), Hip-hop (17), R&B (17), Rock (14),
#   Reggaeton (11), Electro (10), Dancehall (10), Latin (10),
#   Funk (8), House/electro (7), Soul (5), Disco (5), Trap (3), Afro (2)
#
# Key difference vs 90s: Latin/Reggaeton is a major new movement,
# EDM/Electro is the dominant dance genre, Trap emerges within Hip-hop.

MODEL_2010S: list[PlaylistBlock] = [
    PlaylistBlock(
        "Warmup",
        genres=["Pop", "R&B", "Soul", "Funk"],
        tags=["Warmup"],
        track_count=5,
        energy="low",
    ),
    PlaylistBlock(
        "Lancement Dance / EDM",
        genres=["Dance", "Electro", "House / electro", "House", "Disco"],
        tags=["Start"],
        track_count=5,
        energy="high",
    ),
    PlaylistBlock(
        "Hip-hop / Trap",
        genres=["Hip-hop", "Trap", "Français"],
        track_count=4,
        energy="high",
    ),
    PlaylistBlock(
        "Pop Hits",
        genres=["Pop"],
        track_count=5,
        energy="medium",
    ),
    PlaylistBlock(
        "Latin / Reggaeton",
        genres=["Reggaeton", "Latin", "Dancehall", "Afro"],
        track_count=4,
        energy="high",
    ),
    PlaylistBlock(
        "Respiration",
        genres=["Pop", "R&B", "Rock"],
        tags=["Ca va gueuler", "Virgule"],
        track_count=3,
        energy="low",
    ),
    PlaylistBlock(
        "Dance Peak",
        genres=["Dance", "Electro", "House / electro", "Disco"],
        track_count=5,
        energy="peak",
    ),
    PlaylistBlock(
        "R&B / Funk Groove",
        genres=["R&B", "Funk", "Soul"],
        track_count=4,
        energy="medium",
    ),
    PlaylistBlock(
        "Hip-hop Peak",
        genres=["Hip-hop", "Trap", "Français"],
        track_count=4,
        energy="high",
    ),
    PlaylistBlock(
        "Rock / Indie",
        genres=["Rock"],
        track_count=4,
        energy="medium",
    ),
    PlaylistBlock(
        "Late Night Dance",
        genres=["Dance", "Reggaeton", "Afro", "Dancehall", "Latin", "Electro"],
        track_count=5,
        energy="high",
    ),
    PlaylistBlock(
        "Wind Down",
        genres=["Pop", "R&B", "Soul"],
        tags=["Warmdown"],
        track_count=4,
        energy="low",
    ),
    PlaylistBlock(
        "Closer",
        genres=["Pop", "Rock"],
        tags=["Finish"],
        track_count=1,
        energy="low",
    ),
]

MODELS: dict[str, list[PlaylistBlock]] = {
    "2010s": MODEL_2010S,
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

EXCLUDE_TAGS = {"veto", "off"}


def _track_genres(fields: dict) -> list[str]:
    g = fields.get("Genre", [])
    if isinstance(g, list):
        return g
    if isinstance(g, str) and g.strip():
        return [g.strip()]
    return []


def _track_tags(fields: dict) -> list[str]:
    t = fields.get("Tags", [])
    if isinstance(t, list):
        return t
    if isinstance(t, str) and t.strip():
        return [t.strip()]
    return []


def _track_label(fields: dict) -> str:
    artist = fields.get("Artist (string)", "")
    if not artist:
        names = fields.get("Name (from Artist)", [])
        artist = ", ".join(names) if isinstance(names, list) else str(names or "")
    title = fields.get("Title", "") or fields.get("Name", "")
    label = f"{artist} - {title}".strip(" -")
    return label or "?"


def _genres_match(track_genres: list[str], block_genres: list[str]) -> bool:
    return bool(
        set(g.lower() for g in track_genres)
        & set(g.lower() for g in block_genres)
    )


def _tags_match(track_tags: list[str], block_tags: list[str]) -> bool:
    return bool(
        set(t.lower() for t in track_tags)
        & set(t.lower() for t in block_tags)
    )


def _is_excluded(fields: dict) -> bool:
    tags = _track_tags(fields)
    return bool(set(t.lower() for t in tags) & EXCLUDE_TAGS)


def _track_bpm(fields: dict) -> Optional[float]:
    """Extract BPM from Tempo (Spotify) field."""
    t = fields.get("Tempo (Spotify)", None)
    if isinstance(t, list) and t:
        t = t[0]
    try:
        val = float(t) if t else 0.0
        return round(val, 1) if val > 0 else None
    except (ValueError, TypeError):
        return None


def _track_duration_seconds(fields: dict) -> float:
    d = fields.get("Duration (Spotify)", 0)
    if isinstance(d, list) and d:
        d = d[0]
    try:
        if not d:
            return 0.0
        value = float(d)
        if value <= 0:
            return 0.0
        if value > 1000:
            return value / 1000.0
        return value
    except (ValueError, TypeError):
        return 0.0


def _estimated_track_duration_seconds(fields: dict, default_seconds: float = 240.0) -> float:
    duration = _track_duration_seconds(fields)
    if duration > 0:
        return duration
    return float(default_seconds)


def estimate_playlist_duration_seconds(
    playlist: list[tuple[str, dict, str]],
    default_track_seconds: float = 240.0,
) -> float:
    return sum(
        _estimated_track_duration_seconds(r.get("fields", {}) or {}, default_track_seconds)
        for _, r, _ in playlist
    )


def _move_matching_tracks_later(
    playlist: list[tuple[str, dict, str]],
    keywords: tuple[str, ...],
    min_ratio: float,
) -> list[tuple[str, dict, str]]:
    if not playlist or not keywords:
        return playlist

    target_index = max(0, min(len(playlist) - 1, int(len(playlist) * max(0.0, min(0.95, min_ratio)))))
    keywords_lower = tuple(k.lower().strip() for k in keywords if k and k.strip())
    if not keywords_lower:
        return playlist

    out = list(playlist)
    for idx, (_, record, _) in enumerate(list(out)):
        fields = record.get("fields", {}) or {}
        title_blob = f"{fields.get('Title', '')} {fields.get('Name', '')} {_track_label(fields)}".lower()
        if any(k in title_blob for k in keywords_lower):
            if idx < target_index:
                item = out.pop(idx)
                insert_at = min(target_index, len(out))
                out.insert(insert_at, item)
            break
    return out


# ─────────────────────────────────────────────────────────────────────
# Core algorithm
# ─────────────────────────────────────────────────────────────────────

def arrange_tracks(
    records: list[dict],
    model: list[PlaylistBlock],
    shuffle_within_blocks: bool = True,
    seed: Optional[int] = None,
    target_duration_seconds: Optional[float] = None,
    default_track_seconds: float = 240.0,
    late_track_keywords: tuple[str, ...] = (),
    late_track_min_ratio: float = 0.60,
) -> list[tuple[str, dict, str]]:
    """Arrange tracks into a playlist following the energy model.

    Returns list of (record_id, record, block_name) tuples in playlist order.
    """
    if seed is not None:
        random.seed(seed)

    # Build pool (exclude vetoed tracks)
    pool: dict[str, dict] = {}
    for r in records:
        rid = r.get("id", "")
        fields = r.get("fields", {}) or {}
        if _is_excluded(fields):
            continue
        pool[rid] = r

    used: set[str] = set()
    playlist: list[tuple[str, dict, str]] = []

    for block in model:
        block_tracks: list[tuple[str, dict, str]] = []

        # 1) Tag-first: find tracks matching block tags
        if block.tags:
            tagged = []
            for rid, r in pool.items():
                if rid in used:
                    continue
                fields = r.get("fields", {}) or {}
                if _tags_match(_track_tags(fields), block.tags):
                    tagged.append((rid, r))
            if shuffle_within_blocks:
                random.shuffle(tagged)
            for rid, r in tagged[: block.track_count]:
                block_tracks.append((rid, r, block.name))
                used.add(rid)

        # 2) Genre-based fill for remaining slots
        remaining = block.track_count - len(block_tracks)
        if remaining > 0 and block.genres:
            genre_matches = []
            for rid, r in pool.items():
                if rid in used:
                    continue
                fields = r.get("fields", {}) or {}
                if _genres_match(_track_genres(fields), block.genres):
                    genre_matches.append((rid, r))
            if shuffle_within_blocks:
                random.shuffle(genre_matches)
            for rid, r in genre_matches[:remaining]:
                block_tracks.append((rid, r, block.name))
                used.add(rid)

        # 3) Sort block tracks by BPM for smoother transitions
        block_tracks.sort(
            key=lambda t: _track_bpm(t[1].get("fields", {}) or {}) or 999.0
        )

        playlist.extend(block_tracks)

    target_seconds = float(target_duration_seconds or 0.0)
    if target_seconds > 0.0:
        rotation_blocks = [
            block
            for block in model
            if block.name.lower() not in {"warmup", "respiration", "wind down", "closer"}
        ]
        if not rotation_blocks:
            rotation_blocks = list(model)

        rotation_index = 0
        while estimate_playlist_duration_seconds(playlist, default_track_seconds) < target_seconds:
            remaining = [
                (rid, r)
                for rid, r in pool.items()
                if rid not in used
            ]
            if not remaining:
                break

            block = rotation_blocks[rotation_index % len(rotation_blocks)]
            rotation_index += 1

            candidates = [
                (rid, r)
                for rid, r in remaining
                if _genres_match(_track_genres((r.get("fields", {}) or {})), block.genres)
            ]
            if not candidates:
                candidates = remaining

            if shuffle_within_blocks:
                random.shuffle(candidates)

            rid, r = candidates[0]
            used.add(rid)
            playlist.append((rid, r, f"{block.name} (Extended)"))

    if late_track_keywords:
        playlist = _move_matching_tracks_later(playlist, late_track_keywords, late_track_min_ratio)

    return playlist


def get_unused_tracks(
    records: list[dict],
    playlist: list[tuple[str, dict, str]],
) -> list[dict]:
    """Return records not used in the playlist (excluding vetoed)."""
    used_ids = {rid for rid, _, _ in playlist}
    unused = []
    for r in records:
        rid = r.get("id", "")
        fields = r.get("fields", {}) or {}
        if rid not in used_ids and not _is_excluded(fields):
            unused.append(r)
    return unused


# ─────────────────────────────────────────────────────────────────────
# Display
# ─────────────────────────────────────────────────────────────────────

ENERGY_ICONS = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🟠",
    "peak": "🔴",
}


def print_playlist_summary(
    playlist: list[tuple[str, dict, str]],
    model: list[PlaylistBlock],
):
    """Print a formatted summary of the generated playlist."""
    energy_map = {b.name: b.energy for b in model}
    current_block = None
    track_idx = 0

    for rid, record, block_name in playlist:
        if block_name != current_block:
            current_block = block_name
            icon = ENERGY_ICONS.get(energy_map.get(block_name, "medium"), "⚪")
            click.echo(f"\n  {icon} ┌─ {block_name} ─────────────────────")

        track_idx += 1
        fields = record.get("fields", {}) or {}
        label = _track_label(fields)
        genres = ", ".join(_track_genres(fields))
        dur = _track_duration_seconds(fields)
        dur_str = f"{int(dur // 60)}:{int(dur % 60):02d}" if dur > 0 else ""
        bpm = _track_bpm(fields)
        bpm_str = f"  {bpm:.0f}bpm" if bpm else ""
        click.echo(f"  │ {track_idx:3d}. {label}  [{genres}] {dur_str}{bpm_str}")

    click.echo("")
    click.echo(f"  ── Total: {len(playlist)} tracks")

    total_seconds = estimate_playlist_duration_seconds(playlist)
    if total_seconds > 0:
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        click.echo(f"  ── Durée estimée: {hours}h{minutes:02d}m")


def print_model_overview(model: list[PlaylistBlock]):
    """Print a summary of the energy model blocks."""
    click.echo("\n  📋 Modèle de playlist :\n")
    total = sum(b.track_count for b in model)
    for b in model:
        icon = ENERGY_ICONS.get(b.energy, "⚪")
        genres_str = ", ".join(b.genres[:4])
        if len(b.genres) > 4:
            genres_str += "…"
        tags_str = f"  tags: {', '.join(b.tags)}" if b.tags else ""
        click.echo(
            f"  {icon} {b.name:<25s}  {b.track_count} tracks  "
            f"[{genres_str}]{tags_str}"
        )
    click.echo(f"\n  Total cible: ~{total} tracks")


def print_genre_distribution(records: list[dict]):
    """Print genre distribution of available tracks."""
    genres: dict[str, int] = {}
    for r in records:
        fields = r.get("fields", {}) or {}
        if _is_excluded(fields):
            continue
        for g in _track_genres(fields):
            genres[g] = genres.get(g, 0) + 1
    click.echo("\n  🎵 Genres disponibles :")
    for g, count in sorted(genres.items(), key=lambda x: -x[1]):
        bar = "█" * min(count, 40)
        click.echo(f"     {g:<20s} {count:3d}  {bar}")
