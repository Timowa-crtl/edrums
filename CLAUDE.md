# edrums

A simple, low-latency drum module ("drum brain") for the Rock Band 1 drum kit
(Wii edition, USB `1bad:0005`) on Linux, focused on the Raspberry Pi
(Pi 4 now, Pi Zero 2 W as a target). See README.md for usage.

## Goals, in priority order

1. **Low, steady latency.** Pad hit to sound should stay around 10 ms or less.
   Never trade latency for features.
2. **Simple.** One script (`edrums.py`), stdlib only plus `audioop`
   (`audioop-lts` on Python 3.13+). No new dependencies without asking.
3. **Works on most Linux machines.** With PipeWire, PulseAudio or bare ALSA.
   The developer also runs it on a ThinkPad T480 (Ubuntu, PipeWire), so
   changes must not break that.

## Files

- `edrums.py`: the drum module (joystick input, mixer, ALSA output).
- `fetch_samples.py`: one-time download/preparation of the sample kits into
  `kit/` (gitignored). Samples must be 48 kHz, stereo, 16-bit.

## Architecture (edrums.py)

- **Input:** the main thread reads `/dev/input/jsN` (Linux joystick API).
  Pads and buttons are mapped in `PADS`, `KITS` (D-pad) and `VOLUME_BUTTONS`.
- **Mixer thread:** mixes 64-frame blocks (1.33 ms) and writes them to ALSA,
  blocking on the small buffer.
- **ALSA output:** `AlsaOut` uses libasound via `ctypes`. The script opens the
  `default` device unless `-d` / `EDRUMS_AUDIO` names another. If the card
  grants fewer than 2 periods, the buffer is grown, because a single period
  means nonstop underruns.
- **Latency display:** each hit carries the time its event was read. After
  the hit's first block is written, `snd_pcm_delay` gives the time until it
  plays. A printer thread prints the result, so the audio thread never blocks
  on the console.

## Hard rules for the audio path

- **No per-sample Python loops in the mixer** unless measured on the Pi. Use
  `audioop` functions, which run in C. A slow mix makes ALSA underrun, and on
  PipeWire the restart stalls so long that it never recovers (endless
  "zip-zip", see commit 9342f16). The soft limiter is the one exception: about
  0.18 ms per block on a Pi 4, tight on a Pi Zero 2 W. If it causes crackles,
  rewrite it with `audioop`.
- **No printing, locking for long, or allocating large objects in the mixer
  thread.**
- **Default to ALSA's `default` device.** It routes through PipeWire or
  PulseAudio where present. Hard-coding a card breaks other machines. When the
  user picks a card, recommend `plughw:<name>` over `hw:`, because `plughw:`
  converts formats the card doesn't support natively.

## Verifying changes

There are no automated tests. Check audio changes on real hardware:

- `python3 -m py_compile edrums.py`
- Run with the kit and watch the per-hit latency lines. On a Pi 4 with a USB
  sound card at 4 ms they read about 8–9 ms with almost no spread. A rise or
  any spread points to a problem.
- Underrun check: open `AlsaOut` in a script and count `snd_pcm_recover`
  calls while triggering many voices. The count must be zero.

## Raspberry Pi notes

- The Pi 4 headphone jack (bcm2835) has a minimum period of 480 frames, so
  its buffer is about 21 ms. Use a USB sound card instead:
  `-d plughw:<card name>` (the card name survives replugging).
- `usbhid.jspoll=1` in `/boot/firmware/cmdline.txt` polls the kit every 1 ms
  instead of 10 ms. `usbhid` is built into the Pi kernel, so this has to be a
  boot parameter.
- No PipeWire runs on the Pi. ALSA talks to the card directly, and that is
  intended.
