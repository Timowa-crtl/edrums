# edrums

Turn a Rock Band 1 drum kit (Wii edition, USB `1bad:0005`) into low-latency
e-drums on Linux. Pure Python standard library, no pip installs.

- Reads pad hits from `/dev/input/js0` (stock kernel `hid-generic` driver)
- Plays acoustic drum samples with round-robin variation
- Writes straight to ALSA via `ctypes` with a ~4 ms buffer and asks PipeWire
  for a 64-frame quantum

## Usage

```sh
python3 fetch_samples.py   # one-time: downloads & prepares the kit into ./kit
python3 edrums.py          # hit the pads, Ctrl+C to quit
```

Requires `libasound.so.2` and `libsndfile.so.1` (both present on a stock Ubuntu
desktop).

## Pad mapping

| Pad    | Joystick button | Sound         |
|--------|-----------------|---------------|
| Red    | 2               | Snare         |
| Yellow | 3               | Closed hi-hat |
| Blue   | 0               | High tom      |
| Green  | 1               | Crash         |
| Kick   | 4               | Kick          |

Button 7 fires with every pad hit and is ignored. The kit reports no velocity.

## Tweaking the sound

Edit `PIECES`, `MICS`, `MIC_OVERRIDES` and `DRIVE` in `fetch_samples.py`, then
re-run it (downloads are cached in `kit/flac`).

## Samples

[Virtuosity Drums](https://github.com/sfzinstruments/virtuosity_drums), CC0.
