"""Strip secrets and private paths before anything is drawn into a video."""

from __future__ import annotations

import os
import re
import unicodedata

_HOME = os.path.expanduser("~")

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Provider / platform keys
    (re.compile(r"\b(sk-(?:ant-|proj-|or-)?[A-Za-z0-9_\-]{12,})"), "sk-•••"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})"), "gh_•••"),
    (re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})"), "gh_•••"),
    (re.compile(r"\b(xox[abposr]-[A-Za-z0-9\-]{10,})"), "xox-•••"),
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), "AKIA•••"),
    (re.compile(r"\b([rsp]k_(?:live|test)_[A-Za-z0-9]{10,})"), "key•••"),  # Stripe-style keys
    (re.compile(r"\b(AIza[0-9A-Za-z_\-]{30,})"), "AIza•••"),
    (re.compile(r"\b(\d{8,12}:[A-Za-z0-9_\-]{30,})"), "bot-token•••"),  # Telegram bot tokens
    (re.compile(r"\b(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"), "jwt•••"),
    # NAME=value / "token": "value" style assignments
    (
        re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|AUTH)[A-Z0-9_]*)\s*([=:])\s*(\"?)[^\s\"']{4,}\3"),
        r"\1\2\3•••\3",
    ),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1•••"),
    # Credentials inside URLs
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1•••@"),
    # Long opaque strings (hex / base64-ish) that look like secrets
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), "•••"),
    (re.compile(r"\b[A-Za-z0-9+/_\-]{48,}={0,2}"), "•••"),
    # Email addresses, then user@host logins
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "•••@•••"),
    (re.compile(r"\b[A-Za-z0-9._\-]+@[A-Za-z0-9.\-]+\b"), "•••@•••"),
    # IP addresses
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "•••"),
    # "password hunter2"
    (re.compile(r"(?i)\b(password|passwd|passcode|pwd|pin)(\s+(?:is\s+)?)\S+"), r"\1\2•••"),
    # Any other absolute path (/root/..., /opt/..., /srv/...)
    (re.compile(r"(?<![\w:/~])/(?:[\w.\-]+/)+[\w.\-]*"), "…/"),
]


def redact(text: str) -> str:
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    if _HOME and _HOME != "/":
        out = out.replace(_HOME, "~")
    out = re.sub(r"/(?:Users|home)/[^/\s]+", "~", out)
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def short(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
