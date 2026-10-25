# Phase 3 developer resource envelope

The local metadata view has a measured **synthetic developer envelope**, not an event-capacity or room-microphone acceptance claim. This probe exercises the installed read-only projector and ordinary SQLite writer APIs. HTTP latency, browser rendering, audio recognition accuracy, public hosting and sustained event operation are separate acceptance boundaries.

Run from the repository root with an existing output parent and a **new** child directory:

```powershell
.\.venv\Scripts\python.exe scripts/verify_phase3.py --output-directory data/phase3-resources
```

The optional controls default to `--max-seconds 120 --max-generated-bytes 134217728 --minimum-free-bytes 1073741824`. They are declared in `declared-envelope.json` before construction. The directory must be new; all synthetic WAVs, databases, logs and reports stay inside it. Existing/private databases and recordings are never inputs. Socket connections and DNS resolution are blocked in the worker, with explicit fail-closed checks. The parent removes inherited provider credentials and supervises a finite child process deadline.

The declared geometry is:

| Control | Developer bound |
| --- | ---: |
| Selected closed occurrences | 1 / 10 / 100 / 1,000 |
| Unrelated history control | 10 additional histories, 10 occurrences each, beside the 1,000-occurrence selection |
| Derived ancestry control | Eight supported pure offline generations over 10 occurrences |
| Page size / encoded payload | 50 occurrences / 524,288 bytes |
| Concurrent projection readers | Four, three reads each |
| Concurrent writer | 12 sequential committed raw observations in a separate run on the same database |
| SQLite busy timeout | One second |
| Ordinary projection target | Every measured sample at most one second |
| Writer observation-commit target | Every measured sample at most two seconds |
| Explicit startup validation target | Ten seconds |
| Separate Python allocation peak target | 16 MiB |
| Process lifetime peak target | 512 MiB |
| Generated artifact ceiling / free-space headroom | 128 MiB / 1 GiB |

The latency and memory thresholds are probe acceptance targets, not newly enforced production deadlines. Existing projection SQL-work, page/context/text/payload guards remain authoritative. Neither a SQLite busy timeout nor its VM-work guard is a hard wall-clock guarantee for arbitrary corrupt inputs. The supervisor deadline bounds the complete child, including fixture setup and any blocked worker; a stopped child does not establish capacity.

The fixtures use lossless synthetic silence and alternating saved A/B/A identities. The 200-frame windows advance by 100 frames, retaining repeated occurrences and overlapping estimated envelopes through actual persistence. They intentionally contain no acoustic evidence. The 1,000-occurrence source is about 12.5 seconds at 8 kHz; scaling its occurrence count does not simulate a long room recording, a large provider payload, dense schedules, a large catalog or a weekend event.

Each case performs full supported selection validation once as an explicit startup measurement. Three ordinary projector calls run without instrumentation or allocation tracing; their median and individual samples are retained. A separate instrumented pass counts `Database.execute` calls, rows fetched through those cursors and Python JSON decoding work. SQLite internal steps and direct connection operations are not equivalent to those counters. Another separate pass measures peak traced Python allocations. All passes must return identical safe projection bytes. Unchanged database bytes are checked around each read-only case.

Process working set and lifetime peak come from the existing benchmark platform helper. Lifetime peak includes interpreter imports, fixture construction and prior cases in this one worker, so it is not per-operation memory. Python allocation peaks describe a different quantity. Operating-system caches are uncontrolled. Query-plan evidence records the representative bounded page query using the existing run/event index and a temporary ordering B-tree; unchanged fetched-row counts do not prove constant SQL work as histories grow.

The interference case schedules four readers alongside real `RunStore.result` commits on the same synthetic database. It verifies ordinary `DELETE` journal mode without changing it, finite commit/read times and a clean `WriteRevision` guard after acknowledged owned commits. This checks that read-only polling does not look like a foreign database writer. It is a short concurrently scheduled developer workload, not a sustained write-pressure or power-loss test. WAL correctness has separate integration tests; this runner does not change production journal policy.

`report.json` records each case, environment/source hashes, measurements, checks, outcome, failure and any uncompleted cases. `worker.log` retains worker output. A supervisor timeout or worker failure before a report writes `stopped.json`. Exit zero requires all cases and the interference checks to pass. Failed/bounded outputs remain inspectable and must not be described as successful capacity measurements. The exclusive report's inventory is taken before writing that report; it is observed logical storage, not a continuously sampled SQLite journal high-water mark. The helper checks generated bytes/free space at bounded phase boundaries, with a 2 MB construction reservation and a 64 KiB supervisor-metadata reservation, not a filesystem quota.

## Recorded developer observation

On 2026-10-24, Python 3.11.5 / Windows build 26200, the first declared probe at `data/phase3-probe-ui-1/report.json` passed all six cases and the writer checks in **13.03 seconds**. Its inventory before the final report was **5,438,288 logical bytes**. This is one local observation with uncontrolled caches.

| Selection | Median normal projection (ms) | Startup validation (ms) | Page rows | Payload bytes | Counted SQL / fetched rows | Peak traced Python bytes |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| 1 occurrence | 1.981 | 7.566 | 1 | 1,925 | 22 / 48 | 21,316 |
| 10 occurrences | 2.354 | 3.676 | 10 | 5,847 | 31 / 66 | 58,637 |
| 100 occurrences | 4.093 | 15.144 | 50 | 23,524 | 71 / 147 | 240,933 |
| 1,000 occurrences | 7.166 | 134.092 | 50 | 23,635 | 71 / 147 | 242,431 |
| 1,000 plus unrelated histories | 6.479 | 127.531 | 50 | 23,635 | 71 / 147 | 242,431 |
| Eight derived generations | 3.330 | 36.458 | 10 | 5,856 | 39 / 75 | 58,678 |

At the 50-row page, the instrumented pass decoded 104 JSON values / 9,123 input bytes. The deep-lineage pass decoded 24 values / 2,275 bytes. Maximum observed process lifetime peak across cases was **51,204,096 bytes**; current working set at the final case was **45,658,112 bytes**. The concurrent readers' maximum call was **101.30 ms**, and the maximum observation commit was **18.96 ms**; the revision guard remained clean. These numbers support this declared local developer workload only. Larger selections, realistic context/catalog sizes, longer ancestry, more clients or sustained collection need another explicitly declared measurement before reliance.

Focused verification in `tests/test_phase3_verification.py` covers invalid/nonfinite deadlines before artifact creation, existing-directory preservation, a generated-byte budget stop with explicit uncompleted cases, and hard supervisor timeout. Browser/HTTP/privacy/cursor acceptance and frozen historical compatibility remain separate checks; passing this resource probe does not replace them.
