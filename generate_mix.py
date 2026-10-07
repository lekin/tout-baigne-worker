#!/usr/bin/env python3
"""Generate an audio mix from the first N tracks of an Airtable playlist."""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile

import requests
from src.airtable_client import AirtableClient
from src.config import settings


PLAYLIST_TABLE = "Playlists (from events)"
TRACKS_TABLE = "Tracks"
TRANSITION_BARS = 8  # transition length in musical bars
DEFAULT_BPM = 120


def _normalize_google_drive_download_url(url: str) -> str:
    try:
        if not url:
            return url
        if "drive.google.com" not in url:
            return url
        if "uc?export=download" in url and "id=" in url:
            return url
        if "/file/d/" in url:
            file_id = url.split("/file/d/")[1].split("/")[0]
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        if "id=" in url:
            file_id = url.split("id=")[1].split("&")[0]
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        return url
    except Exception:
        return url


def download_google_drive_file(url: str, dst_path: str, timeout_seconds: int = 120) -> bool:
    """Download a file from Google Drive, handling virus scan warning pages."""
    try:
        from urllib.parse import unquote

        url = _normalize_google_drive_download_url(str(url))
        session = requests.Session()
        with session.get(url, stream=True, allow_redirects=True, timeout=timeout_seconds) as r:
            r.raise_for_status()
            content_type = (r.headers.get("content-type") or "").lower()
            if "text/html" not in content_type:
                with open(dst_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
                return True

            html = r.text

            # Try form-based download
            form_action = None
            form_method = "get"
            form_payload = {}
            form_match = re.search(r"(<form\b[^>]*>.*?</form>)", html, flags=re.IGNORECASE | re.DOTALL)
            if form_match:
                form_block = form_match.group(1)
                m_action = re.search(r"action\s*=\s*(['\"])(.*?)\1", form_block, flags=re.IGNORECASE)
                if m_action:
                    form_action = m_action.group(2)
                m_method = re.search(r"method\s*=\s*(['\"])?(get|post)\1?", form_block, flags=re.IGNORECASE)
                if m_method:
                    form_method = (m_method.group(2) or "get").lower()
                for input_tag in re.findall(r"<input\b[^>]*>", form_block, flags=re.IGNORECASE):
                    m_name = re.search(r"name\s*=\s*(['\"])([^'\"]+)\1", input_tag, flags=re.IGNORECASE)
                    if not m_name:
                        continue
                    name = m_name.group(2)
                    m_value = re.search(r"value\s*=\s*(['\"])([^'\"]*)\1", input_tag, flags=re.IGNORECASE)
                    value = m_value.group(2) if m_value else ""
                    form_payload[name] = value

            if form_action:
                action_url = form_action.replace("\\u003d", "=").replace("\\u0026", "&").replace("&amp;", "&")
                action_url = unquote(action_url)
                if action_url.startswith("/"):
                    action_url = "https://drive.google.com" + action_url
                try:
                    req = session.post if form_method == "post" else session.get
                    with req(
                        action_url,
                        data=form_payload if form_method == "post" else None,
                        params=form_payload if form_method != "post" else None,
                        stream=True, allow_redirects=True, timeout=timeout_seconds,
                    ) as rp:
                        rp.raise_for_status()
                        ctp = (rp.headers.get("content-type") or "").lower()
                        if "text/html" not in ctp:
                            with open(dst_path, "wb") as f:
                                for chunk in rp.iter_content(chunk_size=1024 * 256):
                                    if chunk:
                                        f.write(chunk)
                            return True
                except Exception:
                    pass

            # Fallback: confirm token approach
            confirm_token = None
            for k, v in session.cookies.items():
                if k.startswith("download_warning"):
                    confirm_token = v
                    break
            if not confirm_token:
                m = re.search(r"confirm=([0-9A-Za-z_\-]+)", html)
                if m:
                    confirm_token = m.group(1)

            file_id = None
            if "id=" in url:
                file_id = url.split("id=")[1].split("&")[0]
            if not file_id:
                return False

            confirm_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            if confirm_token:
                confirm_url += f"&confirm={confirm_token}"
            else:
                confirm_url += "&confirm=t"

            with session.get(confirm_url, stream=True, allow_redirects=True, timeout=timeout_seconds) as r2:
                r2.raise_for_status()
                ct2 = (r2.headers.get("content-type") or "").lower()
                if "text/html" in ct2:
                    return False
                with open(dst_path, "wb") as f:
                    for chunk in r2.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
            return True
    except Exception:
        return False


def get_audio_url(fields: dict) -> str | None:
    """Extract Google Drive download URL from track fields."""
    gdrive_link = fields.get("Link (from GDrive Audio files)")
    if isinstance(gdrive_link, list) and gdrive_link:
        gdrive_link = gdrive_link[0]
    if isinstance(gdrive_link, str) and gdrive_link:
        return _normalize_google_drive_download_url(gdrive_link)
    return None


def fetch_playlist_tracks(client: AirtableClient, playlist_id: str, limit: int = 5):
    """Fetch playlist and its first N tracks."""
    table = client.get_table(PLAYLIST_TABLE)

    # Try direct record ID
    playlist = None
    if playlist_id.startswith("rec"):
        try:
            playlist = table.get(playlist_id)
        except Exception:
            pass

    if not playlist:
        records = table.all(formula=f"{{ID}} = '{playlist_id}'", max_records=1)
        if records:
            playlist = records[0]

    if not playlist:
        return None, []

    fields = playlist.get("fields", {})
    track_ids = fields.get("Tracks", [])[:limit]

    tracks_table = client.get_table(TRACKS_TABLE)
    tracks = []
    for tid in track_ids:
        try:
            rec = tracks_table.get(tid)
            tracks.append(rec)
        except Exception:
            pass

    return playlist, tracks


def download_track(track: dict, download_dir: str, index: int) -> str | None:
    """Download a track's audio file. Returns local file path or None."""
    fields = track.get("fields", {})
    url = get_audio_url(fields)
    if not url:
        print(f"  ⚠️  Pas d'URL audio pour : {fields.get('Name', '?')}")
        return None

    name = fields.get("Name (from GDrive Audio files)", ["track"])[0] if fields.get("Name (from GDrive Audio files)") else f"track_{index}.mp3"
    dst = os.path.join(download_dir, f"{index:02d}_{name}")
    if not dst.endswith(".mp3"):
        dst += ".mp3"

    print(f"  📥 Téléchargement : {fields.get('Name', '?')}...")
    ok = download_google_drive_file(url, dst)
    if ok and os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return dst
    else:
        print(f"  ❌ Échec du téléchargement")
        return None


def get_duration_seconds(filepath: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def bars_to_seconds(bars: int, bpm: float) -> float:
    """Convert a number of bars (4/4 time) to seconds at a given BPM."""
    beats = bars * 4
    return beats * 60.0 / bpm


def transition_bpm(bpm_out: float, bpm_in: float) -> float:
    """Calculate the target BPM for a transition (midpoint)."""
    return (bpm_out + bpm_in) / 2.0


def normalize_track(src: str, dst: str) -> bool:
    """Normalize a track: strip video, convert to WAV 44.1kHz stereo, apply loudnorm."""
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", src, "-vn",
            "-af", "loudnorm=I=-14:TP=-1:LRA=11",
            "-ar", "44100", "-ac", "2", "-sample_fmt", "s16",
            dst,
        ],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and os.path.exists(dst)


def time_stretch_track(src: str, dst: str, original_bpm: float, target_bpm: float) -> bool:
    """Time-stretch a track to match target BPM using rubberband (pitch-preserving)."""
    ratio = target_bpm / original_bpm
    if abs(ratio - 1.0) < 0.005:
        # No stretching needed
        subprocess.run(["cp", src, dst], check=True)
        return True
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", src,
            "-af", f"rubberband=tempo={ratio}",
            "-ar", "44100", "-ac", "2",
            dst,
        ],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and os.path.exists(dst)


NUM_TRANSITION_STEPS = 8  # number of discrete filter steps in a transition
SAMPLE_RATE = 44100


def detect_outro(filepath: str, bpm: float, beat_offset: float) -> float:
    """Detect where the track's outro/fade-out begins.

    Uses RMS energy per bar to find where the track's energy starts declining.
    For tracks without a clear fade-out, returns a safe point snapped to an
    8-bar phrase boundary before the end, ensuring clean loops.
    """
    import numpy as np

    bar_period = 4 * 60.0 / bpm
    duration = get_duration_seconds(filepath)

    # Analyze the last 50% of the track
    analysis_start = max(0, duration * 0.5)

    raw_path = filepath + ".outro.raw"
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", filepath,
            "-ss", str(analysis_start),
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-acodec", "pcm_s16le",
            raw_path,
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not os.path.exists(raw_path):
        return duration

    try:
        samples = np.fromfile(raw_path, dtype=np.int16).astype(np.float32)
        os.unlink(raw_path)
    except Exception:
        return duration

    if len(samples) < SAMPLE_RATE * 2:
        return duration

    # Compute RMS energy per bar
    bar_samples = int(bar_period * SAMPLE_RATE)
    bar_rms = []
    b = 0
    while True:
        start = b * bar_samples
        end = start + bar_samples
        if end > len(samples):
            break
        chunk = samples[start:end]
        rms = np.sqrt(np.mean(chunk ** 2))
        bar_rms.append(rms)
        b += 1

    if len(bar_rms) < 8:
        return duration

    rms = np.array(bar_rms)
    rms_norm = rms / (np.max(rms) + 1e-10)

    # Compute a smoothed energy curve (4-bar moving average)
    smooth_window = 4
    smoothed = np.convolve(rms_norm, np.ones(smooth_window) / smooth_window, mode="valid")

    # Main section energy = median of the top 50%
    sorted_s = np.sort(smoothed)
    main_level = np.median(sorted_s[len(sorted_s) // 2:])

    if main_level <= 0:
        return duration

    # Scan backwards: find where smoothed energy drops below 50% of main level
    threshold = main_level * 0.50
    last_full_idx = len(smoothed) - 1
    for b in range(len(smoothed) - 1, -1, -1):
        if smoothed[b] >= threshold:
            last_full_idx = b
            break

    # Convert smoothed index back to bar index (offset by smooth_window/2)
    last_full_bar = last_full_idx + smooth_window // 2

    # If the last full bar is near the end, there's no obvious fade-out.
    # Still snap to an 8-bar phrase boundary before the end for clean looping.
    if last_full_bar >= len(bar_rms) - 3:
        # No fade-out detected — snap to last 8-bar boundary
        total_bars_from_start = round(duration / bar_period)
        phrase_len = 8
        safe_bar = (total_bars_from_start // phrase_len) * phrase_len
        outro_time = safe_bar * bar_period
        if outro_time < duration * 0.7:
            return duration  # too aggressive, just use full duration
        return min(outro_time, duration)

    # The "real end" is the end of the last full-energy bar
    outro_time = analysis_start + (last_full_bar + 1) * bar_period

    # Snap to 4-bar phrase boundary
    phrase_len = 4
    outro_bar = round(outro_time / bar_period)
    outro_bar = (outro_bar // phrase_len) * phrase_len
    outro_time = outro_bar * bar_period

    return min(max(outro_time, duration * 0.5), duration)


def detect_hot_cue(filepath: str, bpm: float, beat_offset: float) -> float:
    """Detect the first 'hot cue' — where the main groove starts after the intro.

    Uses FFT-based low-frequency energy analysis (< 200Hz) per bar to find
    the first bar where the kick/bass reaches the main section's energy level.
    Returns the time position (seconds) of the hot cue, snapped to a bar boundary.
    """
    import numpy as np

    bar_period = 4 * 60.0 / bpm
    duration = get_duration_seconds(filepath)
    analysis_dur = min(duration * 0.7, 120.0)

    # Export as raw mono PCM
    raw_path = filepath + ".hotcue.raw"
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", filepath,
            "-t", str(analysis_dur),
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-acodec", "pcm_s16le",
            raw_path,
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not os.path.exists(raw_path):
        return beat_offset

    try:
        samples = np.fromfile(raw_path, dtype=np.int16).astype(np.float32)
        os.unlink(raw_path)
    except Exception:
        return beat_offset

    if len(samples) < SAMPLE_RATE * 2:
        return beat_offset

    # Compute low-frequency energy per beat using FFT
    # Analyze from the start of the track (not from beat_offset) to catch intros
    beat_period = 60.0 / bpm
    beat_samples = int(beat_period * SAMPLE_RATE)

    # Frequency bin cutoff for "bass" (< 200Hz)
    freq_cutoff = 200
    bass_bin_max = max(1, int(freq_cutoff * beat_samples / SAMPLE_RATE))

    beat_bass_energies = []
    b = 0
    while True:
        start = b * beat_samples
        end = start + beat_samples
        if end > len(samples):
            break
        chunk = samples[start:end]
        spectrum = np.abs(np.fft.rfft(chunk))
        bass_energy = np.sum(spectrum[:bass_bin_max] ** 2)
        beat_bass_energies.append(bass_energy)
        b += 1

    if len(beat_bass_energies) < 16:
        return beat_offset

    bass = np.array(beat_bass_energies)
    bass_norm = bass / (np.max(bass) + 1e-10)

    # Group into bars (4 beats per bar)
    n_bars = len(bass_norm) // 4
    bar_bass = np.array([np.mean(bass_norm[i*4:(i+1)*4]) for i in range(n_bars)])

    if len(bar_bass) < 4:
        return beat_offset

    # Main section = top 40% of bars by bass energy
    sorted_bass = np.sort(bar_bass)
    main_level = np.median(sorted_bass[int(len(sorted_bass) * 0.6):])

    if main_level <= 0:
        return beat_offset

    # Strategy 1: Find the biggest energy jump between consecutive bars
    jumps = []
    for b in range(1, min(len(bar_bass), 25)):
        jump = bar_bass[b] - bar_bass[b - 1]
        jumps.append((jump, b))
    jumps.sort(reverse=True)

    # Strategy 2: Find first bar consistently above threshold
    threshold = main_level * 0.35
    first_strong_bar = 0
    for b in range(len(bar_bass)):
        if bar_bass[b] >= threshold:
            first_strong_bar = b
            break

    # Use the biggest jump if significant and preceded by quiet bars
    hot_cue_bar = 0
    if jumps and jumps[0][0] > 0.25:
        jump_bar = jumps[0][1]
        avg_before = np.mean(bar_bass[:jump_bar]) if jump_bar > 0 else 0
        if avg_before < main_level * 0.3 and jump_bar >= 2:
            hot_cue_bar = jump_bar

    # Fallback: use first_strong_bar if after bar 1
    if hot_cue_bar == 0 and first_strong_bar >= 2:
        hot_cue_bar = first_strong_bar

    if hot_cue_bar == 0:
        return beat_offset

    # Snap to the nearest 4-bar phrase boundary (musical phrasing).
    # DJs set hot cues at phrase starts (multiples of 4 or 8 bars).
    # Round UP to the next 4-bar boundary after the detected bar.
    phrase_len = 4
    hot_cue_bar = ((hot_cue_bar + phrase_len - 1) // phrase_len) * phrase_len

    hot_cue_time = hot_cue_bar * bar_period

    # If the hot cue is close to or before beat_offset, use beat_offset
    if hot_cue_time <= beat_offset + bar_period * 0.5:
        return beat_offset

    # Don't return a hot cue that's too far in (more than 40% of the track)
    if hot_cue_time > duration * 0.4:
        return beat_offset

    return hot_cue_time


def detect_first_beat(filepath: str, bpm: float) -> float:
    """Detect the position of the first strong beat (kick) in a track.

    Exports the first few seconds as raw PCM, computes an energy envelope,
    and finds the first onset that aligns with the beat grid.
    Returns the time offset (seconds) of the first downbeat.
    """
    import numpy as np

    beat_period = 60.0 / bpm
    analysis_dur = min(10.0, beat_period * 16)  # analyze first 16 beats

    # Export as raw mono PCM s16le
    raw_path = filepath + ".raw"
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", filepath,
            "-t", str(analysis_dur),
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-acodec", "pcm_s16le",
            raw_path,
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not os.path.exists(raw_path):
        return 0.0

    try:
        samples = np.fromfile(raw_path, dtype=np.int16).astype(np.float32)
        os.unlink(raw_path)
    except Exception:
        return 0.0

    if len(samples) < SAMPLE_RATE:
        return 0.0

    # Compute energy envelope using a window of ~10ms
    window = int(SAMPLE_RATE * 0.01)
    energy = np.array([
        np.sum(samples[i:i + window] ** 2)
        for i in range(0, len(samples) - window, window)
    ])
    if len(energy) == 0:
        return 0.0

    # Normalize
    energy = energy / (np.max(energy) + 1e-10)

    # Compute onset strength (positive derivative of energy)
    onset = np.diff(energy)
    onset = np.maximum(onset, 0)

    # Find peaks above threshold
    threshold = np.max(onset) * 0.3
    peak_indices = []
    for i in range(1, len(onset) - 1):
        if onset[i] > threshold and onset[i] > onset[i - 1] and onset[i] >= onset[i + 1]:
            peak_indices.append(i)

    if not peak_indices:
        return 0.0

    # Convert peak indices to time
    peak_times = [idx * window / SAMPLE_RATE for idx in peak_indices]

    # The first strong peak is likely the first beat
    first_beat = peak_times[0]

    # Validate: check that subsequent peaks align with the beat grid
    # Try small offsets around first_beat to find best grid alignment
    best_offset = first_beat
    best_score = 0
    for candidate in peak_times[:8]:  # try first 8 peaks
        score = 0
        for pt in peak_times:
            # Distance to nearest beat from this candidate
            dist = abs(((pt - candidate) % beat_period) - beat_period / 2)
            if dist < beat_period * 0.15:  # within 15% of a beat
                score += 1
        if score > best_score:
            best_score = score
            best_offset = candidate

    return best_offset


def snap_to_bar(time_pos: float, beat_offset: float, bpm: float, direction: str = "nearest") -> float:
    """Snap a time position to the nearest bar boundary (every 4 beats).

    direction: 'nearest', 'before', or 'after'
    """
    bar_period = 4 * 60.0 / bpm
    # How many bars from the beat offset
    bars_from_offset = (time_pos - beat_offset) / bar_period
    if direction == "before":
        bars_from_offset = math.floor(bars_from_offset)
    elif direction == "after":
        bars_from_offset = math.ceil(bars_from_offset)
    else:
        bars_from_offset = round(bars_from_offset)
    return beat_offset + bars_from_offset * bar_period


def prepare_body_segment(src: str, dst: str, trim_start: float, trim_end: float) -> bool:
    """Extract the main body of a track (between transition zones)."""
    duration = get_duration_seconds(src)
    end_time = max(0, duration - trim_end)
    if end_time <= trim_start:
        subprocess.run(["cp", src, dst], check=True)
        return True

    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", src,
            "-af", f"atrim=start={trim_start}:end={end_time},asetpts=PTS-STARTPTS",
            "-ar", "44100", "-ac", "2",
            dst,
        ],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and os.path.exists(dst)


def _loop_segment(src: str, dst: str, start: float, seg_dur: float, loop_count: int) -> bool:
    """Extract a segment and loop it loop_count times."""
    # First extract the segment
    seg_tmp = dst + ".seg.wav"
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", src,
            "-af", f"atrim=start={start}:end={start + seg_dur},asetpts=PTS-STARTPTS",
            "-ar", "44100", "-ac", "2",
            seg_tmp,
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not os.path.exists(seg_tmp):
        return False

    # Create concat list with the segment repeated loop_count times
    list_path = dst + ".loop.txt"
    with open(list_path, "w") as f:
        for _ in range(loop_count):
            f.write(f"file '{os.path.abspath(seg_tmp)}'\n")
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", dst],
        capture_output=True, text=True,
    )
    try:
        os.unlink(list_path)
        os.unlink(seg_tmp)
    except Exception:
        pass
    return r.returncode == 0 and os.path.exists(dst)


def build_transition_segment(
    outgoing_src: str, incoming_src: str, dst: str,
    transition_dur: float, work_dir: str, label: str,
    out_beat_offset: float = 0.0, in_beat_offset: float = 0.0,
    out_bpm: float = 120.0, in_bpm: float = 120.0,
    in_hot_cue: float = 0.0,
    loop_bars: int = 0,
    out_end: float = 0.0,
) -> bool:
    """Build a DJ-style transition with beat-aligned kicks.

    If loop_bars > 0: extract loop_bars bars from each track, loop them 4x,
    and perform the transition over the looped audio.
    out_end: the "real end" of the outgoing track (before fade-out). 0 = use full duration.
    """
    steps = NUM_TRANSITION_STEPS
    out_duration = get_duration_seconds(outgoing_src)
    in_duration = get_duration_seconds(incoming_src)

    bar_period = 4 * 60.0 / out_bpm

    # Use out_end as the effective end of the outgoing track (skip fade-out)
    effective_out_end = out_end if out_end > 0 else out_duration

    trans_dir = os.path.join(work_dir, f"trans_{label}")
    os.makedirs(trans_dir, exist_ok=True)

    # If looping: extract loop_bars bars, loop 2x, use looped files as sources
    if loop_bars > 0:
        loop_dur = loop_bars * bar_period
        loop_count = 2

        # Outgoing: last loop_bars bars BEFORE the fade-out
        # Snap to the outgoing track's bar grid
        out_loop_start = snap_to_bar(effective_out_end - loop_dur, out_beat_offset, out_bpm, direction="nearest")
        out_loop_start = max(0, out_loop_start)
        out_looped = os.path.join(trans_dir, "out_looped.wav")
        if not _loop_segment(outgoing_src, out_looped, out_loop_start, loop_dur, loop_count):
            return False

        # Incoming: first loop_bars bars from hot cue
        # Snap to the incoming track's bar grid
        in_loop_start = snap_to_bar(in_hot_cue, in_beat_offset, in_bpm, direction="nearest")
        if in_loop_start < 0:
            in_loop_start = 0
        in_looped = os.path.join(trans_dir, "in_looped.wav")
        if not _loop_segment(incoming_src, in_looped, in_loop_start, loop_dur, loop_count):
            return False

        # Now use the looped files as sources
        outgoing_src = out_looped
        incoming_src = in_looped
        out_duration = get_duration_seconds(outgoing_src)
        in_duration = get_duration_seconds(incoming_src)
        effective_out_end = out_duration
        out_beat_offset = 0.0
        in_beat_offset = 0.0
        in_hot_cue = 0.0
        transition_dur = min(out_duration, in_duration)

    # Snap transition duration to whole bars
    transition_bars_count = max(4, round(transition_dur / bar_period))
    transition_dur = transition_bars_count * bar_period

    # Snap outgoing tail start to a bar boundary (use effective end, not absolute end)
    raw_out_start = effective_out_end - transition_dur
    out_start = snap_to_bar(raw_out_start, out_beat_offset, out_bpm, direction="before")
    out_start = max(0, out_start)
    actual_out_dur = effective_out_end - out_start

    # Incoming: start at the hot cue
    in_start = in_hot_cue
    in_start = snap_to_bar(in_start, in_beat_offset, in_bpm, direction="nearest")
    if in_start < 0:
        in_start = 0

    # Align: both segments must be the same length for overlay
    actual_in_dur = min(transition_dur, in_duration - in_start)
    overlay_dur = min(actual_out_dur, actual_in_dur)

    # Re-snap overlay_dur to whole bars
    overlay_bars = max(4, round(overlay_dur / bar_period))
    overlay_dur = overlay_bars * bar_period
    out_start = max(0, effective_out_end - overlay_dur)

    # Use 1 step per bar so every cut lands on a downbeat
    steps = max(4, overlay_bars)
    step_dur = bar_period  # exactly 1 bar per step

    # High-pass frequencies for outgoing (progressively removing bass)
    hp_freqs = [int(20 + (2000 - 20) * (i / (steps - 1)) ** 1.5) for i in range(steps)]
    # Low-pass frequencies for incoming (progressively opening filter)
    lp_freqs = [int(300 + (20000 - 300) * (i / (steps - 1)) ** 0.7) for i in range(steps)]
    # Volume curves — ensure outgoing reaches exactly 0 at the end
    out_vols = [max(0.0, 1.0 - (i / (steps - 1)) ** 1.2) for i in range(steps)]
    out_vols[-1] = 0.0
    in_vols = [min(1.0, (i / (steps - 1)) ** 1.2) for i in range(steps)]
    in_vols[-1] = 1.0

    trans_dir = os.path.join(work_dir, f"trans_{label}")
    os.makedirs(trans_dir, exist_ok=True)

    out_subs = []
    in_subs = []

    for s in range(steps):
        t_out = out_start + s * step_dur
        t_in = in_start + s * step_dur

        # Outgoing sub-segment
        out_sub = os.path.join(trans_dir, f"out_{s:02d}.wav")
        out_filter = (
            f"atrim=start={t_out}:end={t_out + step_dur},"
            f"asetpts=PTS-STARTPTS,"
            f"highpass=f={hp_freqs[s]}:poles=2,"
            f"volume={out_vols[s]:.3f}"
        )
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", outgoing_src, "-af", out_filter, "-ar", "44100", "-ac", "2", out_sub],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and os.path.exists(out_sub):
            out_subs.append(out_sub)

        # Incoming sub-segment
        in_sub = os.path.join(trans_dir, f"in_{s:02d}.wav")
        in_filter = (
            f"atrim=start={t_in}:end={t_in + step_dur},"
            f"asetpts=PTS-STARTPTS,"
            f"lowpass=f={lp_freqs[s]}:poles=2,"
            f"volume={in_vols[s]:.3f}"
        )
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", incoming_src, "-af", in_filter, "-ar", "44100", "-ac", "2", in_sub],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and os.path.exists(in_sub):
            in_subs.append(in_sub)

    if len(out_subs) != steps or len(in_subs) != steps:
        return False

    # Concatenate sub-segments
    out_concat = os.path.join(trans_dir, "out_full.wav")
    _concat_files(out_subs, out_concat)
    in_concat = os.path.join(trans_dir, "in_full.wav")
    _concat_files(in_subs, in_concat)

    # Overlay both — beats are aligned because both start on bar boundaries
    # and both are at the same BPM
    r = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", out_concat, "-i", in_concat,
            "-filter_complex", "[0][1]amix=inputs=2:duration=longest:normalize=0",
            "-ar", "44100", "-ac", "2",
            dst,
        ],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and os.path.exists(dst)


def _concat_files(files: list[str], dst: str) -> bool:
    """Concatenate WAV files using ffmpeg concat demuxer."""
    list_path = dst + ".list.txt"
    with open(list_path, "w") as f:
        for fp in files:
            f.write(f"file '{os.path.abspath(fp)}'\n")
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", dst],
        capture_output=True, text=True,
    )
    try:
        os.unlink(list_path)
    except Exception:
        pass
    return r.returncode == 0 and os.path.exists(dst)


def concatenate_segments(segments: list[str], dst: str) -> bool:
    """Concatenate audio segments sequentially."""
    if len(segments) == 1:
        subprocess.run(["cp", segments[0], dst], check=True)
        return True

    # Create a concat list file
    list_path = dst + ".list.txt"
    with open(list_path, "w") as f:
        for seg in segments:
            f.write(f"file '{os.path.abspath(seg)}'\n")

    r = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-codec:a", "libmp3lame", "-q:a", "2",
            dst,
        ],
        capture_output=True, text=True,
    )
    os.unlink(list_path)
    return r.returncode == 0 and os.path.exists(dst)


def generate_dj_mix(track_infos: list[dict], output_path: str, work_dir: str, transition_bars: int = TRANSITION_BARS, loop_bars: int = 0):
    """Generate a DJ-style mix with beat matching, EQ transitions, and filter sweeps.

    track_infos: list of dicts with keys: path, bpm, name
    """
    n = len(track_infos)
    if n < 2:
        if n == 1:
            subprocess.run(["cp", track_infos[0]["path"], output_path], check=True)
        return

    # Step 1: Normalize loudness
    print("\n🔊 Normalisation du volume...")
    norm_dir = os.path.join(work_dir, "normalized")
    os.makedirs(norm_dir, exist_ok=True)
    for i, info in enumerate(track_infos):
        norm_path = os.path.join(norm_dir, f"{i:02d}.wav")
        if normalize_track(info["path"], norm_path):
            info["norm_path"] = norm_path
            print(f"  ✅ {info['name']}")
        else:
            print(f"  ❌ Échec normalisation : {info['name']}")
            return

    # Step 2: Compute global target BPM (median of all track BPMs)
    all_bpms = [info["bpm"] for info in track_infos]
    target_bpm = float(sorted(all_bpms)[len(all_bpms) // 2])  # median
    print(f"\n🎵 BPM target global : {target_bpm:.1f} BPM (médiane de {', '.join(f'{b:.0f}' for b in all_bpms)})")

    # Step 3: Time-stretch ALL tracks to the target BPM
    print("\n🎚️  Time-stretch de toutes les tracks...")
    stretch_dir = os.path.join(work_dir, "stretched")
    os.makedirs(stretch_dir, exist_ok=True)
    for i, info in enumerate(track_infos):
        bpm = info["bpm"]
        stretched_path = os.path.join(stretch_dir, f"{i:02d}.wav")
        if time_stretch_track(info["norm_path"], stretched_path, bpm, target_bpm):
            info["stretched_path"] = stretched_path
            if abs(target_bpm - bpm) > 0.5:
                print(f"  🎚️  {info['name']}: {bpm:.1f} → {target_bpm:.1f} BPM")
            else:
                print(f"  ✅ {info['name']}: {bpm:.1f} BPM (pas de stretch)")
        else:
            print(f"  Échec stretch : {info['name']}")
            info["stretched_path"] = info["norm_path"]

    # Step 4: Detect beat grids + hot cues on stretched tracks (with cache)
    os.makedirs("output", exist_ok=True)
    cache_path = os.path.join("output", "analysis_cache.json")
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    print("\n🥁 Détection des beats, hot cues et outros...")
    bar_period = 4 * 60.0 / target_bpm
    for i, info in enumerate(track_infos):
        src = info["stretched_path"]
        duration = get_duration_seconds(src)
        cache_key = f"{info['name']}_{target_bpm:.1f}"

        if cache_key in cache:
            info["beat_offset"] = cache[cache_key]["beat_offset"]
            info["hot_cue"] = cache[cache_key]["hot_cue"]
            info["outro"] = cache[cache_key]["outro"]
            cached = True
        else:
            beat_offset = detect_first_beat(src, target_bpm)
            info["beat_offset"] = beat_offset
            hot_cue = detect_hot_cue(src, target_bpm, beat_offset)
            info["hot_cue"] = hot_cue
            outro = detect_outro(src, target_bpm, beat_offset)
            info["outro"] = outro
            cache[cache_key] = {
                "beat_offset": beat_offset,
                "hot_cue": hot_cue,
                "outro": outro,
            }
            cached = False

        hot_cue = info["hot_cue"]
        outro = info["outro"]
        intro_bars = round(hot_cue / bar_period)
        outro_bars = round((duration - outro) / bar_period) if outro < duration - 1 else 0
        parts = []
        if intro_bars >= 2:
            parts.append(f"intro {intro_bars} bars")
        if outro_bars >= 2:
            parts.append(f"outro {outro_bars} bars")
        detail = f" ({', '.join(parts)})" if parts else " (pas d'intro/outro)"
        tag = " (cache)" if cached else ""
        print(f"  🎯 {info['name']}: hot cue {hot_cue:.1f}s, fin {outro:.1f}s / {duration:.1f}s{detail}{tag}")

    # Save cache
    try:
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass

    # Step 5: Build the mix segment by segment
    # All tracks are now at the same BPM — body + transitions use stretched versions
    loop_info = f" (loop {loop_bars} bars x2)" if loop_bars > 0 else ""
    print(f"\n Construction du mix DJ @ {target_bpm:.1f} BPM{loop_info}...")
    seg_dir = os.path.join(work_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)

    trans_dur = bars_to_seconds(transition_bars, target_bpm)
    final_segments = []

    for i in range(n):
        info = track_infos[i]
        src = info["stretched_path"]
        duration = get_duration_seconds(src)

        # Determine trim amounts for the body
        # For incoming tracks: the transition plays from hot_cue for trans_dur,
        # so the body must start AFTER that zone to avoid replaying it.
        hot_cue = info["hot_cue"]
        outro = info["outro"]
        if i > 0:
            trim_start = hot_cue + trans_dur
        else:
            trim_start = 0
        # Trim end: cut at the outro (before fade-out) + leave room for transition/loop
        fade_tail = duration - outro  # how much fade-out to cut
        if i < n - 1:
            if loop_bars > 0:
                # With loop: body ends where the loop segment starts
                loop_dur = loop_bars * bar_period
                trim_end = fade_tail + loop_dur
            else:
                trim_end = fade_tail + trans_dur
        else:
            trim_end = fade_tail

        # Safety: ensure track is long enough
        if trim_start + trim_end >= duration:
            safe_dur = duration / 3
            trim_start = safe_dur if i > 0 else 0
            trim_end = safe_dur if i < n - 1 else 0

        # Extract body
        body_path = os.path.join(seg_dir, f"{i:02d}_body.wav")
        prepare_body_segment(src, body_path, trim_start, trim_end)

        # Build transition with previous track
        if i > 0:
            prev_info = track_infos[i - 1]
            prev_src = prev_info["stretched_path"]

            t_dur = trans_dur
            prev_outro = prev_info["outro"]
            prev_duration = get_duration_seconds(prev_src)
            if t_dur > prev_outro / 3:
                t_dur = prev_outro / 3
            if t_dur > duration / 3:
                t_dur = duration / 3

            mixed_seg = os.path.join(seg_dir, f"trans_{i-1}_{i}_mix.wav")

            print(f"  🔀 Transition {i}: {prev_info['name']} → {info['name']} ({t_dur:.1f}s, {transition_bars} bars)")

            ok = build_transition_segment(
                prev_src, src, mixed_seg,
                t_dur, seg_dir, f"{i-1}_{i}",
                out_beat_offset=prev_info["beat_offset"],
                in_beat_offset=info["beat_offset"],
                out_bpm=target_bpm,
                in_bpm=target_bpm,
                in_hot_cue=info["hot_cue"],
                loop_bars=loop_bars,
                out_end=prev_outro,
            )
            if ok:
                final_segments.append(mixed_seg)
            else:
                print(f"    ⚠️  Transition échouée, skip")

        final_segments.append(body_path)

    # Step 6: Concatenate all segments
    print(f"\n📼 Assemblage de {len(final_segments)} segments...")
    concatenate_segments(final_segments, output_path)


def main():
    parser = argparse.ArgumentParser(
        description="Génère un mix DJ à partir des premiers tracks d'une playlist Airtable."
    )
    parser.add_argument("playlist_id", help="ID de la playlist (record ID ou champ 'ID')")
    parser.add_argument("-n", "--count", type=int, default=5, help="Nombre de tracks à mixer (défaut: 5)")
    parser.add_argument("-b", "--bars", type=int, default=TRANSITION_BARS, help=f"Durée de transition en bars (défaut: {TRANSITION_BARS})")
    parser.add_argument("-l", "--loop", type=int, default=0, metavar="BARS", help="Loop N bars 2x pour les transitions (ex: --loop 4)")
    parser.add_argument("-o", "--output", help="Chemin du fichier de sortie (défaut: output/<nom>.mp3)")

    args = parser.parse_args()

    client = AirtableClient()

    print(f"🔍 Recherche de la playlist '{args.playlist_id}'...")
    playlist, tracks = fetch_playlist_tracks(client, args.playlist_id, limit=args.count)

    if not playlist:
        print(f"❌ Playlist introuvable : {args.playlist_id}")
        sys.exit(1)

    fields = playlist.get("fields", {})
    name = fields.get("Name", "playlist")
    print(f"✅ Playlist : {name}")
    print(f"📋 {len(tracks)} tracks à mixer :\n")

    # Collect track info with BPM
    track_infos = []
    for i, t in enumerate(tracks, 1):
        tf = t.get("fields", {})
        bpm_list = tf.get("Tempo (Spotify)", [])
        bpm = bpm_list[0] if isinstance(bpm_list, list) and bpm_list else DEFAULT_BPM
        bpm = float(bpm)
        track_name = tf.get("Name", "?")
        print(f"  {i}. {track_name} — {bpm:.0f} BPM")
        track_infos.append({"track": t, "bpm": bpm, "name": track_name})

    # Download tracks
    print(f"\n📥 Téléchargement des fichiers audio...")
    download_dir = tempfile.mkdtemp(prefix="djmix_")
    valid_infos = []
    for i, info in enumerate(track_infos, 1):
        path = download_track(info["track"], download_dir, i)
        if path:
            info["path"] = path
            valid_infos.append(info)

    if len(valid_infos) < 2:
        print(f"\n❌ Pas assez de fichiers audio ({len(valid_infos)}). Minimum 2 requis.")
        sys.exit(1)

    print(f"\n✅ {len(valid_infos)}/{len(track_infos)} fichiers téléchargés")

    # Generate mix
    if args.output:
        output_path = args.output
    else:
        os.makedirs("output", exist_ok=True)
        safe_name = name.replace("/", "-").replace("\\", "-").replace(":", "-")
        # Unique filename with timestamp
        from datetime import datetime
        ts = datetime.now().strftime("%H%M%S")
        output_path = os.path.join("output", f"DJ MIX - {safe_name} - {ts}.mp3")

    generate_dj_mix(valid_infos, output_path, download_dir, transition_bars=args.bars, loop_bars=args.loop)

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        dur = get_duration_seconds(output_path)
        dur_min = int(dur // 60)
        dur_sec = int(dur % 60)
        print(f"\n✅ DJ Mix généré : {output_path}")
        print(f"   📊 {size_mb:.1f} MB — {dur_min}:{dur_sec:02d}")
    else:
        print(f"\n❌ Erreur : le fichier de sortie n'a pas été créé")
        sys.exit(1)


if __name__ == "__main__":
    main()
