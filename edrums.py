#!/usr/bin/env python3
"""Low-latency drum module for the Rock Band 1 Wii drum kit (1bad:0005).

Reads pad hits from the Linux joystick device, plays acoustic drum
samples and writes the mix straight to ALSA via ctypes.

Usage: python3 edrums.py [-d ALSA_DEVICE] [/dev/input/jsN]

  -d plughw:Headset   play on a specific sound card (see `aplay -l`);
                      also settable via EDRUMS_AUDIO. Default: ALSA "default".
"""

import argparse
import array
import ctypes
import glob
import math
import os
import queue
import select
import struct
import sys
import threading
import time
import warnings
import wave

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import audioop
    except ImportError:
        sys.exit(
            "Needs audioop. On Python 3.13+ run: "
            "python3 -m pip install audioop-lts --break-system-packages"
        )

RATE = 48000
CHANNELS = 2
BLOCK = 64
LATENCY_US = 4000

KIT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "kit"
)

os.environ.setdefault("PIPEWIRE_LATENCY", f"{BLOCK}/{RATE}")

PADS = {
    2: ("red", "snare"),
    3: ("yellow", "hihat"),
    0: ("blue", "tom"),
    1: ("green", "cymbal"),
    4: ("kick", "kick"),
}

KITS = {
    (5, -32767): "studio",
    (4, -32767): "jazz",
    (4, 32767): "rock",
    (5, 32767): "lofi",
}

DEFAULT_KIT = "studio"

VOLUME_BUTTONS = {9: +3, 8: -3}
VOLUME_RANGE_DB = (-30, 6)

INITIAL_VOLUME_DB = 0

# Soft limiter threshold, as a fraction of full-scale PCM.
LIMITER_THRESHOLD = 0.90


def load_wav(path):
    with wave.open(path) as w:
        assert w.getframerate() == RATE
        assert w.getnchannels() == CHANNELS
        pcm = array.array("h", w.readframes(w.getnframes()))

    peak = max(map(abs, pcm))
    onset = next(
        i for i, sample in enumerate(pcm)
        if abs(sample) > peak * 0.05
    )

    start = max(
        0,
        onset // CHANNELS - RATE // 2000
    ) * CHANNELS

    return pcm[start:].tobytes()


class Sound:
    """Round-robin playback of recorded sample variants."""

    def __init__(self, kit, name):
        files = sorted(
            glob.glob(
                os.path.join(KIT_DIR, kit, f"{name}_*.wav")
            )
        )

        if not files:
            sys.exit(
                f"No samples for '{kit}/{name}' in {KIT_DIR}. "
                "Run fetch_samples.py first."
            )

        self.variants = [load_wav(f) for f in files]
        self.next = 0

    def pick(self):
        sample = self.variants[self.next]
        self.next = (self.next + 1) % len(self.variants)
        return sample


class Mixer:
    MAX_VOICES = 16

    def __init__(self):
        self.voices = []  # [samples, byte position, hit time, label]
        self.lock = threading.Lock()
        self.gain = 10 ** (INITIAL_VOLUME_DB / 20)
        # (label, latency in s) per hit, printed by the main program so the
        # audio thread never blocks on console output.
        self.latencies = queue.SimpleQueue()

    def set_volume(self, volume_db):
        self.gain = 10 ** (volume_db / 20)

    def trigger(self, samples, hit_time, label):
        with self.lock:
            self.voices.append([samples, 0, hit_time, label])

            if len(self.voices) > self.MAX_VOICES:
                self.voices.pop(0)

    def run(self, out):
        n = BLOCK * CHANNELS * 2
        silence = bytes(n)

        while True:
            with self.lock:
                voices = list(self.voices)

            mix = silence
            gain = self.gain

            started = []

            for voice in voices:
                samples, position = voice[0], voice[1]

                if position == 0:
                    started.append(voice)

                chunk = samples[position:position + n]

                if len(chunk) < n:
                    chunk += silence[len(chunk):]

                # Apply master gain to each voice before summing.
                if gain != 1.0:
                    chunk = audioop.mul(chunk, 2, gain)

                # Sum voices using signed 16-bit PCM.
                mix = audioop.add(mix, chunk, 2)

                voice[1] += n

            # Remove finished voices.
            with self.lock:
                self.voices = [
                    voice for voice in self.voices
                    if voice[1] < len(voice[0])
                ]

            # Final soft limiter: prevent harsh clipping from overlapping hits.
            mix = self.soft_limit(mix)

            out.write(mix)

            # Latency per hit: the block just written starts playing once
            # everything queued ahead of it has been played.
            if started:
                ahead = out.delay_frames() - BLOCK
                plays_at = time.monotonic() + max(ahead, 0) / RATE
                for voice in started:
                    self.latencies.put((voice[3], plays_at - voice[2]))

    @staticmethod
    def soft_limit(data):
        """Apply a smooth limiter to signed 16-bit PCM."""
        pcm = array.array("h")
        pcm.frombytes(data)

        threshold = int(32767 * LIMITER_THRESHOLD)

        for i, sample in enumerate(pcm):
            magnitude = abs(sample)

            if magnitude > threshold:
                excess = magnitude - threshold

                # Smoothly compress the signal above the threshold.
                compressed = threshold + (
                    (32767 - threshold)
                    * math.tanh(excess / (32767 - threshold))
                )

                pcm[i] = int(
                    math.copysign(compressed, sample)
                )

        return pcm.tobytes()


class AlsaOut:
    SND_PCM_STREAM_PLAYBACK = 0
    SND_PCM_FORMAT_S16_LE = 2
    SND_PCM_ACCESS_RW_INTERLEAVED = 3

    def __init__(self, device=b"default"):
        self.lib = ctypes.CDLL("libasound.so.2")

        self.lib.snd_strerror.restype = ctypes.c_char_p

        self.pcm = ctypes.c_void_p()

        self._check(
            self.lib.snd_pcm_open(
                ctypes.byref(self.pcm),
                device,
                self.SND_PCM_STREAM_PLAYBACK,
                0
            )
        )

        buffer_frames, period_frames = self._set_params(LATENCY_US)

        # Some drivers (e.g. the Raspberry Pi's bcm2835 headphone jack) have a
        # minimum period of 480 frames (10 ms), so a 4 ms request yields one
        # period == whole buffer: the card drains it completely before asking
        # for more and underruns nonstop (crackly, distorted playback).
        # Grow the buffer to at least two periods in that case.
        if buffer_frames < 2 * period_frames:
            latency_us = 2 * period_frames * 1_000_000 // RATE + 1000
            buffer_frames, period_frames = self._set_params(latency_us)

        print(
            f"ALSA buffer: {buffer_frames} frames "
            f"({buffer_frames * 1000 / RATE:.1f} ms), "
            f"period {period_frames}",
            flush=True
        )

    def _set_params(self, latency_us):
        self._check(
            self.lib.snd_pcm_set_params(
                self.pcm,
                self.SND_PCM_FORMAT_S16_LE,
                self.SND_PCM_ACCESS_RW_INTERLEAVED,
                CHANNELS,
                RATE,
                1,
                latency_us
            )
        )
        buffer_frames = ctypes.c_ulong()
        period_frames = ctypes.c_ulong()
        self._check(
            self.lib.snd_pcm_get_params(
                self.pcm,
                ctypes.byref(buffer_frames),
                ctypes.byref(period_frames)
            )
        )
        return buffer_frames.value, period_frames.value

    def _check(self, err):
        if err < 0:
            raise RuntimeError(
                f"ALSA: {self.lib.snd_strerror(err).decode()}"
            )

    def write(self, data):
        frame_bytes = CHANNELS * 2

        while data:
            r = self.lib.snd_pcm_writei(
                self.pcm,
                data,
                len(data) // frame_bytes
            )

            if r < 0:
                self._check(
                    self.lib.snd_pcm_recover(self.pcm, r, 1)
                )
                continue

            if r == 0:
                continue

            data = data[r * frame_bytes:]

    def delay_frames(self):
        """Frames queued until a newly written sample reaches the output."""
        frames = ctypes.c_long()
        if self.lib.snd_pcm_delay(self.pcm, ctypes.byref(frames)) < 0:
            return 0
        return frames.value

    def close(self):
        self.lib.snd_pcm_close(self.pcm)


def print_hits(latencies):
    """Print one line per hit with its latency and running statistics."""
    history = []

    while True:
        label, latency = latencies.get()
        ms = latency * 1000
        history = (history + [ms])[-50:]

        print(
            f"{label:<22} {ms:5.1f} ms   "
            f"(last {len(history)}: avg {sum(history) / len(history):.1f}, "
            f"min {min(history):.1f}, max {max(history):.1f})",
            flush=True
        )


def main():
    parser = argparse.ArgumentParser(description="Rock Band drum kit e-drums")
    parser.add_argument(
        "joystick", nargs="?", default="/dev/input/js0",
        help="joystick device (default: /dev/input/js0)"
    )
    parser.add_argument(
        "-d", "--device", default=os.environ.get("EDRUMS_AUDIO", "default"),
        help='ALSA output device, e.g. "plughw:Headset" for a USB '
             'sound card (see `aplay -l`); env EDRUMS_AUDIO; '
             'default: "default"'
    )
    args = parser.parse_args()
    dev = args.joystick

    print("Loading kits...", flush=True)

    kits = {
        kit: {
            slot: Sound(kit, slot)
            for _, slot in PADS.values()
        }
        for kit in KITS.values()
    }

    kit = DEFAULT_KIT
    volume_db = INITIAL_VOLUME_DB

    print(f"Audio device: {args.device}", flush=True)
    out = AlsaOut(args.device.encode())
    mixer = Mixer()
    mixer.set_volume(volume_db)

    threading.Thread(
        target=mixer.run,
        args=(out,),
        daemon=True
    ).start()

    threading.Thread(
        target=print_hits,
        args=(mixer.latencies,),
        daemon=True
    ).start()

    fd = os.open(dev, os.O_RDONLY)

    print(
        f"Listening on {dev}. Kit: {kit}. "
        "D-pad switches kits "
        "(up studio, left jazz, right rock, down lofi), "
        "+/- volume. Ctrl+C to quit.\n"
        "Latency per hit = event read -> sound at the audio output; "
        "the drum kit's USB polling comes on top "
        "(10 ms by default, 1 ms with usbhid.jspoll=1).",
        flush=True
    )

    try:
        while True:
            select.select([fd], [], [])

            data = os.read(fd, 8)
            hit_time = time.monotonic()

            _, value, etype, num = struct.unpack("IhBB", data)

            if etype == 1 and value == 1 and num in PADS:
                pad, slot = PADS[num]

                mixer.trigger(
                    kits[kit][slot].pick(),
                    hit_time,
                    f"{pad:>6} -> {kit}/{slot}"
                )

            elif (
                etype == 1
                and value == 1
                and num in VOLUME_BUTTONS
            ):
                lo, hi = VOLUME_RANGE_DB

                volume_db = max(
                    lo,
                    min(
                        hi,
                        volume_db + VOLUME_BUTTONS[num]
                    )
                )

                mixer.set_volume(volume_db)

                print(
                    f"=== volume: {volume_db:+d} dB ===",
                    flush=True
                )

            elif etype == 2 and (num, value) in KITS:
                kit = KITS[(num, value)]

                print(
                    f"=== kit: {kit} ===",
                    flush=True
                )

    except KeyboardInterrupt:
        pass

    finally:
        os.close(fd)
        out.close()


if __name__ == "__main__":
    main()
