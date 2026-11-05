#!/usr/bin/env python3
"""Minimal e-drum POC for the Rock Band 1 Wii drum kit (1bad:0005).

Reads pad hits from the Linux joystick device, plays acoustic drum
samples (CC0 "Virtuosity Drums", prepared by fetch_samples.py) and writes
the mix straight to ALSA via ctypes with a tiny buffer.

The D-pad switches between four kits (see KITS).

Usage: python3 edrums.py [/dev/input/jsN]
"""
import array
import ctypes
import glob
import os
import select
import struct
import sys
import threading
import warnings
import wave

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import audioop  # stdlib up to Python 3.12
    except ImportError:  # 3.13+: pip install audioop-lts
        sys.exit("Needs the audioop module: on Python 3.13+ run `pip install audioop-lts`.")

RATE = 48000  # PipeWire's native rate, avoids resampling
CHANNELS = 2
BLOCK = 64  # frames per mix block (~1.3 ms)
LATENCY_US = 4000  # requested ALSA buffer size
KIT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kit")

# Ask PipeWire's ALSA plugin for a small quantum (default is 1024 = 21 ms).
os.environ.setdefault("PIPEWIRE_LATENCY", f"{BLOCK}/{RATE}")

# Joystick button index -> (pad name, sound slot)
PADS = {
    2: ("red", "snare"),
    3: ("yellow", "hihat"),
    0: ("blue", "tom"),
    1: ("green", "cymbal"),
    4: ("kick", "kick"),
}
# Button 7 fires along with every pad hit; ignored. The 1/2/A/B buttons
# share indices 0-3 with the pads, so they trigger sounds too.

# D-pad (hat axes) -> kit, as (axis, value): axis 4 = left/right, 5 = up/down
KITS = {
    (5, -32767): "studio",  # up (default)
    (4, -32767): "jazz",    # left
    (4, 32767): "rock",     # right
    (5, 32767): "lofi",     # down
}
DEFAULT_KIT = "studio"

# +/- buttons -> master volume step in dB
VOLUME_BUTTONS = {9: +3, 8: -3}
VOLUME_RANGE_DB = (-30, 6)  # above 0 dB loud hits saturate (soft distortion)


def load_wav(path):
    with wave.open(path) as w:
        assert w.getframerate() == RATE and w.getnchannels() == CHANNELS
        pcm = array.array("h", w.readframes(w.getnframes()))
    # Trim the silent pre-roll so the transient plays immediately.
    peak = max(map(abs, pcm))
    onset = next(i for i, s in enumerate(pcm) if abs(s) > peak * 0.05)
    start = max(0, onset // CHANNELS - RATE // 2000) * CHANNELS  # keep 0.5 ms
    return pcm[start:].tobytes()


class Sound:
    """Round-robin over the recorded variants of one kit piece."""

    def __init__(self, kit, name):
        files = sorted(glob.glob(os.path.join(KIT_DIR, kit, f"{name}_*.wav")))
        if not files:
            sys.exit(f"No samples for '{kit}/{name}' in {KIT_DIR}. Run fetch_samples.py first.")
        self.variants = [load_wav(f) for f in files]
        self.next = 0

    def pick(self):
        s = self.variants[self.next]
        self.next = (self.next + 1) % len(self.variants)
        return s


class Mixer:
    MAX_VOICES = 16

    def __init__(self):
        self.voices = []  # [samples, pos]
        self.lock = threading.Lock()
        self.gain = 1.0  # master volume, set from the main thread

    def trigger(self, samples):
        with self.lock:
            self.voices.append([samples, 0])
            if len(self.voices) > self.MAX_VOICES:
                self.voices.pop(0)

    def run(self, out):
        # Mixing must stay far below the 1.3 ms block budget: if it gets close,
        # one hiccup causes an underrun, PipeWire's restart stalls ~0.25 s and
        # the mixer never catches up again (endless "zip-zip"). audioop.add
        # sums 16-bit PCM in C with saturation, so we never loop per sample.
        n = BLOCK * CHANNELS * 2  # bytes per block
        silence = bytes(n)
        while True:
            with self.lock:
                voices = list(self.voices)
            mix = silence
            gain = self.gain
            for v in voices:
                chunk = v[0][v[1]:v[1] + n]
                if len(chunk) < n:
                    chunk += silence[len(chunk):]
                if gain != 1.0:  # scale each voice before summing, so turning
                    chunk = audioop.mul(chunk, 2, gain)  # down avoids clipping
                mix = audioop.add(mix, chunk, 2)
                v[1] += n
            with self.lock:
                self.voices = [v for v in self.voices if v[1] < len(v[0])]
            out.write(mix)  # blocks when the small ALSA buffer is full


class AlsaOut:
    SND_PCM_STREAM_PLAYBACK = 0
    SND_PCM_FORMAT_S16_LE = 2
    SND_PCM_ACCESS_RW_INTERLEAVED = 3

    def __init__(self, device=b"default"):
        self.lib = ctypes.CDLL("libasound.so.2")
        self.lib.snd_strerror.restype = ctypes.c_char_p
        self.pcm = ctypes.c_void_p()
        self._check(self.lib.snd_pcm_open(
            ctypes.byref(self.pcm), device, self.SND_PCM_STREAM_PLAYBACK, 0))
        self._check(self.lib.snd_pcm_set_params(
            self.pcm, self.SND_PCM_FORMAT_S16_LE,
            self.SND_PCM_ACCESS_RW_INTERLEAVED,
            CHANNELS, RATE, 1, LATENCY_US))

    def _check(self, err):
        if err < 0:
            raise RuntimeError(f"ALSA: {self.lib.snd_strerror(err).decode()}")

    def write(self, data):
        frame_bytes = CHANNELS * 2
        while data:
            r = self.lib.snd_pcm_writei(self.pcm, data, len(data) // frame_bytes)
            if r < 0:  # underrun etc.: recover and carry on
                self._check(self.lib.snd_pcm_recover(self.pcm, r, 1))
                continue
            data = data[r * frame_bytes:]

    def close(self):
        self.lib.snd_pcm_close(self.pcm)


def main():
    dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/input/js0"
    print("Loading kits...", flush=True)
    kits = {kit: {slot: Sound(kit, slot) for _, slot in PADS.values()}
            for kit in KITS.values()}
    kit = DEFAULT_KIT
    volume_db = 0

    out = AlsaOut()
    mixer = Mixer()
    threading.Thread(target=mixer.run, args=(out,), daemon=True).start()

    fd = os.open(dev, os.O_RDONLY)
    print(f"Listening on {dev}. Kit: {kit}. D-pad switches kits "
          "(up studio, left jazz, right rock, down lofi), +/- volume. "
          "Ctrl+C to quit.", flush=True)
    try:
        while True:
            select.select([fd], [], [])
            _, value, etype, num = struct.unpack("IhBB", os.read(fd, 8))
            if etype == 1 and value == 1 and num in PADS:  # button press, not init
                pad, slot = PADS[num]
                mixer.trigger(kits[kit][slot].pick())
                print(f"{pad:>6} -> {kit}/{slot}", flush=True)
            elif etype == 1 and value == 1 and num in VOLUME_BUTTONS:
                lo, hi = VOLUME_RANGE_DB
                volume_db = max(lo, min(hi, volume_db + VOLUME_BUTTONS[num]))
                mixer.gain = 10 ** (volume_db / 20)
                print(f"=== volume: {volume_db:+d} dB ===", flush=True)
            elif etype == 2 and (num, value) in KITS:  # D-pad pressed
                kit = KITS[(num, value)]
                print(f"=== kit: {kit} ===", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)
        out.close()


if __name__ == "__main__":
    main()
