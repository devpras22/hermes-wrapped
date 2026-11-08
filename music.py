"""A soundtrack for your week, synthesized in pure Python (stdlib only).

No bundled audio, no downloads, no licences. The mood comes from the week itself:
  night   late-night weeks: slow lo-fi, dusty keys, half-time drums, vinyl crackle
  dawn    early-morning weeks: no drums, warm pads, a bell melody, serene
  upbeat  sprints and big pushes: four-on-the-floor, claps, a plucky arpeggio
  steady  everything else: mellow lo-fi with a light groove
Key, chords, tempo and melody are seeded from the week, so every Wrapped sounds different,
and the same week always sounds the same.
"""
from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

RATE = 22050

QUAL = {  # chord shapes (semitones above the root)
    "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "dom7": [0, 4, 7, 10], "sus": [0, 5, 7, 10],
    "add9": [0, 4, 7, 14], "madd9": [0, 3, 7, 14], "maj": [0, 4, 7, 12], "min": [0, 3, 7, 12],
}

MOODS = {
    "night": {
        "bpm": (70, 80), "keys": [45, 46, 48, 50], "swing": 0.12, "drums": "halftime", "crackle": 0.010,
        "lowpass": 0.07, "pad": 0.020, "keys_vol": 0.10, "bell": 0.0, "arp": 0.0, "melody": 0.06,
        "progs": [[(0, "m7"), (8, "maj7"), (3, "maj7"), (10, "dom7")],
                  [(0, "m7"), (5, "m7"), (10, "dom7"), (3, "maj7")],
                  [(0, "madd9"), (10, "add9"), (8, "maj7"), (7, "m7")]],
    },
    "dawn": {
        "bpm": (64, 72), "keys": [50, 52, 53, 55], "swing": 0.0, "drums": "none", "crackle": 0.0,
        "lowpass": 0.10, "pad": 0.024, "keys_vol": 0.0, "bell": 0.11, "arp": 0.0, "melody": 0.0,
        "progs": [[(0, "maj7"), (5, "maj7"), (9, "m7"), (7, "sus")],
                  [(0, "add9"), (7, "sus"), (9, "m7"), (5, "maj7")],
                  [(5, "maj7"), (0, "add9"), (2, "m7"), (7, "sus")]],
    },
    "upbeat": {
        "bpm": (114, 124), "keys": [48, 50, 52, 53], "swing": 0.0, "drums": "four", "crackle": 0.0,
        "lowpass": 0.22, "pad": 0.014, "keys_vol": 0.0, "bell": 0.0, "arp": 0.07, "melody": 0.0,
        "progs": [[(0, "add9"), (7, "maj"), (9, "min"), (5, "add9")],
                  [(9, "min"), (5, "maj"), (0, "add9"), (7, "maj")],
                  [(0, "maj"), (9, "min"), (5, "maj"), (7, "sus")]],
    },
    "steady": {
        "bpm": (84, 94), "keys": [48, 50, 53, 55], "swing": 0.08, "drums": "lofi", "crackle": 0.005,
        "lowpass": 0.10, "pad": 0.018, "keys_vol": 0.09, "bell": 0.0, "arp": 0.0, "melody": 0.05,
        "progs": [[(0, "maj7"), (9, "m7"), (2, "m7"), (7, "dom7")],
                  [(5, "maj7"), (4, "m7"), (2, "m7"), (0, "maj7")],
                  [(0, "maj7"), (5, "maj7"), (9, "m7"), (7, "sus")]],
    },
}


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_music(path: Path, seconds: float, seed: str = "", duck: list[tuple[float, float]] | None = None,
               mood: str = "steady") -> Path:
    """Write a mono 16-bit WAV of `seconds` length in the given mood."""
    m = MOODS.get(mood, MOODS["steady"])
    rng = random.Random(f"{seed}|{mood}")
    key = rng.choice(m["keys"])
    prog = rng.choice(m["progs"])
    bpm = rng.randint(*m["bpm"])
    beat = 60.0 / bpm
    bar = beat * 4
    n = int(seconds * RATE)
    out = [0.0] * n
    bars = int(math.ceil(seconds / bar))
    TWO_PI = 2 * math.pi

    def add_tone(t0: float, dur: float, f: float, vol: float, attack: float, decay: float | None,
                 shape: str = "sine", trem: float = 0.0) -> None:
        start = int(t0 * RATE)
        length = int(dur * RATE)
        w = TWO_PI * f / RATE
        a = max(1, int(attack * RATE))
        rel = max(1, int(0.12 * RATE))
        for i in range(length):
            j = start + i
            if j >= n:
                break
            if j < 0:
                continue
            ph = w * i
            if shape == "sine":
                v = math.sin(ph)
            elif shape == "keys":  # e-piano-ish: fundamental + soft bell partial
                v = math.sin(ph) + 0.35 * math.sin(2 * ph) * math.exp(-i / (0.25 * RATE))
            else:  # saw
                v = 2 * ((f * i / RATE) % 1.0) - 1
            env = min(1.0, i / a, (length - i) / rel)
            if decay:
                env *= math.exp(-i / (decay * RATE))
            if trem:
                env *= 1 - trem * (0.5 + 0.5 * math.sin(TWO_PI * 4.5 * i / RATE))
            out[j] += v * vol * env

    chords = [(key + root, QUAL[q]) for root, q in prog]

    # --- pad: detuned saws (lowpassed below) on every chord
    pad = [0.0] * n
    for b in range(bars):
        root, shape = chords[b % len(chords)]
        t0 = int(b * bar * RATE)
        length = int(bar * RATE)
        for iv in shape:
            for det in (-0.07, 0.06):
                f = _hz(root + iv + det)
                period = RATE / f
                for i in range(length):
                    j = t0 + i
                    if j >= n:
                        break
                    env = min(1.0, i / (0.45 * RATE), (length - i) / (0.3 * RATE))
                    pad[j] += (2 * ((i % period) / period) - 1) * m["pad"] * env
    a = m["lowpass"]
    y = 0.0
    for i in range(n):
        y += a * (pad[i] - y)
        out[i] += y

    # --- keys: chord stabs, swung, with tremolo (night / steady)
    if m["keys_vol"]:
        for b in range(bars):
            root, shape = chords[b % len(chords)]
            for hit in (0.0, 1.5 + m["swing"], 2.5 + m["swing"]):
                if hit and rng.random() < 0.25:
                    continue
                for k, iv in enumerate(shape):
                    add_tone(b * bar + hit * beat + k * 0.012, beat * 1.4, _hz(root + iv + 12),
                             m["keys_vol"] / len(shape), 0.01, 0.9, "keys", trem=0.25)

    # --- bell melody (dawn): slow pentatonic arpeggio, an octave up
    if m["bell"]:
        for b in range(bars):
            root, shape = chords[b % len(chords)]
            tones = [root + iv + 24 for iv in shape]
            for k in range(4):
                if rng.random() < 0.2:
                    continue
                add_tone(b * bar + k * beat, beat * 2.2, _hz(rng.choice(tones)), m["bell"], 0.004, 0.7)
                add_tone(b * bar + k * beat, beat * 2.2, _hz(rng.choice(tones) + 12) * 1.003, m["bell"] * 0.25, 0.004, 0.4)

    # --- arpeggio (upbeat): 16th-note plucks over the chord
    if m["arp"]:
        for b in range(bars):
            if b == 0:
                continue
            root, shape = chords[b % len(chords)]
            tones = [root + iv + 12 for iv in shape] + [root + shape[1] + 24]
            pattern = rng.sample(range(len(tones)), len(tones))
            for k in range(16):
                f = _hz(tones[pattern[k % len(pattern)]])
                add_tone(b * bar + k * beat / 4, beat / 4, f, m["arp"], 0.003, 0.09, "saw")

    # --- melody (night / steady): a sparse, lazy line on chord tones
    if m["melody"]:
        for b in range(1, bars):
            root, shape = chords[b % len(chords)]
            tones = [root + iv + 24 for iv in shape]
            t = 0.0
            while t < 4:
                step = rng.choice([1, 1.5, 2, 0.5])
                if rng.random() < 0.6:
                    add_tone(b * bar + (t + (m["swing"] if t % 1 else 0)) * beat, beat * step * 0.95,
                             _hz(rng.choice(tones)), m["melody"], 0.02, 1.2, "sine", trem=0.1)
                t += step

    # --- bass
    for b in range(bars):
        root, _ = chords[b % len(chords)]
        f = _hz(root - 12 if root - 12 >= 33 else root)
        hits = {"four": [0.5, 1.5, 2.5, 3.5], "halftime": [0, 2.5], "lofi": [0, 1.5, 2], "none": [0]}[m["drums"]]
        for h in hits:
            length = beat * (3.8 if m["drums"] == "none" else 0.9)
            add_tone(b * bar + h * beat, length, f, 0.18, 0.01, None if m["drums"] == "none" else 0.35)

    # --- drums (start after the first bar)
    noise = random.Random(seed + "drums")

    def kick(t: float, vol: float = 0.34) -> None:
        j0 = int(t * RATE)
        for i in range(int(0.25 * RATE)):
            j = j0 + i
            if j >= n:
                return
            f = 48 + 100 * math.exp(-i / (0.025 * RATE))
            out[j] += math.sin(TWO_PI * f * i / RATE) * vol * math.exp(-i / (0.08 * RATE))

    def hiss(t: float, dur: float, vol: float, bright: bool = True) -> None:
        j0 = int(t * RATE)
        prev = 0.0
        for i in range(int(dur * RATE)):
            j = j0 + i
            if j >= n:
                return
            s = noise.uniform(-1, 1)
            v = (s - prev) if bright else (s + prev) * 0.5
            prev = s
            out[j] += v * vol * math.exp(-i / (dur * 0.3 * RATE))

    beats = int(seconds / beat)
    for k in range(4, beats):
        t = k * beat
        style = m["drums"]
        if style == "four":
            kick(t)
            if k % 2:
                hiss(t, 0.12, 0.12, bright=False)          # clap
            hiss(t + beat / 2, 0.06, 0.05)                  # offbeat hat
        elif style == "halftime":
            if k % 4 == 0:
                kick(t, 0.3)
            if k % 4 == 2:
                hiss(t, 0.16, 0.09, bright=False)          # soft snare
            hiss(t + (0.5 + m["swing"]) * beat, 0.04, 0.025)
        elif style == "lofi":
            if k % 2 == 0:
                kick(t, 0.28)
            else:
                hiss(t, 0.12, 0.07, bright=False)
            hiss(t + (0.5 + m["swing"]) * beat, 0.04, 0.03)
        elif style == "none" and k % 2 == 0:
            hiss(t, 0.05, 0.012)                            # faint shaker

    # --- vinyl crackle
    if m["crackle"]:
        cr = random.Random(seed + "crackle")
        for i in range(n):
            if cr.random() < 0.0006:
                out[i] += cr.uniform(-1, 1) * m["crackle"] * 8
            out[i] += cr.uniform(-1, 1) * m["crackle"] * 0.15

    # --- echo, then master: normalize, fade in/out, duck under any narration
    d = int(beat * 0.75 * RATE)
    for i in range(d, n):
        out[i] += out[i - d] * 0.2
    fade_in, fade_out = 0.8 * RATE, 2.0 * RATE
    windows = [(int(s * RATE), int(e * RATE)) for s, e in (duck or [])]
    ramp = int(0.15 * RATE)
    # Every mood lands at the same loudness: scale to a target RMS, then soft-clip the peaks.
    rms = math.sqrt(sum(v * v for v in out) / max(1, n)) or 1e-9
    gain = 0.13 / rms
    for i in range(n):
        g = min(1.0, i / fade_in, (n - i) / fade_out)
        for s, e in windows:
            if s - ramp <= i <= e + ramp:
                g *= 1 - 0.65 * max(0.0, min(1.0, (i - (s - ramp)) / ramp, ((e + ramp) - i) / ramp))
        out[i] = math.tanh(out[i] * gain) * 0.95 * g

    path = Path(path)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v)) * 32767)) for v in out))
    return path


def mood_for(archetype: str, peak_hour: int, night_share: float) -> str:
    """Pick the soundtrack's mood from the week."""
    if archetype in ("The Night Shift Manager", "The 2am Debugger") or night_share >= 0.25 or peak_hour >= 22 or peak_hour < 4:
        return "night"
    if 4 <= peak_hour <= 9:
        return "dawn"
    if archetype in ("The Sprinter", "The Marathoner", "The Big Spender", "The Delegator"):
        return "upbeat"
    return "steady"


if __name__ == "__main__":
    import sys
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    for md in MOODS:
        print(make_music(out_dir / f"music-{md}.wav", 30, seed="demo", mood=md))
