# edrums

A simple, low-latency drum module for the Rock Band 1 drum kit (Wii edition,
USB `1bad:0005`) on Linux, focused on the Raspberry Pi. Python standard
library plus `audioop`, no other dependencies.

- Reads pad hits from `/dev/input/js0` (stock kernel `hid-generic` driver)
- Plays acoustic drum samples with round-robin variation
- Four kits, switched with the D-pad
- Writes straight to ALSA via `ctypes` with a ~4 ms buffer (grown
  automatically if the sound card needs more) and asks PipeWire for a
  64-frame quantum
- Prints the latency of every hit

## Usage

```sh
python3 fetch_samples.py   # one-time: downloads & prepares the kits into ./kit
python3 edrums.py          # hit the pads, Ctrl+C to quit
python3 edrums.py -d plughw:Headset   # play on a specific sound card
```

`-d` takes any ALSA device (see `aplay -l`; use the card name, it survives
replugging). `EDRUMS_AUDIO=plughw:Headset` does the same. Without it the
script uses ALSA's `default` (PipeWire/PulseAudio on desktops).

Each hit prints its latency, e.g. `red -> studio/snare  8.1 ms (last 50:
avg ...)`: time from reading the pad event to the sound reaching the audio
output. The drum kit's USB polling comes on top (see below).

### Start at boot (systemd)

To make the Pi behave like a real drum module (power on, play):

```sh
./install-service.sh plughw:Headset   # sound card as for -d; omit for "default"
```

This installs and starts `/etc/systemd/system/edrums.service`, running as
your user. It starts at boot and restarts within 2 s whenever the script
exits, so unplugging and replugging the drum kit or sound card just works.

```sh
journalctl -u edrums -f               # live output incl. per-hit latency
sudo systemctl stop edrums            # stop (e.g. to run edrums.py by hand;
sudo systemctl start edrums           #   both at once: "Device or resource busy")
sudo systemctl disable --now edrums && sudo rm /etc/systemd/system/edrums.service   # remove
```

Requires `libasound.so.2` and `libsndfile.so.1` (both present on a stock Ubuntu
desktop) and the `audioop` module (stdlib up to Python 3.12; on 3.13+
`pip install audioop-lts`).

## Pad mapping

| Pad    | Joystick button | Slot   |
|--------|-----------------|--------|
| Red    | 2               | snare  |
| Yellow | 3               | hihat  |
| Blue   | 0               | tom    |
| Green  | 1               | cymbal |
| Kick   | 4               | kick   |

Button 7 fires with every pad hit and is ignored. The kit reports no velocity.
The 1/2/A/B buttons share indices 0-3 with the pads, so they trigger sounds too.

## Kits (D-pad)

| D-pad          | Kit    | Red          | Yellow          | Blue              | Green      | Kick       |
|----------------|--------|--------------|-----------------|-------------------|------------|------------|
| Up (default)   | studio | snare center | closed hi-hat   | high tom          | crash      | kick       |
| Left           | jazz   | soft snare   | soft closed hat | high tom, off-ctr | ride       | snares off |
| Right          | rock   | rimshot      | half-open hat   | low tom           | loud crash | punchy     |
| Down           | lofi   | studio pieces, recorded with the "lofi" mic position | | | | |

D-pad events: axis 5 = up (−32767) / down (+32767), axis 4 = left / right.

## Volume

`+` (button 9) / `−` (button 8) change the master volume in 3 dB steps,
from −30 dB to +6 dB (start: 0 dB). A soft limiter rounds off peaks that
overlapping hits push close to full scale.

## Raspberry Pi

Tested on a Pi 4 with Pi OS. To get low latency:

- **Use a USB sound card.** The Pi 4 headphone jack only allows 10 ms
  periods, so the buffer has to be ~21 ms. A USB adapter (e.g. the one that
  comes with a Logitech G430 headset) runs a 4 ms buffer: ~8 ms measured
  per hit. Start with `-d plughw:<card name>`.
- **Poll the drum kit every 1 ms** instead of the default 10 ms: append
  ` usbhid.jspoll=1` to the single line in `/boot/firmware/cmdline.txt` and
  reboot. Total pad-to-sound latency is then ~9–10 ms.
- No PipeWire needed: on Pi OS Lite ALSA talks to the card directly.

Pi Zero 2 W should work too (has no headphone jack, so needs a USB sound
card plus a USB hub on its single port), but has less CPU headroom.

Avoid Bluetooth audio: it adds 100–200 ms.

## Tweaking the sound

Edit `KITS` in `fetch_samples.py` (pieces, mic mix, overrides, drive), then
re-run it. `python3 fetch_samples.py rock` rebuilds just one kit; downloads
are cached in `kit/flac`.

## Samples

[Virtuosity Drums](https://github.com/sfzinstruments/virtuosity_drums), CC0.
