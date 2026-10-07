"""
DJ Set Track Identifier
Identifies tracks in a DJ set recording using Shazam audio fingerprinting.

Splits the audio into overlapping segments and recognizes each one,
then deduplicates and produces a tracklist with timestamps.
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.shazam_fingerprint import recognize_segment


@dataclass
class IdentifiedTrack:
    title: str
    artist: str
    album: str = ""
    label: str = ""
    year: str = ""
    isrc: str = ""
    shazam_id: str = ""
    cover_url: str = ""
    first_seen_at: float = 0.0  # seconds into the set
    last_seen_at: float = 0.0
    confidence: int = 0
    occurrences: int = 1

    @property
    def key(self) -> str:
        return f"{self.artist.lower().strip()}|{self.title.lower().strip()}"

    def timestamp_str(self) -> str:
        """Format first_seen_at as MM:SS or HH:MM:SS."""
        total = int(self.first_seen_at)
        h, remainder = divmod(total, 3600)
        m, s = divmod(remainder, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


@dataclass
class IdentificationResult:
    source_file: str
    duration_seconds: float
    tracks: list[IdentifiedTrack] = field(default_factory=list)
    segment_duration: int = 15
    segment_step: int = 10
    total_segments: int = 0
    recognized_segments: int = 0


def _parse_shazam_result(
    result: dict,
    start_ms: int,
    end_ms: int,
) -> IdentifiedTrack | None:
    """Parse a Shazam API response into an IdentifiedTrack."""
    if not result or "track" not in result:
        return None

    track_data = result["track"]
    sections = track_data.get("sections", [])
    metadata = {}
    for section in sections:
        if section.get("type") == "SONG":
            for item in section.get("metadata", []):
                metadata[item.get("title", "").lower()] = item.get("text", "")

    return IdentifiedTrack(
        title=track_data.get("title", "Unknown"),
        artist=track_data.get("subtitle", "Unknown"),
        album=metadata.get("album", ""),
        label=metadata.get("label", ""),
        year=metadata.get("released", metadata.get("année", "")),
        isrc=track_data.get("isrc", ""),
        shazam_id=track_data.get("key", ""),
        cover_url=track_data.get("images", {}).get("coverart", ""),
        first_seen_at=start_ms / 1000.0,
        last_seen_at=end_ms / 1000.0,
    )


def identify_dj_set(
    audio_path: str,
    segment_duration: int = 12,
    segment_step: int = 30,
    progress_callback=None,
    on_new_track_callback=None,
) -> IdentificationResult:
    """
    Identify tracks in a DJ set recording.

    Uses pure Python Shazam fingerprinting (no shazamio-core) with
    direct HTTP requests to Shazam's API.

    Args:
        audio_path: Path to the audio file (m4a, mp3, wav, etc.)
        segment_duration: Duration of each analysis segment in seconds
        segment_step: Step between segments in seconds
        progress_callback: Optional callback(current, total, message)

    Returns:
        IdentificationResult with deduplicated tracklist
    """
    audio_path = str(Path(audio_path).resolve())
    print(f"🎵 Chargement de l'audio: {audio_path}")

    # Get duration via ffprobe
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True,
    )
    duration_s = float(probe.stdout.strip())
    duration_ms = int(duration_s * 1000)
    print(f"   Durée: {int(duration_s // 60)}m {int(duration_s % 60)}s")

    # Calculate segments
    seg_dur_ms = segment_duration * 1000
    seg_step_ms = segment_step * 1000
    segments = []
    pos = 0
    while pos + seg_dur_ms <= duration_ms:
        segments.append((pos, pos + seg_dur_ms))
        pos += seg_step_ms
    if pos < duration_ms and duration_ms - pos > 5000:
        segments.append((pos, duration_ms))

    total_segments = len(segments)
    print(f"   {total_segments} segments à analyser (fenêtre {segment_duration}s, pas {segment_step}s)")

    result = IdentificationResult(
        source_file=audio_path,
        duration_seconds=duration_s,
        segment_duration=segment_duration,
        segment_step=segment_step,
        total_segments=total_segments,
    )

    tracks_by_key: dict[str, IdentifiedTrack] = {}
    recognized = 0
    consecutive_shazam_errors = 0
    consecutive_429 = 0
    cooldown_until = 0.0
    REQUEST_DELAY = 0.4
    RATE_LIMIT_THRESHOLD = 8
    MAX_RETRIES = 2

    for idx, (start_ms, end_ms) in enumerate(segments):
        start_s = start_ms / 1000.0
        dur_s = (end_ms - start_ms) / 1000.0
        if progress_callback:
            progress_callback(idx + 1, total_segments, f"Analyse segment {idx + 1}/{total_segments} ({int(start_s)}s)")
        print(f"  🔍 [{idx + 1}/{total_segments}] {int(start_s)}s...", end="", flush=True)

        now = time.monotonic()
        if cooldown_until and now < cooldown_until:
            wait_s = cooldown_until - now
            print(f" ⏳ cooldown {wait_s:.0f}s...", end="", flush=True)
            time.sleep(wait_s)

        track = None
        failure_reason = None
        attempts = 1 + (MAX_RETRIES if consecutive_shazam_errors >= RATE_LIMIT_THRESHOLD else 0)
        t0 = time.monotonic()

        for attempt in range(attempts):
            try:
                shazam_result = recognize_segment(audio_path, start_s=start_s, duration_s=dur_s)
                if isinstance(shazam_result, dict):
                    err = shazam_result.get("_error")
                    status = shazam_result.get("_http_status")
                    retry_after = shazam_result.get("_retry_after")
                    if err in ("no_samples", "no_signature"):
                        failure_reason = f"Extraction audio: {err}"
                        track = None
                        break

                    if err or (status is not None and status != 200):
                        if status is None:
                            failure_reason = f"Shazam error ({err or 'error'})"
                        else:
                            failure_reason = f"Shazam {status} ({err or 'error'})"

                        if status in (403, 429) or err in ("non_json", "http_error", "exception"):
                            consecutive_shazam_errors += 1

                        if status == 429:
                            consecutive_429 += 1
                            if consecutive_429 >= 3:
                                if retry_after is not None:
                                    try:
                                        wait_s = float(retry_after)
                                    except Exception:
                                        wait_s = 60.0
                                else:
                                    wait_s = min(600.0, 30.0 * (2 ** min(4, consecutive_429 - 3)))
                                cooldown_until = max(cooldown_until, time.monotonic() + wait_s)
                            if consecutive_429 >= 10:
                                print("\n❌ Trop de 429 consécutifs, arrêt pour éviter un blocage plus long.")
                                result.tracks = sorted(tracks_by_key.values(), key=lambda t: t.first_seen_at)
                                result.recognized_segments = recognized
                                return result
                        else:
                            consecutive_429 = 0

                        track = None
                        continue
                track = _parse_shazam_result(shazam_result, start_ms, end_ms)
            except Exception as e:
                print(f" ⚠ Erreur: {e}", end="", flush=True)
                track = None
                failure_reason = "Exception"

            if track:
                break
            if attempt < attempts - 1:
                retry_delay = 3 * (attempt + 1)
                print(f" ↻ retry dans {retry_delay}s...", end="", flush=True)
                time.sleep(retry_delay)

        elapsed = time.monotonic() - t0

        if track:
            consecutive_shazam_errors = 0
            consecutive_429 = 0
            recognized += 1
            key = track.key
            if key in tracks_by_key:
                existing = tracks_by_key[key]
                existing.occurrences += 1
                existing.last_seen_at = max(existing.last_seen_at, track.last_seen_at)
                existing.first_seen_at = min(existing.first_seen_at, track.first_seen_at)
                print(f" ✓ {track.artist} - {track.title} (déjà vu) [{elapsed:.1f}s]")
            else:
                tracks_by_key[key] = track
                print(f" ✓ {track.artist} - {track.title} [{elapsed:.1f}s]")
                if on_new_track_callback:
                    result.tracks = sorted(tracks_by_key.values(), key=lambda t: t.first_seen_at)
                    result.recognized_segments = recognized
                    on_new_track_callback(result)
        else:
            if failure_reason:
                print(f" ✗ {failure_reason} [{elapsed:.1f}s]")
            else:
                print(f" ✗ Non reconnu [{elapsed:.1f}s]")

        time.sleep(REQUEST_DELAY)

    # Sort tracks by first appearance
    sorted_tracks = sorted(tracks_by_key.values(), key=lambda t: t.first_seen_at)
    result.tracks = sorted_tracks
    result.recognized_segments = recognized

    return result


def format_tracklist(result: IdentificationResult, include_details: bool = False) -> str:
    """Format the identification result as a readable tracklist."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"🎧 TRACKLIST - DJ SET")
    lines.append(f"   Fichier: {Path(result.source_file).name}")
    lines.append(f"   Durée: {int(result.duration_seconds // 60)}m {int(result.duration_seconds % 60)}s")
    lines.append(f"   Segments analysés: {result.total_segments} | Reconnus: {result.recognized_segments}")
    lines.append(f"   Titres identifiés: {len(result.tracks)}")
    lines.append("=" * 60)
    lines.append("")

    for i, track in enumerate(result.tracks, 1):
        line = f"  {i:2d}. [{track.timestamp_str()}] {track.artist} — {track.title}"
        if track.occurrences > 1:
            dur = track.last_seen_at - track.first_seen_at
            line += f" (~{int(dur)}s)"
        lines.append(line)

        if include_details:
            details = []
            if track.album:
                details.append(f"Album: {track.album}")
            if track.label:
                details.append(f"Label: {track.label}")
            if track.year:
                details.append(f"Année: {track.year}")
            if track.isrc:
                details.append(f"ISRC: {track.isrc}")
            if details:
                lines.append(f"      {' | '.join(details)}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def export_tracklist_json(result: IdentificationResult, output_path: str):
    """Export tracklist as JSON."""
    data = {
        "source_file": result.source_file,
        "duration_seconds": result.duration_seconds,
        "total_segments": result.total_segments,
        "recognized_segments": result.recognized_segments,
        "tracks": [
            {
                "position": i + 1,
                "timestamp": track.timestamp_str(),
                "first_seen_at": track.first_seen_at,
                "last_seen_at": track.last_seen_at,
                "artist": track.artist,
                "title": track.title,
                "album": track.album,
                "label": track.label,
                "year": track.year,
                "isrc": track.isrc,
                "shazam_id": track.shazam_id,
                "cover_url": track.cover_url,
                "occurrences": track.occurrences,
            }
            for i, track in enumerate(result.tracks)
        ],
    }
    _atomic_write_text(output_path, json.dumps(data, ensure_ascii=False, indent=2))
    print(f"📄 Tracklist JSON exportée: {output_path}")


def _atomic_write_text(path: str, content: str) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Identifie les titres joués dans un enregistrement de DJ set"
    )
    parser.add_argument("audio_file", help="Chemin vers le fichier audio du DJ set")
    parser.add_argument(
        "--segment-duration",
        type=int,
        default=12,
        help="Durée de chaque segment d'analyse en secondes (défaut: 12)",
    )
    parser.add_argument(
        "--segment-step",
        type=int,
        default=30,
        help="Pas entre les segments en secondes (défaut: 30)",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Afficher les détails (album, label, année)",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        help="Exporter la tracklist en JSON vers ce fichier",
    )
    parser.add_argument(
        "--output",
        dest="text_output",
        help="Exporter la tracklist en texte vers ce fichier",
    )

    args = parser.parse_args()

    if not os.path.exists(args.audio_file):
        print(f"❌ Fichier introuvable: {args.audio_file}")
        sys.exit(1)

    t_start = time.monotonic()

    def on_new_track(result: IdentificationResult) -> None:
        if args.json_output:
            data = {
                "source_file": result.source_file,
                "duration_seconds": result.duration_seconds,
                "total_segments": result.total_segments,
                "recognized_segments": result.recognized_segments,
                "tracks": [
                    {
                        "position": i + 1,
                        "timestamp": track.timestamp_str(),
                        "first_seen_at": track.first_seen_at,
                        "last_seen_at": track.last_seen_at,
                        "artist": track.artist,
                        "title": track.title,
                        "album": track.album,
                        "label": track.label,
                        "year": track.year,
                        "isrc": track.isrc,
                        "shazam_id": track.shazam_id,
                        "cover_url": track.cover_url,
                        "occurrences": track.occurrences,
                    }
                    for i, track in enumerate(result.tracks)
                ],
            }
            _atomic_write_text(args.json_output, json.dumps(data, ensure_ascii=False, indent=2))

        if args.text_output:
            tracklist_text = format_tracklist(result, include_details=args.details)
            _atomic_write_text(args.text_output, tracklist_text)

    result = identify_dj_set(
        audio_path=args.audio_file,
        segment_duration=args.segment_duration,
        segment_step=args.segment_step,
        on_new_track_callback=on_new_track,
    )
    elapsed_total = time.monotonic() - t_start
    print(f"\n⏱ Analyse terminée en {int(elapsed_total // 60)}m {int(elapsed_total % 60)}s")

    tracklist = format_tracklist(result, include_details=args.details)
    print()
    print(tracklist)

    if args.json_output:
        export_tracklist_json(result, args.json_output)

    if args.text_output:
        with open(args.text_output, "w", encoding="utf-8") as f:
            f.write(tracklist)
        print(f"📄 Tracklist texte exportée: {args.text_output}")


if __name__ == "__main__":
    main()
