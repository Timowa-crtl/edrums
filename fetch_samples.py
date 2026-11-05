#!/usr/bin/env python3
"""One-time setup: download a few hits from the CC0 "Virtuosity Drums" kit
(github.com/sfzinstruments/virtuosity_drums), mix the mic positions down
to stereo and save ready-to-play 48 kHz 16-bit WAVs into ./kit/<kit name>/.

Needs libsndfile (installed on Ubuntu by default) to decode FLAC.
"""
import array
import ctypes
import math
import os
import sys
import urllib.parse
import urllib.request
import wave

BASE = "https://raw.githubusercontent.com/sfzinstruments/virtuosity_drums/master/Samples"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "kit", "flac")  # raw downloads
OUT = os.path.join(HERE, "kit")
RATE = 48000

# Each kit fills the five pad slots used by edrums.py (snare, hihat, tom,
# cymbal, kick). Per kit:
#   mics:      mic positions mixed into every hit, like a real drum mix
#   pieces:    slot -> (sample dir, file stem without mic prefix, variants,
#              level, max seconds)
#   overrides: per-slot mic gain overrides
#   drive:     per-slot soft-clip drive: >1 raises perceived loudness at the
#              same peak
STUDIO_PIECES = {
    "kick":   ("kick", "kick_snon_vl3_rr{}", range(1, 5), 0.95, 1.5),
    "snare":  ("snare", "snare_center_vl{}", range(26, 30), 0.95, 1.5),
    "hihat":  ("hh", "hh_closed_vl3_rr{}", range(1, 5), 0.55, 1.0),
    "tom":    ("htom", "htom_center_vl{}", range(11, 15), 0.85, 2.0),
    "cymbal": ("crash", "crash_crash_vl2_rr{}", range(1, 5), 0.7, 4.0),
}

KITS = {
    "studio": {
        "mics": {"kickmic": 0.8, "snaremic": 0.8, "oh": 1.0, "room": 0.5},
        "pieces": STUDIO_PIECES,
        "overrides": {"kick": {"kickmic": 1.4}},
        "drive": {"kick": 3.0},
    },
    "jazz": {
        "mics": {"kickmic": 0.5, "snaremic": 0.6, "oh": 1.0, "room": 0.7},
        "pieces": {
            "kick":   ("kick", "kick_snoff_vl2_rr{}", range(1, 5), 0.85, 1.5),
            "snare":  ("snare", "snare_center_vl{}", range(14, 18), 0.8, 1.5),
            "hihat":  ("hh", "hh_closed_vl2_rr{}", range(1, 5), 0.5, 1.0),
            "tom":    ("htom", "htom_offcenter_vl{}", range(6, 10), 0.75, 2.0),
            "cymbal": ("ride", "ride_ride_vl2_rr{}", range(1, 5), 0.65, 4.0),
        },
        "overrides": {},
        "drive": {},
    },
    "rock": {
        "mics": {"kickmic": 1.0, "snaremic": 1.0, "oh": 1.0, "room": 1.0},
        "pieces": {
            "kick":   ("kick", "kick_snon_vl4_rr{}", range(1, 5), 0.95, 1.5),
            "snare":  ("snare", "snare_rimshot_vl{}", range(9, 13), 0.95, 1.5),
            "hihat":  ("hh", "hh_half_vl4_rr{}", range(1, 5), 0.3, 1.5),
            "tom":    ("ltom", "ltom_center_vl{}", range(13, 17), 0.9, 2.5),
            "cymbal": ("crash", "crash_crash_vl3_rr{}", range(1, 5), 0.75, 4.0),
        },
        "overrides": {"kick": {"kickmic": 1.6}},
        "drive": {"kick": 3.5, "snare": 2.0},
    },
    "lofi": {
        "mics": {"lofi": 1.0},
        "pieces": STUDIO_PIECES,
        "overrides": {},
        "drive": {"kick": 2.0},
    },
}


class SF_INFO(ctypes.Structure):
    _fields_ = [("frames", ctypes.c_int64), ("samplerate", ctypes.c_int),
                ("channels", ctypes.c_int), ("format", ctypes.c_int),
                ("sections", ctypes.c_int), ("seekable", ctypes.c_int)]


sndfile = ctypes.CDLL("libsndfile.so.1")
sndfile.sf_open.restype = ctypes.c_void_p
sndfile.sf_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(SF_INFO)]
sndfile.sf_readf_float.restype = ctypes.c_int64
sndfile.sf_readf_float.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int64]
sndfile.sf_close.argtypes = [ctypes.c_void_p]


def read_stereo(path):
    """Decode an audio file to (left, right) float lists at RATE."""
    info = SF_INFO()
    f = sndfile.sf_open(path.encode(), 0x10, ctypes.byref(info))  # SFM_READ
    if not f:
        raise RuntimeError(f"cannot decode {path}")
    buf = (ctypes.c_float * (info.frames * info.channels))()
    n = sndfile.sf_readf_float(f, buf, info.frames)
    sndfile.sf_close(f)
    ch = info.channels
    left = list(buf[0:n * ch:ch])
    right = list(buf[1:n * ch:ch]) if ch > 1 else left
    if info.samplerate != RATE:
        left, right = resample(left, info.samplerate), resample(right, info.samplerate)
    return left, right


def resample(x, src):
    """Linear-interpolation resample to RATE (fine for a POC)."""
    step = src / RATE
    out, n = [], len(x) - 1
    for i in range(int(n / step)):
        p = i * step
        j = int(p)
        out.append(x[j] + (x[j + 1] - x[j]) * (p - j))
    return out


def download(mic, piece_dir, stem):
    name = f"{mic}_{stem}.flac"
    dest = os.path.join(CACHE, name)
    if not os.path.exists(dest):
        url = f"{BASE}/{mic}/{piece_dir}/{urllib.parse.quote(name)}"
        urllib.request.urlretrieve(url, dest + ".part")
        os.rename(dest + ".part", dest)
    return dest


def build_kit(kit, cfg):
    out_dir = os.path.join(OUT, kit)
    os.makedirs(out_dir, exist_ok=True)
    for piece, (piece_dir, pattern, variants, level, max_s) in cfg["pieces"].items():
        mixes = []
        for v in variants:
            stem = pattern.format(v)
            L, R = [], []
            for mic, gain in {**cfg["mics"], **cfg["overrides"].get(piece, {})}.items():
                l, r = read_stereo(download(mic, piece_dir, stem))
                if len(l) > len(L):
                    L += [0.0] * (len(l) - len(L))
                    R += [0.0] * (len(r) - len(R))
                for i in range(len(l)):
                    L[i] += l[i] * gain
                    R[i] += r[i] * gain
            # Trim to max length with a short fade-out.
            n = min(len(L), int(max_s * RATE))
            fade = min(n, int(0.3 * RATE))
            L, R = L[:n], R[:n]
            for i in range(fade):
                g = 1 - i / fade
                L[n - fade + i] *= g
                R[n - fade + i] *= g
            mixes.append((stem, L, R))
            print(f"  {kit}/{piece}: {stem} ({n / RATE:.2f}s)", flush=True)

        # Normalize all variants of a piece together so they stay consistent.
        peak = max(max(map(abs, L + R)) for _, L, R in mixes) or 1.0
        drive = cfg["drive"].get(piece, 0)
        if drive:
            shape = lambda s: math.tanh(drive * s / peak) / math.tanh(drive)
        else:
            shape = lambda s: s / peak
        for idx, (_, L, R) in enumerate(mixes, 1):
            k = level * 32767
            pcm = array.array("h", (int(shape(s) * k) for pair in zip(L, R) for s in pair))
            with wave.open(os.path.join(out_dir, f"{piece}_{idx}.wav"), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(pcm.tobytes())


def main():
    """Build all kits, or only the ones named on the command line."""
    os.makedirs(CACHE, exist_ok=True)
    names = sys.argv[1:] or list(KITS)
    for kit in names:
        if kit not in KITS:
            sys.exit(f"Unknown kit '{kit}'. Choose from: {', '.join(KITS)}")
        build_kit(kit, KITS[kit])
    print(f"Done. Kits written to {OUT}")


if __name__ == "__main__":
    main()
