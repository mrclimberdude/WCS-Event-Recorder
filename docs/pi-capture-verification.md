# Pi setup and supervised capture software verification

Recorded 2026-10-30 for the [first-capture execution plan](pi-bring-up-plan.md). This is a completed software verification record, not Linux/Pi or acoustic acceptance. The Pi had not arrived. Future changes should add another record rather than edit these measured results.

## Delivered scope

Implemented strict private capture configuration; ordinary-user Pi installation support; local tool/device/SSD/clock preflight; explicit discarded one-second microphone probing; finite single-process native PCM acquisition; exact WAV rotation; bounded clean-stop queued-tail draining; retained failure facts; and reviewed-clock source sealing for existing completed-file processing. Read the [setup guide](pi-setup.md) and [capture contract](microphone-capture.md).

The new runtime boundary comprises `capture_config.py`, `capture_preflight.py`, `capture_process.py`, `capture.py`, `capture_artifacts.py`, `capture_sealing.py` and `cli_capture.py`. Existing code changes are limited to CLI registration/dispatch. No SQLite schema, fixed-length source contract, recognition method, historical report or public output format changed. Installation assets remain in `deploy/pi/` for use from a trusted checkout.

## Tests and compatibility

| Gate | Observed result |
| --- | --- |
| Full installed-package regression | 3,165 passed, 42 skipped, no failures; 747.55 seconds. This invocation did not select the retained browser cache. |
| Explicit retained-Chromium rerun | 53 passed, no skips, including all 31 actual Chromium cases; 39.88 seconds. Browser cases initially skipped by the broad run passed here. |
| Combined unique application tests | **3,196 passed, 11 unresolved platform skips, no failures** across 3,207 identities; repeated passing cases are counted once. |
| Starting identities / additions | All 3,001 starting test identities retained; 206 new cases, with 203 passing and three Linux/POSIX cases skipped on Windows. |
| Warnings | Existing upstream Starlette/HTTPX TestClient deprecation warning; capture checks also passed with unraisable-exception warnings treated as errors. |
| Frozen legacy corpus | 35 histories, all 174 exact historical reports and all 228 protected files unchanged; zero real provider requests. The corpus was not regenerated. |
| Existing code/test/help compatibility | 222 preexisting source/test/fixture/script files unchanged; two existing CLI files intentionally extended; all 61 existing subcommand help outputs unchanged. Four capture commands added. |
| Execution stability | All 240 snapshotted source/test/script/deployment files unchanged across the frozen regression and final audit. |
| Historical documentation | All 17 completed implementation plans remained unedited; earlier verification records remained untouched. Current guides and active plans were updated separately. |

The eight unresolved older skips require real Windows symlink privileges. Three new skips require a POSIX FIFO, actual Linux directory-relative write/sync/remove, and a POSIX child that ignores SIGTERM to exercise forced termination. Portable configuration/orchestration, mocked Linux facts, and real synthetic child stdout/stderr tests did execute. These do not establish actual ALSA/ARM behavior. WSL distribution inspection was unavailable to this session, so no Linux execution is claimed.

Coverage includes malformed/duplicate/oversized inputs and independent byte/frame limits; unmounted/wrong/read-only/low-space storage; no microSD fallback; native negotiated format and exact rate; busy/missing devices; clock synchronization and steps; finite child/queue/no-data handling; fragmented mono/stereo byte reconstruction; exact/multiple/partial rotation; operator signals and bounded queued-tail drain; incomplete frames and xrun failures; output exclusivity; source corruption/substitution; bound anchor review; staged manifest publication; retained originals; and existing fixed-length source loading. Captured sample counts and unknown device-tail facts remain separate from physical continuity claims.

## Installed-wheel rehearsal

Built a wheel entirely offline using the existing base Python build tools, then installed it separately under the private verification directory. The development venv lacked the `wheel` build command, so it was not modified to add build dependencies. The resulting wheel SHA-256 was:

```text
da1f9c856fa0a7e6b1562463c84ab24d48a73757fc297e6cf07d68ad06f85c08
```

All 94 packaged runtime Python/browser files matched both the installed wheel and the final source bytes. An isolated `python -I` rehearsal loaded that installed package, blocked socket connections/DNS and optional web imports, and exercised:

```text
synthetic Python child -> native-format backend parser -> two WAV chunks
    -> explicit synthetic clock review -> fresh sealing verification
    -> existing fixture processing / accelerated replay of one sealed source
```

The generated source was three seconds of patterned 48 kHz stereo 16-bit PCM: 144,000 frames, split into 96,000 and 48,000 frames. The reconstructed PCM matched the independent byte oracle exactly. Sealing covered 576,088 aggregate WAV bytes. One two-second chunk produced eight fixture observations and three A/B/A occurrences through the installed application. Storage/clock adapters were explicitly synthetic; no microphone or actual mounted SSD was used. Original capture-result bytes remained unchanged. The rehearsal made zero provider requests and imported no optional web framework.

## Retained evidence and limits

Private artifacts are under ignored `data/pi-capture-verification/`: `frozen-regression.xml`/`.txt`, `browser.xml`/`.txt`, `test-summary.json`, `compatibility-summary.json`, the original corpus check, frozen source hashes, the wheel, separately installed package, rehearsal script/audio/database and `wheel-trial/verification.json`. They may not exist in another checkout. Earlier partial test runs were interrupted while final tests were being completed; only the frozen regression and explicit browser rerun supply the totals above.

Final current-document link/parser checks and focused documentation tests passed. These were documentation-only checks after the application snapshot; they do not imply another full application regression. No real recognition, audio upload, external output write, hardware installation or public deployment occurred.

Actual Pi/Linux installation, microphone identity/native formats, storage permissions/mount behavior, gain/listening, capture latency/drift, acoustic continuity, power/temperature and sustained workload remain pending [physical acceptance](pi-bring-up-plan.md#step-8-physical-acceptance-when-the-pi-arrives). Automatic startup/recovery, cross-file recognition, analysis backfill and unattended event readiness are outside this verified slice.
