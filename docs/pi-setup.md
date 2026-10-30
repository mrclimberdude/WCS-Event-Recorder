# Pi installation and supervised bench handoff

This guide targets the selected [Pi 5, microphone and SSD](prototype-hardware.md) using Raspberry Pi OS Lite 64-bit. Installation and capture software can be checked synthetically; actual Linux/ARM installation, microphone compatibility, timing, power, cooling and sustained storage acceptance remain pending. Read the [first-capture plan](pi-bring-up-plan.md) for scope and the [status](status.md) for remaining gates. This is a foreground, finite-session workflow; no automatic startup or recovery is configured.

## Prepare the Pi manually

On another computer, follow the official [Imager and headless setup instructions](https://www.raspberrypi.com/documentation/computers/getting-started.html). Select the Pi 5 and Raspberry Pi OS Lite **64-bit**, then identify the intended microSD by capacity/device identity before authorizing Imager to overwrite it. Configure an operator username, unique hostname, SSH with your public key and the intended network. Keep Wi-Fi secrets out of this repository. Retain the exact image release/date in private setup notes. Lite provides a command-line system; 64-bit supports the Pi 5. See the official [OS edition/architecture guidance](https://www.raspberrypi.com/documentation/computers/os.html).

With power disconnected, assemble the case and official Active Cooler according to their supplied instructions, checking clearances and the fan connection. Insert the flashed microSD, connect the microphone and SSD (prefer a USB 3 port for the SSD), provide cable strain relief, then connect Ethernet if available and the official 27 W supply. Reserve USB-C for power. Verify the SSH host identity before trusting the first connection. Do not assume either the microphone format or combined peripheral power stability has been established.

The following blocks run in an SSH **Linux shell**, not Windows PowerShell. OS package installation is a manual privileged, online step. It does not authorize any provider execution or publication:

```sh
sudo apt update
sudo apt install python3 python3-venv python3-pip alsa-utils util-linux git ca-certificates
uname -m
python3 --version
cat /etc/os-release
arecord --version
```

Require `aarch64` and Python 3.11 or newer. Record these outputs and the image name privately. Check installed tool behavior with `man arecord` and `man findmnt`; the [ALSA manual](https://manpages.debian.org/trixie/alsa-utils/arecord.1.en.html) documents device listing and explicit hardware selection. Ordinary-user ALSA access must work. If the distribution requires `audio` group membership, the owner can add the selected operator account manually with `sudo usermod -aG audio OPERATOR`, then log out/in. Do not run the recorder as root to bypass permissions.

## Identify and mount the SSD manually

Inspect disks before any storage administration:

```sh
lsblk -o NAME,PATH,SIZE,FSTYPE,UUID,MOUNTPOINTS,MODEL,SERIAL
findmnt --mountpoint / --output TARGET,SOURCE,FSTYPE,UUID
```

Match the SSD physically by model/serial/capacity and verify its partition's filesystem UUID. Do not select a disk merely because it happens to be `/dev/sda`. Back up existing contents first. This package performs no partitioning or formatting; if ext4 preparation is needed, the owner must handle the explicitly identified device separately. Ext4 is the preferred prototype filesystem.

For an already prepared SSD, the owner creates `/mnt/wcs-audio` with `sudo mkdir -p /mnt/wcs-audio`, then manually edits `/etc/fstab` with `sudoedit /etc/fstab`. Substitute the verified filesystem UUID in this example line:

```text
UUID=PLACEHOLDER-SSD-FILESYSTEM-UUID /mnt/wcs-audio ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2
```

UUID avoids changing device-order names; `nofail` allows boot to continue when the SSD is absent. Review the installed [fstab manual](https://manpages.debian.org/trixie/mount/fstab.5.en.html). Reload the mount configuration and mount only the named target:

```sh
sudo systemctl daemon-reload
sudo mount /mnt/wcs-audio
findmnt --mountpoint /mnt/wcs-audio --output TARGET,SOURCE,FSTYPE,UUID,OPTIONS
```

**Stop here if the exact mount, expected UUID, ext4 type and writable options do not match.** Compare its source with the root filesystem. Only after that verification, create the recording directory and assign it to the ordinary operator (replace both `OPERATOR` placeholders):

```sh
sudo install -d -m 700 -o OPERATOR -g OPERATOR /mnt/wcs-audio/recordings
```

Use `findmnt --mountpoint`, which requires the actual mountpoint; `findmnt --target` alone can report an underlying microSD filesystem. See [findmnt](https://manpages.debian.org/trixie/util-linux/findmnt.8.en.html). Never create a fallback audio directory on microSD, automatically remount a missing drive, or delete old recordings to make room. The recorder must independently verify storage identity immediately before writing; a successful manual check is not a lasting guarantee.

## Install the application as the operator

Obtain a trusted checkout of this repository on microSD, outside the SSD audio directory. Run from its root. Create private state outside the checkout; the example uses separate absolute source and environment paths. The shell expands `$HOME` into those absolute paths:

```sh
umask 077
mkdir -p "$HOME/wcs-private"
python3 deploy/pi/install.py --source "$HOME/wcs-event-recorder" --venv "$HOME/wcs-private/app-venv"
```

Replace the source path with the actual checkout. The helper requires Linux aarch64, Python >=3.11, Git, venv/ensurepip, an existing private parent directory, a valid source checkout and separate paths without links or parent traversal. It refuses root and Windows before creating anything. It installs the ordinary package into its dedicated venv, with no web/browser extras. This step accesses package indexes and may build dependencies; allow time and connectivity. Recording itself needs no network or provider token.

Repeating the helper reuses its owned compatible venv and runs pip installation/check again. It refuses unrelated or incompatible existing environments and preserves them; choose a new explicit venv path rather than deleting an old environment. Failed attempts retain their files for inspection; an incomplete environment may also require a new path. It never edits disks, firmware, credentials, firewall or boot services. Installation trusts the selected source and the Python package supply chain.

The private venv's `wcs-pi-install.json` records source revision, dirty-workspace flag, Python version and resolved distribution names/versions. Retain this with the OS/ALSA observations. It records no environment dump or credential-bearing package URLs. Keep machine config, bounded operator logs, databases and reports under `$HOME/wcs-private`, outside version control; on a development checkout use ignored `data/`. This foreground slice adds no persistent log service. If collecting terminal logs, use a finite session and an explicit size limit; stop logging at the limit and retain earlier evidence. Budget microSD state separately from SSD audio, and do not prune recordings automatically.

For the short `wcs-recorder` commands in the capture guide, activate this venv in the current Linux shell:

```sh
. "$HOME/wcs-private/app-venv/bin/activate"
```

Alternatively, use the full `$HOME/wcs-private/app-venv/bin/wcs-recorder` path each time. Activation is per shell; reconnecting over SSH needs activation again or the explicit path.

## Run the offline installed-package smoke check

From the checkout, choose a fresh private smoke directory and database. These commands create two seconds of synthetic PCM silence, validate its manifest/schedule and run fixture recognition through the installed package. They do not open the microphone or call providers:

```sh
"$HOME/wcs-private/app-venv/bin/python" scripts/create_demo_inputs.py --output "$HOME/wcs-private/smoke-001"
"$HOME/wcs-private/app-venv/bin/wcs-recorder" validate-inputs --manifest "$HOME/wcs-private/smoke-001/manifest.json" --schedule "$HOME/wcs-private/smoke-001/schedule.json"
"$HOME/wcs-private/app-venv/bin/wcs-recorder" process-fixture --database "$HOME/wcs-private/smoke-001.sqlite3" --manifest "$HOME/wcs-private/smoke-001/manifest.json" --schedule "$HOME/wcs-private/smoke-001/schedule.json" --fixture tests/fixtures/aggregation-demo.json --window-seconds 0.25 --hop-seconds 0.25
```

Expect successful validation and a completed fixture run with three song occurrences (A, B, A). Preserve its printed run ID. A repeat needs fresh output paths. This verifies installed file processing; it cannot establish microphone operation or room-acoustic accuracy.

## Configure and check before opening the microphone

Copy the tracked placeholder example into private state, then edit the copy:

```sh
cp deploy/pi/config.example.json "$HOME/wcs-private/capture.json"
chmod 600 "$HOME/wcs-private/capture.json"
arecord --list-devices
timedatectl status
date --utc --iso-8601=seconds
```

Replace the hardware selector and SSD UUID with the explicitly identified devices. Use `hw:CARD=ACTUAL_CARD_ID,DEV=0`, never a default or implicit conversion device. Supported software profiles are signed 16-bit little-endian PCM (`S16_LE`), one/two channels and 44,100/48,000 Hz; choose one only after an explicit probe verifies the actual negotiation. Do not infer microphone capability from the example's 48 kHz mono setting.

The closed version-1 config requires every example field and rejects unknown/duplicate keys. `recording_root` must be strictly beneath the absolute `mount_path`. Set event/room IDs and an event IANA timezone independently of the device's display timezone. `chunk_seconds` is 1-900 (normally 600); `session_seconds` is required, finite and at most 21,600; at most 256 chunks are allowed. Set `max_audio_bytes` to at least `session_seconds * sample_rate * channels * 2`. The example's 1,200-second mono session needs 115,200,000 PCM bytes; headers/metadata and reserve require additional storage. `reserve_bytes` must be at least 2,147,483,648. Capture refuses insufficient space rather than falling back to microSD.

Check synchronization while connectivity is available using the distribution's configured time service; the official [configuration guide](https://www.raspberrypi.com/documentation/computers/configuration.html) covers system administration. Keep independent before/after clock observations privately. An OS synchronization flag, timezone display and measured absolute accuracy are different facts. Preflight requires usable synchronization evidence even for subsequent offline operation; unknown state means stop and resolve it. No command here sets the clock automatically.

Use the installed `wcs-recorder` executable with the following commands; see the [capture contract](microphone-capture.md) for outputs and refusal codes. Run `capture-preflight --config PATH` first; it inspects without opening the microphone. The optional `--check-write` explicitly authorizes its small write/sync/remove storage probe. Then `capture-probe --config PATH` explicitly opens the microphone and discards a bounded PCM sample. Only after reviewing both results run `capture-microphone --config PATH` in the foreground for the configured finite session. These capture operations are Linux-only; Windows software tests do not provide hardware acceptance. No recognition requests, schedule or provider credentials are required.

## Review, seal and hand off

Inspect the reported new session directory and terminal outcome; absence of a terminal result after a crash is unconfirmed acquisition. Listen to the finalized WAVs and inspect format, levels and continuity. Retain host/monotonic observations and exact session-frame mappings. Host launch/first-read time does not establish the true first-sample clock because device/USB buffering latency remains unknown.

Prepare the bound operator anchor review described by the [capture contract](microphone-capture.md), then invoke `seal-capture --session-directory PATH --anchor-review PATH --max-source-bytes 2000000000 --max-seconds 120`. These explicit verification bounds must cover the retained session; choose a larger byte bound deliberately for longer sessions. Sealing freshly verifies retained audio and creates manifests only for a consciously accepted anchor, including an explicitly provisional bench estimate when appropriate. It does not repair or resume capture. Preserve the original start/terminal records and incomplete files on failure. A filename alone never proves completion or downstream readiness.

For a first hardware bench test, use 30-60 seconds from laptop speakers; then a finite 20-30 minute trial with 600-second chunks. Review independent clocks, boundaries, hashes, temperature/power and write behavior; deliberately stop with Ctrl+C and start a fresh session. Test missing SSD and missing/busy microphone with expendable recordings. Physical continuity, latency, clock accuracy and sustained capacity remain unverified until these observations are retained.

Use a sealed manifest with the existing [recording-to-review workflow](operator-guide.md), choosing a fixture for offline verification. Current recognition processes one completed source per run. Independently processing chunks can split a song or miss a boundary-straddling window; this slice does not provide continuous whole-session recognition. Phase 5 [independent recording/label acceptance](phase-5-trial-checklist.md), real provider execution, service outputs and public hosting remain separate gates.
