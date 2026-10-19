# Phase 1 input formats

The `validate-inputs` command reads a recording manifest, an event schedule, and the referenced WAV file. It inspects and validates them without replaying audio, recognizing songs, assigning context to plays, or writing a database.

Both JSON documents use `schema_version: 1`. Strings required by the schema must be nonempty. IDs must be unique within each schedule collection, and references must resolve. Duplicate JSON keys and non-finite numbers such as `NaN` are rejected. Files are UTF-8; a UTF-8 byte-order mark is accepted for compatibility with Windows editors.

## Recording manifest

```json
{
  "schema_version": 1,
  "source_id": "sample-main-001",
  "event_id": "demo-event",
  "room_id": "main-ballroom",
  "audio_path": "sample.wav",
  "start_time": "2027-01-01T23:59:59-05:00",
  "timezone": "America/New_York"
}
```

All fields above are required. Relative audio paths resolve against the manifest's directory, regardless of the CLI's working directory. Absolute paths are also accepted. Use forward slashes in JSON paths on Windows, or escape each backslash as `\\`.

`start_time` is when the first audio frame occurred at the event, not when processing starts. `timezone` is an IANA name, such as `America/New_York`. Manifest event ID, room ID, and timezone must agree with the schedule; timezone names must match exactly rather than relying on alias equivalence.

Audio metadata is read from the file, not trusted from JSON. Start with standard uncompressed integer PCM WAV, 1–4 bytes per channel sample. The Python 3.11 baseline does not support float WAV or WAVE_FORMAT_EXTENSIBLE; convert those inputs to standard PCM WAV before use. Compressed audio and video decoding are deferred. See the official [`wave` documentation](https://docs.python.org/3.11/library/wave.html).

Validation checks the header, nonempty frame count, and availability of the declared final frame. It reads one audio frame after seeking; it does not scan the whole payload or verify acoustic content. Original files are opened read-only and remain unmodified.

## Schedule

```json
{
  "schema_version": 1,
  "source": "synthetic_fixture",
  "event": {
    "event_id": "demo-event",
    "name": "Synthetic WCS Weekend",
    "timezone": "America/New_York"
  },
  "rooms": [
    {"room_id": "main-ballroom", "name": "Main Ballroom"}
  ],
  "sessions": [
    {
      "session_id": "social",
      "room_id": "main-ballroom",
      "start_time": "2027-01-01T23:00:00-05:00",
      "end_time": "2027-01-02T00:30:00-05:00",
      "type": "social"
    }
  ],
  "djs": [
    {"dj_id": "dj-example", "name": "Example DJ"}
  ],
  "dj_shifts": [
    {
      "shift_id": "evening-shift",
      "dj_id": "dj-example",
      "room_id": "main-ballroom",
      "start_time": "2027-01-01T23:00:00-05:00",
      "end_time": "2027-01-02T00:30:00-05:00"
    }
  ]
}
```

All top-level sections above are required. Collection sections may be empty, though the manifest's room must exist. Session types are `social`, `competition`, and `unknown`. Sessions may additionally include nullable `division` and `round` strings. Every session and shift must reference a declared room; every shift must reference a declared DJ.

`source` records the schedule's provenance, such as a published schedule URL or a label indicating manual/synthetic input. It is not fetched by this command. An attribution derived later from this schedule must preserve that provenance; the schedule alone does not confirm actual activity. No numeric confidence is assigned during input validation.

## Time rules

- Supply complete dates and times, including seconds and an explicit offset. Up to six fractional second digits are supported.
- UTC timestamps ending in `Z` or a zero offset are accepted. Other timestamps must use the event's local time and the correct offset for that date.
- Invalid local times, such as a skipped spring daylight-saving hour, are rejected. An explicit offset distinguishes the two occurrences of a repeated autumn hour.
- Timestamps normalize to UTC internally. The named event timezone is retained and used for CLI display. The [`tzdata` dependency](https://docs.python.org/3.11/library/zoneinfo.html) supplies named zones on Windows.
- End times must be later than start times. Cross-midnight sessions must explicitly use the next date.
- All intervals are half-open: `[start, end)`. Adjacent sessions sharing a boundary do not overlap.

Audio position is measured in integer sample frames. One frame contains one sample per channel, so frame size is `channels * sample_width` bytes. Durations retain an exact rational representation. Absolute times derive from the original start plus an absolute frame offset, rounded once to Python datetime's microsecond precision. Adding the duration in UTC avoids daylight-saving wall-clock arithmetic errors.

## Warnings and exit codes

The validator inspects session and DJ coverage separately, restricted to the recording's room and time interval. It reports `session_gap`, `session_overlap`, `dj_gap`, and `dj_overlap` warnings with exact affected intervals and overlapping IDs. It does not choose between conflicting schedule entries or fill uncovered periods.

| Exit code | Meaning |
| --- | --- |
| `0` | Inputs are valid; schedule coverage warnings may still be present |
| `1` | Input validation failed |
| `2` | Command-line arguments are invalid |

Summaries go to stdout. Warnings, errors, and optional `--verbose` troubleshooting details go to stderr. No credentials, recognition services, or network access are needed for validation.

## Shared model boundaries

`models.py` defines `AudioSource`, `RecordingSource`, `AudioChunk`, source windows, schedule entities, `Track`, recognition candidates/results, `SongPlay`, inference, and analysis-run metadata. These are in-memory contracts rather than a database schema. JSON loaders validate external input before constructing schedule entities; audio models additionally guard frame ranges and payload lengths. `audio.FileAudioSource` and the [replay scheduler](replay.md) now implement the file-input stream.

Recognition candidates have no canonical track ID until resolution. Results retain match/no-match/error status and raw evidence. [Fixture recognition](recognition.md) produces raw results and JSONL diagnostics; [aggregation](aggregation.md) additionally emits conservative identity decisions and provisional song plays. [SQLite processing](persistence.md) persists those relationships, durable tracks, and optional schedule snapshots. Plays reference their track, estimated source window, run, supporting/bridged evidence, and closure reason. Live and final inference are separate optional values. Missing confidence remains unknown. [Scheduled context](attribution.md) is attached as independent session/DJ intervals from saved snapshots. Step 8 adds an explicit [real-provider command](provider-recognition.md) and [independent labels/evaluation](evaluation.md); actual event-state inference and the private acoustic trial remain pending.

## Private evaluation sample

The generated demo contains silence and demonstrates software behavior through validation, replay, fixture recognition, and grouping. The real Phase 1 evaluation still needs a private 3–5 minute event WAV containing at least two known songs. Place it under `data/samples/` with a manifest, schedule, and notes recording approximate known song boundaries. Keep that audio and its notes out of version control. Automated tests use synthetic audio and can run before this sample is selected.
