"""Focused unit tests for the karaoke QA core."""
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
import torch
import torchaudio

from src.qa.diagnosis import diagnose, status_from_gates_and_diagnosis
from src.qa.gates import evaluate_gates
from src.qa.models import (
    DiagnosisType,
    FinalStatus,
    LineTimingMetrics,
    LyricLine,
    LyricWord,
    StructuredLyrics,
    SyncMetrics,
    SyncStatus,
    TimelineTransform,
    to_json,
)
from src.qa.normalization import LyricsNormalizer, TokenMapper
from src.qa.persistence import save_sync_report
from src.qa.scoring import compute_sync_metrics
from src.qa.timeline import build_timeline_transform


def _make_line(
    text,
    source_start,
    source_end=None,
    predicted_start=None,
    predicted_end=None,
    words=None,
):
    return LyricLine(
        id="L",
        text=text,
        source_start_ms=source_start,
        source_end_ms=source_end,
        words=words or [],
        predicted_start_ms=predicted_start,
        predicted_end_ms=predicted_end,
    )


def test_timeline_transform_composition():
    t = TimelineTransform(
        lyrics_to_source_offset_ms=300.0,
        source_to_karaoke_audio_offset_ms=0.0,
        karaoke_audio_to_video_offset_ms=120.0,
    )
    assert t.musixmatch_to_source(1000.0) == 1300.0
    assert t.source_to_musixmatch(1300.0) == 1000.0
    assert t.source_to_final_video(1000.0) == 1120.0
    assert t.final_video_to_source(1120.0) == 1000.0


def test_build_timeline_transform_from_record():
    record = {
        "fields": {
            "Lyrics to singing offset (s)": 0.25,
            "Audio to music video time offset (s)": -0.12,
        }
    }
    transform = build_timeline_transform(record)
    assert transform.lyrics_to_source_offset_ms == 250.0
    assert transform.karaoke_audio_to_video_offset_ms == -120.0


def test_lyrics_normalizer_preserves_mapping():
    n = LyricsNormalizer()
    text = "  She LOVES you, (Chorus) yeah!  "
    norm = n.normalize_text(text)
    assert norm == "she loves you yeah"
    # Ensure parenthesized marker is removed.
    assert "chorus" not in norm


def test_token_mapper_repeated_words():
    mapper = TokenMapper()
    source = ["she", "loves", "you", "yeah", "yeah", "yeah"]
    predicted = ["she", "loves", "you", "yeah", "yeah", "yeah"]
    mapping = mapper.map_predicted_to_source(source, predicted)
    assert mapping == [0, 1, 2, 3, 4, 5]


def test_token_mapper_missing_predicted_word():
    mapper = TokenMapper()
    source = ["she", "loves", "you"]
    predicted = ["she", "you"]
    mapping = mapper.map_predicted_to_source(source, predicted)
    # "you" in predicted matches source index 2, skipping "loves".
    assert mapping == [0, 2]


def test_compute_sync_metrics_perfect_alignment():
    lyrics = StructuredLyrics(
        source="test",
        source_track_id=None,
        language="en",
        lines=[
            _make_line("Line one", 1000.0, 2000.0, 1000.0, 2000.0),
            _make_line("Line two", 3000.0, 4000.0, 3000.0, 4000.0),
        ],
    )
    transform = TimelineTransform()
    metrics = compute_sync_metrics(lyrics, transform)
    assert metrics.line_start.median_error_ms == 0.0
    assert metrics.line_start.max_error_ms == 0.0
    assert metrics.line_alignment_coverage == 1.0
    assert metrics.drift_slope == pytest.approx(1.0, abs=1e-6)
    assert metrics.drift_intercept_ms == pytest.approx(0.0, abs=1e-6)


def test_compute_sync_metrics_global_offset():
    lyrics = StructuredLyrics(
        source="test",
        source_track_id=None,
        language="en",
        lines=[
            _make_line("Line one", 1000.0, 2000.0, 1500.0, 2500.0),
            _make_line("Line two", 3000.0, 4000.0, 3500.0, 4500.0),
        ],
    )
    transform = TimelineTransform()
    metrics = compute_sync_metrics(lyrics, transform)
    assert metrics.line_start.median_error_ms == 500.0
    assert metrics.drift_slope == pytest.approx(1.0, abs=1e-6)
    assert metrics.drift_intercept_ms == pytest.approx(500.0, abs=1e-6)


def test_compute_sync_metrics_unresolved_region():
    lyrics = StructuredLyrics(
        source="test",
        source_track_id=None,
        language="en",
        lines=[
            _make_line("Line one", 0.0, 1000.0, 0.0, 1000.0),
            _make_line("Line two", 1000.0, 2000.0, None, None),
            _make_line("Line three", 2000.0, 3000.0, None, None),
            _make_line("Line four", 5000.0, 6000.0, 5000.0, 6000.0),
        ],
    )
    transform = TimelineTransform()
    metrics = compute_sync_metrics(lyrics, transform)
    # Largest unresolved block is lines two and three, from 1000 to 3000 = 2000ms.
    assert metrics.largest_unresolved_lyric_region_ms == 2000.0
    assert metrics.line_alignment_coverage == 0.5


def test_gates_pass_for_perfect_sync():
    metrics = SyncMetrics(
        line_start=LineTimingMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 10),
        line_end=None,
        word=__import__("src.qa.models", fromlist=["WordTimingMetrics"]).WordTimingMetrics(
            coverage=1.0, aligned_count=10, total_count=10, median_error_ms=0.0, p90_error_ms=0.0, max_error_ms=0.0
        ),
        total_expected_lines=10,
        line_alignment_coverage=1.0,
        largest_unresolved_lyric_region_ms=0.0,
        drift_slope=1.0,
        drift_intercept_ms=0.0,
        drift_residual_std_ms=0.0,
        duration_mismatch_ms=0.0,
        source_duration_ms=60_000.0,
        predicted_duration_ms=60_000.0,
    )
    gates = evaluate_gates(metrics)
    assert all(g["pass"] for g in gates.values())


def test_gates_fail_large_median():
    metrics = SyncMetrics(
        line_start=LineTimingMetrics(500.0, 800.0, 1200.0, 500.0, 200.0, 10),
        line_end=None,
        word=__import__("src.qa.models", fromlist=["WordTimingMetrics"]).WordTimingMetrics(
            coverage=1.0, aligned_count=10, total_count=10, median_error_ms=None, p90_error_ms=None, max_error_ms=None
        ),
        total_expected_lines=10,
        line_alignment_coverage=1.0,
        largest_unresolved_lyric_region_ms=0.0,
        drift_slope=1.0,
        drift_intercept_ms=500.0,
        drift_residual_std_ms=50.0,
        duration_mismatch_ms=0.0,
        source_duration_ms=60_000.0,
        predicted_duration_ms=60_000.0,
    )
    gates = evaluate_gates(metrics)
    assert not gates["line_start_median"]["pass"]


def test_diagnose_global_offset():
    lyrics = StructuredLyrics(
        source="test",
        source_track_id=None,
        language="en",
        lines=[
            _make_line("Line one", 1000.0, 2000.0, 1500.0, 2000.0),
            _make_line("Line two", 3000.0, 4000.0, 3500.0, 4000.0),
            _make_line("Line three", 5000.0, 6000.0, 5500.0, 6000.0),
        ],
    )
    transform = TimelineTransform()
    metrics = compute_sync_metrics(lyrics, transform)
    diag = diagnose(metrics, lyrics)
    assert diag.type == DiagnosisType.GLOBAL_OFFSET.value
    assert diag.estimated_global_offset_ms == pytest.approx(500.0, abs=1e-6)


def test_status_from_gates_all_pass():
    gates = {
        "line_start_median": {"pass": True},
        "line_start_p90": {"pass": True},
        "line_start_max": {"pass": True},
        "line_coverage": {"pass": True},
        "unresolved_lyric_region": {"pass": True},
        "drift_slope": {"pass": True},
    }
    from src.qa.models import SyncDiagnosis, Confidence
    diag = SyncDiagnosis(
        type=DiagnosisType.GOOD, confidence=Confidence.HIGH, description="good"
    )
    status = status_from_gates_and_diagnosis(gates, diag)
    assert status == SyncStatus.SYNC_VERIFIED


def test_structural_analyzer_matches_global_offset():
    from src.qa.structural import StructuralAnalyzer

    # Create a synthetic lyrics structure where the first line starts at 1000 ms
    # and subsequent lines are 1000 ms apart.
    lines = [
        _make_line(f"line {i}", 1000.0 + i * 1000.0, 1900.0 + i * 1000.0)
        for i in range(5)
    ]
    lyrics = StructuredLyrics(
        source="test", source_track_id=None, language="en", lines=lines
    )

    # Build a synthetic audio file with clicks exactly at the lyric start times.
    sr = 22050
    duration_s = 7.0
    y = np.zeros(int(sr * duration_s), dtype=np.float32)
    for i in range(5):
        start = int((1.0 + i) * sr)
        y[start : start + int(sr * 0.05)] = 1.0

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name
    import torchaudio

    torchaudio.save(audio_path, torch.tensor(y).unsqueeze(0), sr)

    try:
        analyzer = StructuralAnalyzer()
        result = analyzer.align(audio_path, lyrics)
        metrics = compute_sync_metrics(result.lyrics, TimelineTransform())
        assert metrics.line_alignment_coverage == 1.0
        assert metrics.line_start.median_error_ms < 100.0
        assert metrics.line_start.max_error_ms < 200.0
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


def test_to_json_serializes_enums_and_dataclasses():
    lyric = _make_line("Hello", 0.0, 1000.0)
    data = to_json(lyric)
    assert data["text"] == "Hello"
    assert data["source_start_ms"] == 0.0


def test_save_sync_report_roundtrip():
    from src.qa.models import AlignmentResult, SyncDiagnosis, SyncReport, Confidence

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["QA_OUTPUT_DIR"] = tmp
        from src.config import settings

        settings.qa_output_dir = tmp
        lyrics = StructuredLyrics(
            source="test",
            source_track_id="123",
            language="en",
            lines=[_make_line("Hello", 0.0, 1000.0, 0.0, 1000.0)],
        )
        report = SyncReport(
            record_id="rec123",
            track_name="Test",
            artist_name="Artist",
            musixmatch_track_id="123",
            transform=TimelineTransform(),
            metrics=compute_sync_metrics(lyrics, TimelineTransform()),
            diagnosis=SyncDiagnosis(
                type=DiagnosisType.GOOD,
                confidence=Confidence.HIGH,
                description="good",
            ),
            status=SyncStatus.SYNC_VERIFIED,
            lyrics=lyrics,
            alignment_result=AlignmentResult(
                aligner_name="stable-ts",
                model_name="base",
                settings={},
                lyrics=lyrics,
            ),
        )
        path = save_sync_report(report)
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["record_id"] == "rec123"
        assert loaded["status"] == "SYNC_VERIFIED"
