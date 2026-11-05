# edrums

Turn a Rock Band 1 drum kit (Wii edition, USB `1bad:0005`) into low-latency
e-drums on Linux. Pure Python standard library, no pip installs.

- Reads pad hits from `/dev/input/js0` (stock kernel `hid-generic` driver)
- Plays acoustic drum samples with round-robin variation
- Four kits, switched with the D-pad
- Writes straight to ALSA via `ctypes` with a ~4 ms buffer and asks PipeWire
  for a 64-frame quantum

## Usage

```sh
python3 fetch_samples.py   # one-time: downloads & prepares the kits into ./kit
python3 edrums.py          # hit the pads, Ctrl+C to quit
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
from −30 dB to +6 dB (start: 0 dB). Above 0 dB loud hits saturate slightly.

## Tweaking the sound

Edit `KITS` in `fetch_samples.py` (pieces, mic mix, overrides, drive), then
re-run it. `python3 fetch_samples.py rock` rebuilds just one kit; downloads
are cached in `kit/flac`.

## Samples

[Virtuosity Drums](https://github.com/sfzinstruments/virtuosity_drums), CC0.
