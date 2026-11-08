"""Size text to fit, using the real glyph widths of the bundled fonts (stdlib TrueType reader).

Measuring in Python instead of the browser keeps every render identical and avoids races with font loading.
"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

FONTS = Path(__file__).resolve().parent / "assets" / "fonts"
FACES = {"px": "Silkscreen-Bold.ttf", "term": "VT323-Regular.ttf", "gothic": "PathwayGothicOne-Regular.ttf",
         "serif": "InstrumentSerif-Italic.ttf", "mono": "IBMPlexMono-Medium.ttf"}


@lru_cache(maxsize=None)
def _metrics(name: str) -> tuple[dict[int, float], float]:
    """(codepoint -> advance in em, default advance) for one bundled font."""
    data = (FONTS / name).read_bytes()
    num = struct.unpack(">H", data[4:6])[0]
    tables = {}
    for i in range(num):
        tag, _, off, length = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        tables[tag.decode("latin-1")] = (off, length)
    upm = struct.unpack(">H", data[tables["head"][0] + 18: tables["head"][0] + 20])[0]
    n_hm = struct.unpack(">H", data[tables["hhea"][0] + 34: tables["hhea"][0] + 36])[0]
    hmtx = tables["hmtx"][0]
    adv = [struct.unpack(">H", data[hmtx + 4 * i: hmtx + 4 * i + 2])[0] for i in range(n_hm)]
    cmap = tables["cmap"][0]
    gid: dict[int, int] = {}
    for i in range(struct.unpack(">H", data[cmap + 2: cmap + 4])[0]):
        pid, eid, off = struct.unpack(">HHI", data[cmap + 4 + 8 * i: cmap + 12 + 8 * i])
        sub = cmap + off
        if struct.unpack(">H", data[sub: sub + 2])[0] != 4 or (pid, eid) not in ((3, 1), (0, 3), (0, 4), (0, 1)):
            continue
        seg = struct.unpack(">H", data[sub + 6: sub + 8])[0] // 2
        ends = struct.unpack(f">{seg}H", data[sub + 14: sub + 14 + 2 * seg])
        p = sub + 16 + 2 * seg
        starts = struct.unpack(f">{seg}H", data[p: p + 2 * seg])
        deltas = struct.unpack(f">{seg}h", data[p + 2 * seg: p + 4 * seg])
        ro_at = p + 4 * seg
        ros = struct.unpack(f">{seg}H", data[ro_at: ro_at + 2 * seg])
        for k in range(seg):
            for c in range(starts[k], min(ends[k], 0x2FFF) + 1):
                if ros[k] == 0:
                    g = (c + deltas[k]) & 0xFFFF
                else:
                    a = ro_at + 2 * k + ros[k] + 2 * (c - starts[k])
                    g = struct.unpack(">H", data[a: a + 2])[0]
                    g = (g + deltas[k]) & 0xFFFF if g else 0
                if g:
                    gid[c] = g
        break
    widths = {c: adv[min(g, n_hm - 1)] / upm for c, g in gid.items()}
    default = widths.get(ord("M"), 0.6)
    return widths, default


def width(text: str, face: str, size: float, tracking: float = 0.0) -> float:
    """Rendered width in px of one line of text."""
    w, d = _metrics(FACES[face])
    return sum(w.get(ord(c), d) for c in text) * size + tracking * size * len(text)


def wrap(text: str, face: str, size: float, box: float) -> list[str] | None:
    """Greedy word wrap; None if a single word is wider than the box."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if width(trial, face, size) <= box:
            cur = trial
            continue
        if width(word, face, size) > box:
            return None
        lines.append(cur)
        cur = word
    if cur:
        lines.append(cur)
    return lines


def fit(text: str, face: str, max_size: int, box: float, lines: int = 1, min_size: int = 28) -> int:
    """Largest font size (step 2px) at which text fits the box in at most `lines` lines, never splitting a word."""
    size = max_size
    while size > min_size:
        got = wrap(text, face, size, box)
        if got is not None and len(got) <= lines:
            return size
        size -= 2
    return min_size
