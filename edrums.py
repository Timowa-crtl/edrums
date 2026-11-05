#!/usr/bin/env python3
"""Minimal e-drum POC for the Rock Band 1 Wii drum kit (1bad:0005).

Reads pad hits from the Linux joystick device, plays acoustic drum
samples (CC0 "Virtuosity Drums", prepared by fetch_samples.py) and writes
the mix straight to ALSA via ctypes with a tiny buffer.

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
import wave

RATE = 48000  # PipeWire's native rate, avoids resampling
CHANNELS = 2
BLOCK = 64  # frames per mix block (~1.3 ms)
LATENCY_US = 4000  # requested ALSA buffer size
KIT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kit")

# Ask PipeWire's ALSA plugin for a small quantum (default is 1024 = 21 ms).
os.environ.setdefault("PIPEWIRE_LATENCY", f"{BLOCK}/{RATE}")

# Joystick button index -> (pad name, sound name)
PADS = {
    2: ("red", "snare"),
    3: ("yellow", "hihat"),
    0: ("blue", "tom"),
    1: ("green", "crash"),
    4: ("kick", "kick"),
}
# Button 7 fires along with every pad hit; ignored.


def load_wav(path):
    with wave.open(path) as w:
        assert w.getframerate() == RATE and w.getnchannels() == CHANNELS
        pcm = array.array("h", w.readframes(w.getnframes()))
    # Trim the silent pre-roll so the transient plays immediately.
    peak = max(map(abs, pcm))
    onset = next(i for i, s in enumerate(pcm) if abs(s) > peak * 0.05)
    start = max(0, onset // CHANNELS - RATE // 2000) * CHANNELS  # keep 0.5 ms
    return pcm[start:]


class Sound:
    """Round-robin over the recorded variants of one kit piece."""

    def __init__(self, name):
        files = sorted(glob.glob(os.path.join(KIT_DIR, f"{name}_*.wav")))
        if not files:
            sys.exit(f"No samples for '{name}' in {KIT_DIR}. Run fetch_samples.py first.")
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

    def trigger(self, samples):
        with self.lock:
            self.voices.append([samples, 0])
            if len(self.voices) > self.MAX_VOICES:
                self.voices.pop(0)

    def run(self, out):
        n = BLOCK * CHANNELS
        while True:
            with self.lock:
                voices = list(self.voices)
            mix = [0] * n
            for v in voices:
                chunk = v[0][v[1]:v[1] + n]
                if len(chunk) == n:
                    mix = [a + b for a, b in zip(mix, chunk)]
                else:
                    for i, s in enumerate(chunk):
                        mix[i] += s
                v[1] += n
            with self.lock:
                self.voices = [v for v in self.voices if v[1] < len(v[0])]
            buf = array.array("h", (32767 if s > 32767 else -32767 if s < -32767 else s
                                    for s in mix))
            out.write(buf)  # blocks when the small ALSA buffer is full


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

    def write(self, buf):
        ptr, n = buf.buffer_info()
        frames = n // CHANNELS
        while frames > 0:
            r = self.lib.snd_pcm_writei(self.pcm, ctypes.c_void_p(ptr), frames)
            if r < 0:  # underrun etc.: recover and carry on
                self._check(self.lib.snd_pcm_recover(self.pcm, r, 1))
                continue
            ptr += r * CHANNELS * 2
            frames -= r

    def close(self):
        self.lib.snd_pcm_close(self.pcm)


def main():
    dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/input/js0"
    print("Loading kit...", flush=True)
    sounds = {name: Sound(name) for _, name in PADS.values()}

    out = AlsaOut()
    mixer = Mixer()
    threading.Thread(target=mixer.run, args=(out,), daemon=True).start()

    fd = os.open(dev, os.O_RDONLY)
    print(f"Listening on {dev}. Hit the pads! Ctrl+C to quit.", flush=True)
    try:
        while True:
            select.select([fd], [], [])
            _, value, etype, num = struct.unpack("IhBB", os.read(fd, 8))
            if etype == 1 and value == 1 and num in PADS:  # button press, not init
                pad, sound = PADS[num]
                mixer.trigger(sounds[sound].pick())
                print(f"{pad:>6} -> {sound}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)
        out.close()


if __name__ == "__main__":
    main()
