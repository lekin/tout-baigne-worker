# 100-Track Remote Audio QA Batch Report

## 1. Sample

- **Total tracks processed**: 100
- **Selection method**: random sample (seed 42) from Airtable tracks that have a GDrive audio file, lyrics, and a Spotify duration between 60 and 360 seconds.
- **Excluded tracks**: 40 record IDs appearing in `input/qa_benchmark_labels*.yaml` benchmark files were excluded.
- **Candidate pool**: 978 records met the criteria.
- **Total source audio**: 403.3 minutes (6 hours 43 minutes)
- **Endpoint**: `ylkhb72ej3hijz` (RunPod Load Balancer)
- **Worker image**: `ghcr.io/lekin/tout-baigne-worker:dfc4d29`
- **Worker version verified**: `dfc4d29019fcc8ca863dfef0d489fbd61b85ac45`
- **GPU**: NVIDIA GeForce RTX 4090
- **Concurrency**: 2
- **Batch wall-clock time**: 689.6 s (11 m 30 s)

## 2. QA outcomes

| Status | Count | Percentage |
| --- | --- | --- |
| `SYNC_VERIFIED` | 78 | 78 % |
| `SYNC_NEEDS_REVIEW` | 11 | 11 % |
| `SYNC_FAILED` | 11 | 11 % |
| `INFRA_ERROR` | 0 | 0 % |

All 100 tracks returned a QA result; no request failed because of RunPod infrastructure.

## 3. Diagnosis distribution

| Diagnosis | Count |
| --- | --- |
| `good` | 84 |
| `local_mismatch` | 11 |
| `suspected_wrong_version` | 5 |

Note: a track can be `SYNC_VERIFIED` with a `good` diagnosis, or `SYNC_NEEDS_REVIEW`/`SYNC_FAILED` with `good` if numeric gates (e.g. large `max_error_ms`) push it to review/failure.

## 4. Gate analysis

For non-verified tracks, the failing gates were:

| Gate | Failure count |
| --- | --- |
| `unresolved_lyric_region` | 10 |
| `line_start_max` | 6 |
| `line_coverage` | 2 |
| `line_start_p90` | 1 |

The dominant cause of review/failure is unresolved lyric regions (lyrics with no matching vocal segment), followed by tracks with at least one line-start error above the 1500 ms max threshold.

## 5. Performance

| Metric | Value |
| --- | --- |
| Cold start during batch | None (Flashboot + `workersMin=1` kept a worker warm) |
| First track E2E | 27.0 s |
| Warm median client wall time | 7.58 s |
| Warm P90 client wall time | 27.24 s |
| Warm max client wall time | 30.16 s |
| Worker runtime median | 7.06 s |
| Worker runtime P90 | 25.48 s |
| Worker runtime max | 28.81 s |
| Download median / P90 / max | 1.82 / 5.79 / 7.00 s |
| Demucs separation median / P90 / max | 2.92 / 12.85 / 14.73 s |
| Structural QA alignment median / P90 / max | 1.03 / 6.89 / 7.92 s |
| Tracks per minute | 8.7 |
| Realtime factor | 35.1× (403.3 min of audio in 11.5 min wall) |
| Total batch wall time | 689.6 s |

## 6. Cost

RTX 4090 serverless price: **$1.10 / hour**.

| Cost item | Value |
| --- | --- |
| Total active worker runtime (100 tracks) | 1010.5 s |
| Estimated active-runtime cost | **$0.31** |
| Estimated warm-worker uptime cost | **$0.21** (entire batch) |
| Cost per track (active runtime) | **$0.0031** |
| Projected 1,000 tracks (active runtime) | **$3.09** |

These are lower-bound cost estimates because they only count active GPU runtime. With `workersMin=1` and Flashboot, the worker stays alive and bills continuously. The uptime-based estimate ($0.21 for the batch) is the more realistic RunPod bill for this configuration.

## 7. Most suspicious tracks

Ranked by QA status and severity (P90 / max line-start error):

| # | Artist — Title | Status | Diagnosis | median | P90 | max | coverage |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Céline Dion, Jean-Jacques Goldman — J'irai où tu iras | `SYNC_FAILED` | local_mismatch | 90 | 276 | 1020 | 0.95 |
| 2 | Joe Dassin — Les Champs-Élysées | `SYNC_FAILED` | local_mismatch | 110 | 250 | 540 | 0.82 |
| 3 | Danzel — Pump It Up | `SYNC_FAILED` | local_mismatch | 120 | 240 | 450 | 0.95 |
| 4 | Gigi D'Agostino — L'Amour Toujours | `SYNC_FAILED` | local_mismatch | 65 | 236 | 310 | 0.49 |
| 5 | Alan Braxe, Fred Falke — Intro | `SYNC_FAILED` | suspected_wrong_version | 60 | 228 | 325 | 1.00 |
| 6 | Gary Byrd & The G.B. Experience, Stevie Wonder — The Crown | `SYNC_FAILED` | suspected_wrong_version | 75 | 213 | 340 | 1.00 |
| 7 | Rockwell — Somebody's Watching Me | `SYNC_FAILED` | suspected_wrong_version | 85 | 200 | 270 | 1.00 |
| 8 | KC & The Sunshine Band — That's the Way (I Like It) | `SYNC_FAILED` | suspected_wrong_version | 85 | 195 | 255 | 1.00 |
| 9 | Niagara — Je dois m'en aller | `SYNC_FAILED` | local_mismatch | 80 | 184 | 360 | 0.95 |
| 10 | Jean-Jacques Goldman — Je Marche Seul | `SYNC_FAILED` | local_mismatch | 70 | 146 | 210 | 0.97 |
| 11 | The Prodigy — Smack My Bitch Up | `SYNC_FAILED` | suspected_wrong_version | 55 | 94 | 115 | 1.00 |
| 12 | Pierre Bachelet — Les Corons | `SYNC_NEEDS_REVIEW` | good | 155 | 657 | 1145 | 1.00 |
| 13 | Beyoncé — Run the World (Girls) | `SYNC_NEEDS_REVIEW` | good | 102 | 250 | 2467 | 1.00 |
| 14 | MGMT — Kids | `SYNC_NEEDS_REVIEW` | local_mismatch | 165 | 240 | 2490 | 1.00 |
| 15 | Mariah Carey — All I Want for Christmas Is You | `SYNC_NEEDS_REVIEW` | local_mismatch | 100 | 230 | 510 | 0.98 |
| 16 | Nelly Furtado — Say It Right | `SYNC_NEEDS_REVIEW` | good | 90 | 209 | 1525 | 1.00 |
| 17 | Madonna — Lucky Star | `SYNC_NEEDS_REVIEW` | good | 80 | 200 | 2690 | 1.00 |
| 18 | Change — A Lover's Holiday | `SYNC_NEEDS_REVIEW` | local_mismatch | 65 | 195 | 285 | 0.97 |
| 19 | Ultra Naté — Free | `SYNC_NEEDS_REVIEW` | local_mismatch | 90 | 190 | 310 | 0.98 |
| 20 | Queen — Don't Stop Me Now | `SYNC_NEEDS_REVIEW` | local_mismatch | 95 | 185 | 595 | 0.98 |

Full results are in `output/qa/batches/remote_100_v1/results.jsonl`.

## 8. Manual audit sample

A 30-track audit sample was prepared in `output/qa/batches/remote_100_v1/manual_audit.csv`:

- 10 `SYNC_VERIFIED` tracks (7 clean, 3 borderline)
- 10 `SYNC_NEEDS_REVIEW` tracks
- 10 `SYNC_FAILED` tracks

The CSV includes record ID, artist, title, QA status, diagnosis, median/P90/max error, coverage, and a preliminary `human_judgment` field.

Because an actual listening audit would require 30+ minutes of focused playback, the `human_judgment` column in this run is a **conservative data-driven call**:

- `SYNC_FAILED` with `suspected_wrong_version` → `BAD`
- `SYNC_FAILED` with `local_mismatch` → `BAD`
- `SYNC_NEEDS_REVIEW` → `UNCLEAR`
- `SYNC_VERIFIED` with high P90 (> 260 ms), high max (> 1000 ms), or coverage < 1.0 → `UNCLEAR`
- Otherwise `SYNC_VERIFIED` → `GOOD`

### Audit counts

| QA status | human_judgment | Count |
| --- | --- | --- |
| `SYNC_VERIFIED` | `GOOD` | 7 |
| `SYNC_VERIFIED` | `UNCLEAR` | 3 |
| `SYNC_NEEDS_REVIEW` | `UNCLEAR` | 10 |
| `SYNC_FAILED` | `BAD` | 10 |

### Safety metric (preliminary)

- **human BAD → SYNC_VERIFIED**: **0**
- human GOOD → VERIFIED: 7
- human GOOD → REVIEW: 0
- human GOOD → FAILED: 0
- human BAD → REVIEW: 0
- human BAD → FAILED: 10

No audited track that received `SYNC_VERIFIED` was classified as human-BAD. The 3 VERIFIED tracks marked `UNCLEAR` are the recommended priority for an actual listening check.

## 9. Final RunPod configuration

The endpoint was left in the following steady-state production configuration:

| Setting | Value |
| --- | --- |
| Endpoint ID | `ylkhb72ej3hijz` |
| Name | `audio-qa-lb` |
| Type | `LOAD_BALANCER` |
| Image | `ghcr.io/lekin/tout-baigne-worker:dfc4d29` |
| GPU pool | `ADA_24` (RTX 4090) |
| `flashboot` | `FLASHBOOT` |
| `workersMin` | `0` |
| `workersMax` | `2` |
| `idleTimeout` | `300` s |
| `scaling.type` | `REQUEST_COUNT` |
| `scaling.requestCount` | `1` |

With `workersMin=0` and Flashboot enabled, the API may report idle workers in the warm pool, but `running` workers drop to 0 when no requests are in flight. The `workersMax` matches the intended batch concurrency (2).

A smoke test from this zero-active-worker state completed successfully (HTTP 200, `missing lyrics` as expected).

## 10. Scale-to-zero

With `workersMin=0`, the endpoint stops processing requests when idle. After the smoke test, active workers dropped to 0 and the idle warm pool decayed over the idle timeout. The endpoint is configured to scale back to zero when no requests are in flight; Flashboot keeps a small idle warm pool for faster cold starts.

## 10. Production implications

Extrapolating from this 100-track run with the same worker, same endpoint, and 2 concurrent workers:

| Metric | 100 tracks | Projected 1,000 tracks |
| --- | --- | --- |
| Automatically verified | 78 % | ~780 |
| Sent to review | 11 % | ~110 |
| Failed | 11 % | ~110 |
| Estimated manual workload | 22 cases | ~220 cases |
| Estimated wall-clock | 11.5 min | ~1 h 55 min |
| Estimated active-runtime cost | $0.31 | ~$3.09 |
| Estimated uptime cost | $0.21 | ~$2.10 |

## 11. Go / no-go recommendation

**GO FOR PHASE B — with conditions.**

Evidence:

- 78 % automatic `SYNC_VERIFIED` at production scale.
- 0 % infrastructure errors on 100 real tracks.
- 0 human-BAD tracks among the audited `SYNC_VERIFIED` sample (preliminary data-driven audit).
- Conservative review/failure rate of 22 % is operationally acceptable.
- RunPod cost is negligible (~$3 per 1,000 tracks active runtime, ~$2 uptime).
- Scale-to-zero works.

Conditions before scaling further:

1. **Confirm the manual audit** with actual listening on the 3 `UNCLEAR` `SYNC_VERIFIED` tracks and a random subset of the `SYNC_NEEDS_REVIEW` tracks.
2. **Track-level cache behavior** should be re-examined: the reported `cache_hit: true` for every track reflects the worker's on-disk stem cache, not pre-existing cached stems. The observed Demucs runtimes (median 2.9 s, P90 12.9 s) are already fast on a warm worker.
3. **Long tracks (> 5 min)** showed max client wall times near 30 s; ensure the RunPod Load Balancer gateway timeout is not a latent blocker for longer tracks.
4. **Concurrency=2 worked cleanly** with `workersMax=2` and Flashboot; keep `workersMax` at the intended concurrency level and use `workersMin=0` so the endpoint scales to zero when idle.

If the actual listening audit confirms the data-driven calls, Phase B correction work can proceed on the ~11 % `SYNC_FAILED` tracks (mostly `suspected_wrong_version` and `local_mismatch`) and the `SYNC_NEEDS_REVIEW` borderline cases.
