"""
Pure Python Shazam fingerprinting and recognition.
Based on SongRec by marin-m (https://github.com/marin-m/SongRec).
No dependency on shazamio-core (which hangs on short audio clips).
"""

import struct
import subprocess
import uuid
from base64 import b64decode, b64encode
from binascii import crc32
from copy import copy
from ctypes import LittleEndianStructure, c_uint32
from enum import IntEnum
from io import BytesIO
from math import log, sqrt, exp
from random import choice, random, seed
from time import time
from typing import Any, Dict, List, Optional

import numpy as np
import requests as http_requests

# ─── Signature Format ────────────────────────────────────────────────

DATA_URI_PREFIX = "data:audio/vnd.shazam.sig;base64,"


class SampleRate(IntEnum):
    _8000 = 1
    _11025 = 2
    _16000 = 3
    _32000 = 4
    _44100 = 5
    _48000 = 6


class FrequencyBand(IntEnum):
    _0_250 = -1
    _250_520 = 0
    _520_1450 = 1
    _1450_3500 = 2
    _3500_5500 = 3


class RawSignatureHeader(LittleEndianStructure):
    _pack_ = True
    _fields_ = [
        ("magic1", c_uint32),
        ("crc32", c_uint32),
        ("size_minus_header", c_uint32),
        ("magic2", c_uint32),
        ("void1", c_uint32 * 3),
        ("shifted_sample_rate_id", c_uint32),
        ("void2", c_uint32 * 2),
        ("number_samples_plus_divided_sample_rate", c_uint32),
        ("fixed_value", c_uint32),
    ]


class FrequencyPeak:
    def __init__(
        self,
        fft_pass_number: int,
        peak_magnitude: int,
        corrected_peak_frequency_bin: int,
        sample_rate_hz: int,
    ):
        self.fft_pass_number = fft_pass_number
        self.peak_magnitude = peak_magnitude
        self.corrected_peak_frequency_bin = corrected_peak_frequency_bin
        self.sample_rate_hz = sample_rate_hz

    def get_frequency_hz(self) -> float:
        return self.corrected_peak_frequency_bin * (self.sample_rate_hz / 2 / 1024 / 64)

    def get_seconds(self) -> float:
        return (self.fft_pass_number * 128) / self.sample_rate_hz


class DecodedMessage:
    sample_rate_hz: int = None
    number_samples: int = None
    frequency_band_to_sound_peaks: Dict[FrequencyBand, List[FrequencyPeak]] = None

    def encode_to_binary(self) -> bytes:
        header = RawSignatureHeader()
        header.magic1 = 0xCAFE2580
        header.magic2 = 0x94119C00
        header.shifted_sample_rate_id = (
            int(getattr(SampleRate, "_%s" % self.sample_rate_hz)) << 27
        )
        header.fixed_value = (15 << 19) + 0x40000
        header.number_samples_plus_divided_sample_rate = int(
            self.number_samples + self.sample_rate_hz * 0.24
        )

        contents_buf = BytesIO()

        for frequency_band, frequency_peaks in sorted(
            self.frequency_band_to_sound_peaks.items()
        ):
            peaks_buf = BytesIO()
            fft_pass_number = 0

            for frequency_peak in frequency_peaks:
                assert frequency_peak.fft_pass_number >= fft_pass_number

                if frequency_peak.fft_pass_number - fft_pass_number >= 255:
                    peaks_buf.write(b"\xff")
                    peaks_buf.write(
                        (frequency_peak.fft_pass_number).to_bytes(4, "little")
                    )
                    fft_pass_number = frequency_peak.fft_pass_number

                peaks_buf.write(
                    bytes([frequency_peak.fft_pass_number - fft_pass_number])
                )
                peaks_buf.write(
                    (frequency_peak.peak_magnitude).to_bytes(2, "little")
                )
                peaks_buf.write(
                    (frequency_peak.corrected_peak_frequency_bin).to_bytes(2, "little")
                )

                fft_pass_number = frequency_peak.fft_pass_number

            contents_buf.write((0x60030040 + int(frequency_band)).to_bytes(4, "little"))
            contents_buf.write(len(peaks_buf.getvalue()).to_bytes(4, "little"))
            contents_buf.write(peaks_buf.getvalue())
            contents_buf.write(b"\x00" * (-len(peaks_buf.getvalue()) % 4))

        header.size_minus_header = len(contents_buf.getvalue()) + 8

        buf = BytesIO()
        buf.write(bytes(header))
        buf.write((0x40000000).to_bytes(4, "little"))
        buf.write((len(contents_buf.getvalue()) + 8).to_bytes(4, "little"))
        buf.write(contents_buf.getvalue())

        buf.seek(8)
        header.crc32 = crc32(buf.read()) & 0xFFFFFFFF
        buf.seek(0)
        buf.write(bytes(header))

        return buf.getvalue()

    def encode_to_uri(self) -> str:
        return DATA_URI_PREFIX + b64encode(self.encode_to_binary()).decode("ascii")


# ─── Fingerprint Algorithm ───────────────────────────────────────────

HANNING_MATRIX = np.hanning(2050)[1:-1]


class RingBuffer(list):
    def __init__(self, buffer_size: int, default_value: Any = None):
        if default_value is not None:
            list.__init__(
                self, [copy(default_value) for _ in range(buffer_size)]
            )
        else:
            list.__init__(self, [None] * buffer_size)
        self.position: int = 0
        self.buffer_size: int = buffer_size
        self.num_written: int = 0

    def append(self, value: Any):
        self[self.position] = value
        self.position += 1
        self.position %= self.buffer_size
        self.num_written += 1


class SignatureGenerator:
    def __init__(self):
        self.input_pending_processing: List[int] = []
        self.samples_processed: int = 0

        self.ring_buffer_of_samples: RingBuffer = RingBuffer(
            buffer_size=2048, default_value=0
        )
        self.fft_outputs: RingBuffer = RingBuffer(
            buffer_size=256, default_value=[0.0 * 1025]
        )
        self.spread_ffts_output: RingBuffer = RingBuffer(
            buffer_size=256, default_value=[0] * 1025
        )

        self.MAX_TIME_SECONDS = 3.1
        self.MAX_PEAKS = 255

        self.next_signature = DecodedMessage()
        self.next_signature.sample_rate_hz = 16000
        self.next_signature.number_samples = 0
        self.next_signature.frequency_band_to_sound_peaks = {}

    def feed_input(self, s16le_mono_samples: List[int]):
        self.input_pending_processing += s16le_mono_samples

    def get_next_signature(self) -> Optional[DecodedMessage]:
        if len(self.input_pending_processing) - self.samples_processed < 128:
            return None

        while len(self.input_pending_processing) - self.samples_processed >= 128 and (
            self.next_signature.number_samples / self.next_signature.sample_rate_hz
            < self.MAX_TIME_SECONDS
            or sum(
                len(peaks)
                for peaks in self.next_signature.frequency_band_to_sound_peaks.values()
            )
            < self.MAX_PEAKS
        ):
            self.process_input(
                self.input_pending_processing[
                    self.samples_processed : self.samples_processed + 128
                ]
            )
            self.samples_processed += 128

        returned_signature = self.next_signature

        self.next_signature = DecodedMessage()
        self.next_signature.sample_rate_hz = 16000
        self.next_signature.number_samples = 0
        self.next_signature.frequency_band_to_sound_peaks = {}

        self.ring_buffer_of_samples = RingBuffer(buffer_size=2048, default_value=0)
        self.fft_outputs = RingBuffer(
            buffer_size=256, default_value=[0.0 * 1025]
        )
        self.spread_ffts_output = RingBuffer(
            buffer_size=256, default_value=[0] * 1025
        )

        return returned_signature

    def process_input(self, s16le_mono_samples: List[int]):
        self.next_signature.number_samples += len(s16le_mono_samples)

        for position_of_chunk in range(0, len(s16le_mono_samples), 128):
            self.do_fft(
                s16le_mono_samples[position_of_chunk : position_of_chunk + 128]
            )
            self.do_peak_spreading_and_recognition()

    def do_fft(self, batch_of_128_s16le_mono_samples):
        self.ring_buffer_of_samples[
            self.ring_buffer_of_samples.position : self.ring_buffer_of_samples.position
            + len(batch_of_128_s16le_mono_samples)
        ] = batch_of_128_s16le_mono_samples

        self.ring_buffer_of_samples.position += len(batch_of_128_s16le_mono_samples)
        self.ring_buffer_of_samples.position %= 2048
        self.ring_buffer_of_samples.num_written += len(batch_of_128_s16le_mono_samples)

        excerpt_from_ring_buffer = (
            self.ring_buffer_of_samples[self.ring_buffer_of_samples.position :]
            + self.ring_buffer_of_samples[: self.ring_buffer_of_samples.position]
        )

        fft_results = np.fft.rfft(HANNING_MATRIX * excerpt_from_ring_buffer)
        assert len(fft_results) == 1025 and len(excerpt_from_ring_buffer) == 2048

        fft_results = (fft_results.real**2 + fft_results.imag**2) / (1 << 17)
        fft_results = np.maximum(fft_results, 0.0000000001)

        self.fft_outputs.append(fft_results)

    def do_peak_spreading_and_recognition(self):
        self.do_peak_spreading()
        if self.spread_ffts_output.num_written >= 46:
            self.do_peak_recognition()

    def do_peak_spreading(self):
        origin_last_fft = self.fft_outputs[self.fft_outputs.position - 1]
        spread_last_fft = list(origin_last_fft)

        for position in range(1025):
            if position < 1023:
                spread_last_fft[position] = max(
                    spread_last_fft[position : position + 3]
                )

            max_value = spread_last_fft[position]
            for former_fft_num in [-1, -3, -6]:
                former_fft_output = self.spread_ffts_output[
                    (self.spread_ffts_output.position + former_fft_num)
                    % self.spread_ffts_output.buffer_size
                ]
                former_fft_output[position] = max_value = max(
                    former_fft_output[position], max_value
                )

        self.spread_ffts_output.append(spread_last_fft)

    def do_peak_recognition(self):
        fft_minus_46 = self.fft_outputs[
            (self.fft_outputs.position - 46) % self.fft_outputs.buffer_size
        ]
        fft_minus_49 = self.spread_ffts_output[
            (self.spread_ffts_output.position - 49)
            % self.spread_ffts_output.buffer_size
        ]
        fft_minus_53 = self.spread_ffts_output[
            (self.spread_ffts_output.position - 53)
            % self.spread_ffts_output.buffer_size
        ]
        fft_minus_45 = self.spread_ffts_output[
            (self.spread_ffts_output.position - 45)
            % self.spread_ffts_output.buffer_size
        ]

        for bin_position in range(10, 1015):
            if (
                fft_minus_46[bin_position] >= 1 / 64
                and fft_minus_46[bin_position] >= fft_minus_49[bin_position - 1]
            ):
                max_neighbor_in_fft_minus_49 = 0
                for neighbor_offset in [
                    *range(-10, -3, 3),
                    -3,
                    1,
                    *range(2, 9, 3),
                ]:
                    max_neighbor_in_fft_minus_49 = max(
                        fft_minus_49[bin_position + neighbor_offset],
                        max_neighbor_in_fft_minus_49,
                    )

                if fft_minus_46[bin_position] > max_neighbor_in_fft_minus_49:
                    max_neighbor_in_other_adjacent_ffts = max_neighbor_in_fft_minus_49

                    for other_offset in [
                        -53,
                        -45,
                        *range(165, 201, 7),
                        *range(214, 250, 7),
                    ]:
                        max_neighbor_in_other_adjacent_ffts = max(
                            self.spread_ffts_output[
                                (self.spread_ffts_output.position + other_offset)
                                % self.spread_ffts_output.buffer_size
                            ][bin_position - 1],
                            max_neighbor_in_other_adjacent_ffts,
                        )

                    if (
                        fft_minus_46[bin_position]
                        > max_neighbor_in_other_adjacent_ffts
                    ):
                        fft_number = self.spread_ffts_output.num_written - 46

                        peak_magnitude = (
                            log(max(1 / 64, fft_minus_46[bin_position])) * 1477.3
                            + 6144
                        )
                        peak_magnitude_before = (
                            log(max(1 / 64, fft_minus_46[bin_position - 1])) * 1477.3
                            + 6144
                        )
                        peak_magnitude_after = (
                            log(max(1 / 64, fft_minus_46[bin_position + 1])) * 1477.3
                            + 6144
                        )

                        peak_variation_1 = (
                            peak_magnitude * 2
                            - peak_magnitude_before
                            - peak_magnitude_after
                        )
                        peak_variation_2 = (
                            (peak_magnitude_after - peak_magnitude_before)
                            * 32
                            / peak_variation_1
                        )

                        corrected_peak_frequency_bin = (
                            bin_position * 64 + peak_variation_2
                        )

                        assert peak_variation_1 > 0

                        frequency_hz = corrected_peak_frequency_bin * (
                            16000 / 2 / 1024 / 64
                        )

                        if frequency_hz < 250:
                            continue
                        elif frequency_hz < 520:
                            band = FrequencyBand._250_520
                        elif frequency_hz < 1450:
                            band = FrequencyBand._520_1450
                        elif frequency_hz < 3500:
                            band = FrequencyBand._1450_3500
                        elif frequency_hz <= 5500:
                            band = FrequencyBand._3500_5500
                        else:
                            continue

                        if (
                            band
                            not in self.next_signature.frequency_band_to_sound_peaks
                        ):
                            self.next_signature.frequency_band_to_sound_peaks[band] = []

                        self.next_signature.frequency_band_to_sound_peaks[
                            band
                        ].append(
                            FrequencyPeak(
                                fft_number,
                                int(peak_magnitude),
                                int(corrected_peak_frequency_bin),
                                16000,
                            )
                        )


# ─── Communication with Shazam ───────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def recognize_from_signature(signature: DecodedMessage) -> dict:
    """Send a fingerprint signature to Shazam's servers and return the result."""
    first_uuid = str(uuid.uuid4()).upper()
    second_uuid = str(uuid.uuid4())

    fuzz = random() * 15.3 - 7.65

    response = http_requests.post(
        f"https://amp.shazam.com/discovery/v5/fr/FR/android/-/tag/{first_uuid}/{second_uuid}",
        params={
            "sync": "true",
            "webv3": "true",
            "sampling": "true",
            "connected": "",
            "shazamapiversion": "v3",
            "sharehub": "true",
            "video": "v3",
        },
        headers={
            "Content-Type": "application/json",
            "User-Agent": choice(USER_AGENTS),
            "Content-Language": "fr_FR",
        },
        json={
            "geolocation": {
                "altitude": random() * 400 + 100 + fuzz,
                "latitude": random() * 180 - 90 + fuzz,
                "longitude": random() * 360 - 180 + fuzz,
            },
            "signature": {
                "samplems": int(
                    signature.number_samples / signature.sample_rate_hz * 1000
                ),
                "timestamp": int(time() * 1000),
                "uri": signature.encode_to_uri(),
            },
            "timestamp": int(time() * 1000),
            "timezone": "Europe/Paris",
        },
        timeout=10,
    )

    try:
        data = response.json()
    except Exception:
        retry_after = response.headers.get("Retry-After")
        return {
            "_error": "non_json",
            "_http_status": response.status_code,
            "_retry_after": retry_after,
            "_content_type": response.headers.get("Content-Type"),
            "_text": (response.text or "")[:500],
        }

    if isinstance(data, dict):
        data.setdefault("_http_status", response.status_code)

    if response.status_code != 200:
        if isinstance(data, dict):
            data.setdefault("_error", "http_error")
            data.setdefault("_retry_after", response.headers.get("Retry-After"))
            data.setdefault("_content_type", response.headers.get("Content-Type"))
            return data
        return {
            "_error": "http_error",
            "_http_status": response.status_code,
            "_retry_after": response.headers.get("Retry-After"),
            "_content_type": response.headers.get("Content-Type"),
            "_data": data,
        }

    return data


def audio_to_raw_samples(audio_path: str, start_s: float = 0, duration_s: float = 12) -> List[int]:
    """
    Extract raw 16-bit signed mono 16kHz PCM samples from an audio file
    using ffmpeg.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_s),
        "-t", str(duration_s),
        "-i", audio_path,
        "-ar", "16000",
        "-ac", "1",
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        return []

    raw_bytes = proc.stdout
    # Unpack as signed 16-bit little-endian integers
    num_samples = len(raw_bytes) // 2
    samples = list(struct.unpack("<%dh" % num_samples, raw_bytes[:num_samples * 2]))
    return samples


def recognize_segment(
    audio_path: str,
    start_s: float = 0,
    duration_s: float = 12,
) -> Optional[dict]:
    """
    Recognize a segment of audio from a file.
    Returns the Shazam API response dict, or None on failure.
    """
    samples = audio_to_raw_samples(audio_path, start_s, duration_s)
    if not samples:
        return {
            "_error": "no_samples",
        }

    generator = SignatureGenerator()
    generator.feed_input(samples)
    generator.MAX_TIME_SECONDS = duration_s

    signature = generator.get_next_signature()
    if signature is None:
        return {
            "_error": "no_signature",
        }

    try:
        result = recognize_from_signature(signature)
        return result
    except Exception as e:
        return {
            "_error": "exception",
            "_message": str(e),
        }
