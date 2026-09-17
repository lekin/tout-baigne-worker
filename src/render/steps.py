"""Shared media helpers for karaoke rendering.

Used by both the local CLI (`src/cli.py`) and the remote RunPod render
worker (`src/render/executor.py`) so both paths run identical steps.
"""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote

import requests


def get_audio_duration(audio_path: str) -> Optional[int]:
    """Get duration of an audio file in seconds using ffprobe."""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        data = json.loads(result.stdout)
        return int(float(data['format']['duration']))
    except Exception as e:
        print(f"Warning: Could not get audio duration: {e}")
        return None


def get_media_duration_seconds(media_path: str) -> Optional[float]:
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            media_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except Exception as e:
        print(f"Warning: Could not get media duration: {e}")
        return None


def _download_to_file(url: str, dst_path: str, timeout_seconds: int = 60) -> None:
    if url.startswith("file://"):
        shutil.copy(url[7:], dst_path)
        return
    with requests.get(url, stream=True, allow_redirects=True, timeout=timeout_seconds) as r:
        r.raise_for_status()
        with open(dst_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)


def normalize_google_drive_download_url(url: str) -> str:
    try:
        if not url:
            return url
        if 'drive.google.com' not in url:
            return url
        if 'uc?export=download' in url and 'id=' in url:
            return url
        if '/file/d/' in url:
            file_id = url.split('/file/d/')[1].split('/')[0]
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        if 'id=' in url:
            file_id = url.split('id=')[1].split('&')[0]
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        return url
    except Exception:
        return url


# Backwards-compatible private alias (cli.py and friends used the underscored name).
_normalize_google_drive_download_url = normalize_google_drive_download_url


def _extract_google_drive_file_id(url: str) -> Optional[str]:
    try:
        normalized = normalize_google_drive_download_url(str(url))
        if '/file/d/' in normalized:
            return normalized.split('/file/d/')[1].split('/')[0]
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(normalized)
        ids = parse_qs(parsed.query).get('id') or []
        if ids and str(ids[0]).strip():
            return str(ids[0]).strip()
    except Exception:
        pass
    return None


def _gdrive_cache_dir() -> str:
    cache_dir = (os.getenv('GDRIVE_CACHE_DIR') or '').strip()
    if not cache_dir:
        cache_dir = os.path.join('output', 'gdrive_cache')
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _gdrive_cache_path(url: str) -> str:
    normalized = normalize_google_drive_download_url(str(url))
    file_id = _extract_google_drive_file_id(normalized)
    cache_key = f"id_{file_id}" if file_id else hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    return os.path.join(_gdrive_cache_dir(), f"{cache_key}.bin")


def _restore_google_drive_cache(url: str, dst_path: str) -> bool:
    try:
        cache_path = _gdrive_cache_path(url)
        if not os.path.exists(cache_path):
            return False
        if os.path.getsize(cache_path) <= 1000 or _looks_like_html(cache_path):
            return False
        if os.path.abspath(cache_path) != os.path.abspath(dst_path):
            shutil.copy2(cache_path, dst_path)
        return os.path.exists(dst_path) and os.path.getsize(dst_path) > 1000 and not _looks_like_html(dst_path)
    except Exception:
        return False


def _save_google_drive_cache(url: str, src_path: str) -> None:
    try:
        if not os.path.exists(src_path):
            return
        if os.path.getsize(src_path) <= 1000 or _looks_like_html(src_path):
            return
        cache_path = _gdrive_cache_path(url)
        tmp_cache_path = f"{cache_path}.tmp"
        shutil.copy2(src_path, tmp_cache_path)
        os.replace(tmp_cache_path, cache_path)
    except Exception:
        return


def download_google_drive_file(url: str, dst_path: str, timeout_seconds: int = 60) -> bool:
    try:
        url = normalize_google_drive_download_url(str(url))
        if _restore_google_drive_cache(url, dst_path):
            return True
        session = requests.Session()
        with session.get(url, stream=True, allow_redirects=True, timeout=timeout_seconds) as r:
            r.raise_for_status()

            content_type = (r.headers.get('content-type') or '').lower()
            if 'text/html' not in content_type:
                with open(dst_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
                _save_google_drive_cache(url, dst_path)
                return True

            html = r.text

            # Some warning pages require submitting a download form (GET or POST).
            # We try to parse the first form and replay it.
            form_action: Optional[str] = None
            form_method = 'get'
            form_payload: dict[str, str] = {}
            form_match = re.search(r'(<form\b[^>]*>.*?</form>)', html, flags=re.IGNORECASE | re.DOTALL)
            if form_match:
                form_block = form_match.group(1)
                m_action = re.search(r"action\s*=\s*(['\"])(.*?)\1", form_block, flags=re.IGNORECASE)
                if m_action:
                    form_action = m_action.group(2)
                m_method = re.search(r"method\s*=\s*(['\"])?(get|post)\1?", form_block, flags=re.IGNORECASE)
                if m_method:
                    form_method = (m_method.group(2) or 'get').lower()

                # Extract inputs as name/value pairs (value optional)
                for input_tag in re.findall(r"<input\b[^>]*>", form_block, flags=re.IGNORECASE):
                    m_name = re.search(r"name\s*=\s*(['\"])([^'\"]+)\1", input_tag, flags=re.IGNORECASE)
                    if not m_name:
                        continue
                    name = m_name.group(2)
                    m_value = re.search(r"value\s*=\s*(['\"])([^'\"]*)\1", input_tag, flags=re.IGNORECASE)
                    value = m_value.group(2) if m_value else ''
                    form_payload[name] = value

            if form_action:
                action_url = form_action
                action_url = action_url.replace('\\u003d', '=').replace('\\u0026', '&')
                action_url = action_url.replace('&amp;', '&')
                action_url = unquote(action_url)
                if action_url.startswith('/'):
                    action_url = 'https://drive.google.com' + action_url

                try:
                    req = session.post if form_method == 'post' else session.get
                    with req(action_url, data=form_payload if form_method == 'post' else None, params=form_payload if form_method != 'post' else None, stream=True, allow_redirects=True, timeout=timeout_seconds) as rp:
                        rp.raise_for_status()
                        ctp = (rp.headers.get('content-type') or '').lower()
                        if 'text/html' not in ctp:
                            with open(dst_path, 'wb') as f:
                                for chunk in rp.iter_content(chunk_size=1024 * 256):
                                    if chunk:
                                        f.write(chunk)
                            _save_google_drive_cache(url, dst_path)
                            return True
                except Exception:
                    pass

            # Some large files show a "Virus scan warning" HTML page containing a download link/form.
            # Try to extract a direct /uc?export=download... URL from the HTML.
            extracted_url: Optional[str] = None
            patterns = [
                r'href=["\'](\/uc\?export=download[^"\']+)["\']',
                r'action=["\'](\/uc\?export=download[^"\']+)["\']',
                r'"downloadUrl"\s*:\s*"([^"]+)"',
                r"'downloadUrl'\s*:\s*'([^']+)'",
                r'href=["\'](https?:\/\/[^"\']+export=download[^"\']+)["\']',
                r'href=["\'](https?:\/\/driveusercontent\.google\.com\/download[^"\']+)["\']',
                r'href=["\'](https?:\/\/drive\.usercontent\.google\.com\/download[^"\']+)["\']',
                r'href=["\'](https?:\/\/drive\.usercontent\.google\.com\/download\?[^"\']+)["\']',
                r'href=["\'](https?:\/\/drive\.usercontent\.google\.com\/uc\?[^"\']+)["\']',
                r'href=["\'](https?:\/\/drive\.usercontent\.googleusercontent\.com\/download[^"\']+)["\']',
                r'href=["\'](https?:\/\/drive\.usercontent\.google\.com\/download\?[^"\']+)["\']',
                r'href=["\'](https?:\/\/drive\.usercontent\.google\.com\/download[^"\']+)["\']',
            ]
            for pat in patterns:
                m = re.search(pat, html)
                if not m:
                    continue
                cand = m.group(1)
                if not cand:
                    continue
                cand = cand.replace('\\u003d', '=').replace('\\u0026', '&')
                cand = cand.replace('&amp;', '&')
                cand = unquote(cand)
                if cand.startswith('http://') or cand.startswith('https://'):
                    extracted_url = cand
                elif cand.startswith('/'):
                    extracted_url = 'https://drive.google.com' + cand
                else:
                    extracted_url = 'https://drive.google.com/' + cand
                break

            confirm_token = None
            for k, v in session.cookies.items():
                if k.startswith('download_warning'):
                    confirm_token = v
                    break
            if not confirm_token:
                m = re.search(r'confirm=([0-9A-Za-z_\-]+)', html)
                if m:
                    confirm_token = m.group(1)

            file_id = None
            if 'id=' in url:
                file_id = url.split('id=')[1].split('&')[0]

            if not file_id:
                return False

            confirm_url = extracted_url or f"https://drive.google.com/uc?export=download&id={file_id}"
            if 'confirm=' not in confirm_url:
                if confirm_token:
                    join = '&' if ('?' in confirm_url) else '?'
                    confirm_url += f"{join}confirm={confirm_token}"
                else:
                    join = '&' if ('?' in confirm_url) else '?'
                    confirm_url += f"{join}confirm=t"

            with session.get(confirm_url, stream=True, allow_redirects=True, timeout=timeout_seconds) as r2:
                r2.raise_for_status()
                ct2 = (r2.headers.get('content-type') or '').lower()
                if 'text/html' in ct2:
                    return False
                with open(dst_path, 'wb') as f:
                    for chunk in r2.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
            _save_google_drive_cache(url, dst_path)
            return True
    except Exception:
        return False


def _looks_like_html(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            head = f.read(2048).lstrip()
        return head.lower().startswith(b'<!doctype html') or head.lower().startswith(b'<html')
    except Exception:
        return False


def _extract_html_hint(path: str) -> Optional[str]:
    try:
        if not _looks_like_html(path):
            return None

        with open(path, 'rb') as f:
            raw = f.read(8192)
        text = raw.decode('utf-8', errors='ignore')
        m = re.search(r'<title>(.*?)</title>', text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r'\s+', ' ', m.group(1)).strip()
            if title:
                return title[:140]

        lower = text.lower()
        for key in (
            'access denied',
            'sign in',
            'too many users',
            'quota exceeded',
            'cannot be downloaded',
            'sorry',
        ):
            if key in lower:
                return key

        snippet = re.sub(r'\s+', ' ', text)
        snippet = snippet.strip()
        return snippet[:140] if snippet else None
    except Exception:
        return None


def download_asset(url: str, dst_path: str, timeout_seconds: int = 120) -> bool:
    """Download an asset URL (Google Drive aware) and validate it is not HTML."""
    try:
        if not url:
            return False
        if 'drive.google.com' in str(url):
            ok = download_google_drive_file(url, dst_path, timeout_seconds=timeout_seconds)
            if not ok:
                _download_to_file(url, dst_path, timeout_seconds=timeout_seconds)
        else:
            _download_to_file(url, dst_path, timeout_seconds=timeout_seconds)
        return os.path.exists(dst_path) and os.path.getsize(dst_path) > 1000 and not _looks_like_html(dst_path)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Video encoder selection (platform + availability aware)
# ---------------------------------------------------------------------------

_FFMPEG_ENCODERS_CACHE: Optional[set] = None
_NVENC_NEW_API_CACHE: Optional[bool] = None


def _ffmpeg_encoders() -> set:
    global _FFMPEG_ENCODERS_CACHE
    if _FFMPEG_ENCODERS_CACHE is None:
        try:
            result = subprocess.run(
                ['ffmpeg', '-hide_banner', '-encoders'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            )
            _FFMPEG_ENCODERS_CACHE = set(re.findall(r'\b(h264_\w+|libx264|hevc_\w+|libx265)\b', result.stdout))
        except Exception:
            _FFMPEG_ENCODERS_CACHE = set()
    return _FFMPEG_ENCODERS_CACHE


def ffmpeg_has_encoder(name: str) -> bool:
    return name in _ffmpeg_encoders()


def _nvenc_supports_p_presets() -> bool:
    """Detect whether ffmpeg's h264_nvenc exposes the p1-p7 preset API."""
    global _NVENC_NEW_API_CACHE
    if _NVENC_NEW_API_CACHE is None:
        try:
            result = subprocess.run(
                ['ffmpeg', '-hide_banner', '-h', 'encoder=h264_nvenc'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            )
            _NVENC_NEW_API_CACHE = 'p7' in (result.stdout + result.stderr)
        except Exception:
            _NVENC_NEW_API_CACHE = False
    return _NVENC_NEW_API_CACHE


_NVENC_USABLE_CACHE: Optional[bool] = None


def nvenc_usable() -> bool:
    """Run a real 1s h264_nvenc encode once — the encoder can be listed while
    the runtime still rejects sessions (e.g. RunPod serverless workers report
    'unsupported device'). Result is cached per process."""
    global _NVENC_USABLE_CACHE
    if _NVENC_USABLE_CACHE is None:
        try:
            r = subprocess.run(
                ['ffmpeg', '-hide_banner', '-f', 'lavfi', '-i',
                 'testsrc2=s=320x240:d=1:r=24', '-c:v', 'h264_nvenc', '-f', 'null', '-'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
            )
            _NVENC_USABLE_CACHE = r.returncode == 0
        except Exception:
            _NVENC_USABLE_CACHE = False
        if not _NVENC_USABLE_CACHE:
            print("h264_nvenc listed but encode test failed — falling back to libx264")
    return _NVENC_USABLE_CACHE


def _nvenc_args(fast: bool) -> List[str]:
    """Quality-oriented h264_nvenc args, old- or new-preset API."""
    if _nvenc_supports_p_presets():
        if fast:
            return ['-c:v', 'h264_nvenc', '-preset', 'p4', '-tune', 'hq', '-rc', 'vbr', '-cq', '28', '-b:v', '0', '-maxrate', '12000k', '-bufsize', '24000k']
        return ['-c:v', 'h264_nvenc', '-preset', 'p7', '-tune', 'hq', '-rc', 'vbr', '-cq', '23', '-b:v', '0', '-maxrate', '15000k', '-bufsize', '30000k']
    if fast:
        return ['-c:v', 'h264_nvenc', '-preset', 'fast', '-rc', 'vbr_hq', '-cq', '28', '-b:v', '0']
    return ['-c:v', 'h264_nvenc', '-preset', 'slow', '-rc', 'vbr_hq', '-cq', '23', '-b:v', '0']


def _videotoolbox_args() -> List[str]:
    return ['-c:v', 'h264_videotoolbox', '-b:v', '4000k']


def _x264_args(fast: bool) -> List[str]:
    if fast:
        return ['-c:v', 'libx264', '-crf', '33', '-preset', 'ultrafast']
    return ['-c:v', 'libx264', '-crf', '23', '-preset', 'medium']


def resolve_video_encoder(encoder: str = 'auto', fast: bool = False) -> Tuple[List[str], List[str], str]:
    """Resolve (codec_args, fps_args, encoder_name) for the render pipeline.

    - 'x264'        -> libx264 (fast: ultrafast/crf33, normal: medium/crf23)
    - 'nvenc'       -> h264_nvenc (falls back to libx264 if unavailable)
    - 'videotoolbox'-> h264_videotoolbox (falls back to libx264 if unavailable)
    - 'auto'        -> fast mode: videotoolbox (macOS) or nvenc or x264-fast;
                       normal mode: nvenc if available else x264 (matches
                       historical local behaviour where libx264 was used).
    """
    enc = (encoder or 'auto').lower()
    is_darwin = platform.system() == 'Darwin'

    if enc == 'x264':
        return _x264_args(fast), [], 'libx264'
    if enc == 'nvenc':
        if ffmpeg_has_encoder('h264_nvenc') and nvenc_usable():
            return _nvenc_args(fast), (['-r', '24'] if fast else []), 'h264_nvenc'
        print("h264_nvenc not usable, falling back to libx264")
        return _x264_args(fast), [], 'libx264'
    if enc == 'videotoolbox':
        if ffmpeg_has_encoder('h264_videotoolbox'):
            return _videotoolbox_args(), ['-r', '24'], 'h264_videotoolbox'
        print("h264_videotoolbox not available, falling back to libx264")
        return _x264_args(fast), [], 'libx264'

    # auto
    if fast:
        if is_darwin and ffmpeg_has_encoder('h264_videotoolbox'):
            return _videotoolbox_args(), ['-r', '24'], 'h264_videotoolbox'
        if ffmpeg_has_encoder('h264_nvenc') and nvenc_usable():
            return _nvenc_args(fast), ['-r', '24'], 'h264_nvenc'
        return _x264_args(True), [], 'libx264'
    if ffmpeg_has_encoder('h264_nvenc') and nvenc_usable():
        return _nvenc_args(False), [], 'h264_nvenc'
    return _x264_args(False), [], 'libx264'


# ---------------------------------------------------------------------------
# Post-render steps (shared by cli.py and the remote render executor)
# ---------------------------------------------------------------------------

def mux_replace_audio(
    video_in: str,
    audio_in: str,
    out_path: str,
    offset_s: float = 0.0,
    fast: bool = False,
    encoder: str = 'auto',
) -> None:
    """Replace a rendered video's audio track with the original audio file.

    Mirrors the cli.py behaviour: stream-copy video when no offset is needed,
    otherwise re-encode with the resolved encoder.
    """
    audio_duration = get_audio_duration(audio_in)
    cmd = ['ffmpeg', '-y']
    if offset_s:
        cmd.extend(['-itsoffset', str(offset_s)])
    cmd.extend(['-i', video_in, '-i', audio_in])
    cmd.extend(['-map', '0:v:0', '-map', '1:a:0'])

    if not offset_s:
        cmd.extend([
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', ('192k' if fast else '320k'),
            '-movflags', '+faststart',
        ])
    else:
        codec_args, fps_args, enc_name = resolve_video_encoder(encoder, fast=fast)
        if enc_name == 'h264_videotoolbox':
            # Preserve the historical mux re-encode settings used by the CLI.
            codec_args, fps_args = ['-c:v', 'h264_videotoolbox', '-b:v', '6500k'], ['-r', '30']
        cmd.extend(fps_args + codec_args)
        cmd.extend(['-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', ('192k' if fast else '320k'), '-movflags', '+faststart'])

    if audio_duration is not None:
        cmd.extend(['-t', str(audio_duration)])
    else:
        cmd.extend(['-shortest'])
    cmd.append(out_path)
    subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)


def apply_intro_overlay(
    video_in: str,
    out_path: str,
    intro_path: str,
    width: int,
    height: int,
    fast: bool = False,
    encoder: str = 'auto',
    overlay_seconds: float = 4.0,
) -> bool:
    """Overlay the GKDA intro clip on the first N seconds of the video."""
    intro_duration = get_media_duration_seconds(str(intro_path))
    if not intro_duration or intro_duration <= 0:
        print("Warning: could not read intro duration, skipping intro")
        return False
    overlay_duration = min(float(intro_duration), float(overlay_seconds))

    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    filter_complex = (
        f"[0:v]{scale_pad},trim=0:{overlay_duration:.6f},setpts=PTS-STARTPTS[v0];"
        f"[1:v]{scale_pad}[v1];"
        f"[v1][v0]overlay=0:0:enable='between(t,0,{overlay_duration:.6f})'[v]"
    )

    codec_args, _fps_args, _name = resolve_video_encoder(encoder, fast=fast)
    cmd = ['ffmpeg', '-y', '-i', str(intro_path), '-i', video_in]
    cmd.extend([
        '-filter_complex', filter_complex,
        '-map', '[v]',
        '-map', '1:a:0',
        *codec_args,
        '-pix_fmt', 'yuv420p',
        '-c:a', 'copy',
        '-movflags', '+faststart',
        out_path,
    ])
    subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
    return True


def synth_color_background(
    color: str,
    out_path: str,
    width: int,
    height: int,
    duration_s: float,
    fast: bool = False,
) -> None:
    """Create a solid color background video via ffmpeg lavfi."""
    cmd = [
        'ffmpeg', '-y',
        '-f', 'lavfi',
        '-i', f"color=c={color}:s={width}x{height}:r={'24' if fast else '30'}",
        '-t', str(duration_s or 180),
        '-pix_fmt', 'yuv420p',
        out_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)


# Fontname values in generated ASS files historically embed the font *file
# path* (e.g. "legacy/karaoke/fonts/SpaceMono-Regular.ttf"), which libass
# cannot resolve through fontconfig/CoreText — it silently falls back to a
# default font. Rewriting the path to the family name lets libass match the
# font when the filter's fontsdir (or fontconfig) provides the .ttf files.
_ASS_FONTNAME_PATH_RE = re.compile(r"(?m)^(Style:[^,\n]*,)[^,\n]*\.ttf(,)")
_ASS_FONT_FAMILY = "Space Mono"


def normalize_ass_font_path(ass_path: str, family: str = _ASS_FONT_FAMILY) -> bool:
    """Rewrite .ttf-path Fontname entries in an ASS file to a family name.

    Edits the file in place; returns True when at least one style was fixed.
    """
    try:
        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()
        fixed, n = _ASS_FONTNAME_PATH_RE.subn(rf"\g<1>{family}\g<2>", content)
        if n:
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(fixed)
        return n > 0
    except Exception:
        return False


def escape_ffmpeg_filter_path(path: str) -> str:
    """Escape a path for use inside an ffmpeg filtergraph argument."""
    return (
        str(path)
        .replace('\\', '\\\\')
        .replace(':', '\\:')
        .replace("'", "\\'")
        .replace(',', '\\,')
        .replace('[', '\\[')
        .replace(']', '\\]')
    )
