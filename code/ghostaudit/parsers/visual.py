"""Screenshots and PDFs -> observations.

Text comes from (in order): a vision-language model if one is configured, else local OCR
(`pdftotext` for PDFs, `tesseract` for images). Results are cached on disk by file hash, so a
re-run never pays twice. The text is then read with the same grounded rules as every other source:
resource ids, e-mail addresses, phone numbers and user references that appear together.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from pathlib import Path

from ..models import (ACTIVE, ANONYMIZED, BLOCKED, DISABLED, IDENTIFIABLE, PRESENT, REDACTED, RESOURCE_TABLE,
                      Coverage, Obs, norm, split_resource)

log = logging.getLogger("ghostaudit.visual")

RID_RE = re.compile(r"\b(" + "|".join(sorted(RESOURCE_TABLE, key=len, reverse=True)) + r")_(\d+)\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+\d[\d ()\-]{7,}\d")
USER_RE = re.compile(r"\buser_\d+\b")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")


_WIN_DIRS = [r"C:\Program Files\Tesseract-OCR", r"C:\Program Files (x86)\Tesseract-OCR",
             r"C:\Program Files\poppler\Library\bin", r"C:\poppler\Library\bin", r"C:\poppler\bin"]


def find_tool(name: str) -> str | None:
    """PATH first, then the usual Windows install folders (the installers do not always add PATH)."""
    import os
    found = shutil.which(name)
    if found:
        return found
    for d in list(_WIN_DIRS) + [p for p in os.environ.get("OCR_TOOL_DIRS", "").split(os.pathsep) if p]:
        for ext in ("", ".exe"):
            cand = Path(d) / (name + ext)
            if cand.is_file():
                return str(cand)
    # poppler zips unpack into a versioned sub-folder (C:\poppler\poppler-xx\Library\bin): search below the root
    for base in (r"C:\poppler", r"C:\Program Files\poppler", os.path.expanduser(r"~\poppler")):
        if Path(base).is_dir():
            for cand in Path(base).rglob(name + ".exe"):
                return str(cand)
    return None


_warned: set[str] = set()


def extract_text(path: Path, cache_dir: Path | None = None) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:20]
    cache = (cache_dir / f"{path.stem}.{digest}.txt") if cache_dir else None
    if cache and cache.exists():
        return cache.read_text(errors="ignore")
    text = ""
    ext = path.suffix.lower()
    tool = find_tool("pdftotext") if ext == ".pdf" else find_tool("tesseract")
    if tool and ext == ".pdf":
        text = subprocess.run([tool, "-layout", str(path), "-"], capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout
    elif tool:
        text = subprocess.run([tool, str(path), "-", "--psm", "6"], capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout
    elif ("pdf" if ext == ".pdf" else "img") not in _warned:
        _warned.add("pdf" if ext == ".pdf" else "img")
        log.warning("no local OCR tool for %s files (%s); screenshots/PDFs need the vision model or an OCR install",
                    ext, "pdftotext/poppler" if ext == ".pdf" else "tesseract")
    if cache and text:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text)
    return text


class VisualReader:
    """Callable(path, file_id, captured_at) -> (observations, coverage)."""

    def __init__(self, cache_dir: Path | None = None, vlm=None) -> None:
        self.cache_dir, self.vlm = cache_dir, vlm

    def text(self, path: Path) -> str:
        """Local OCR first (free, deterministic); the vision model only when OCR is unavailable or empty."""
        t = extract_text(path, self.cache_dir)
        if t.strip() or self.vlm is None:
            return t
        try:
            return self.vlm(path) or ""
        except Exception as exc:
            log.warning("vision model failed on %s: %s", path.name, exc)
            return ""

    def __call__(self, path: Path, file_id: str, captured_at: str) -> tuple[list[Obs], list[Coverage]]:
        text = self.text(path)
        low = text.lower()
        def keys_in(chunk: str) -> frozenset:
            return frozenset(norm(e) for e in EMAIL_RE.findall(chunk)) | \
                frozenset(norm(p) for p in PHONE_RE.findall(chunk)) | \
                frozenset(USER_RE.findall(chunk)) | frozenset(HASH_RE.findall(chunk))

        doc_keys = keys_in(text)
        subjects = {k for k in doc_keys if "@" in k or k.startswith("user_")}
        single_subject = len(subjects) <= 2        # one person's e-mail (+ maybe user ref): a per-person document
        obs: list[Obs] = []
        for m in RID_RE.finditer(text):
            rid = m.group(0)
            line = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.end()) if text.find("\n", m.end()) >= 0 else len(text)]
            keys = doc_keys if single_subject else keys_in(line)
            sysid, cat = split_resource(rid)
            if cat == "SUPPORT_MESSAGE" and "attachment" in low:
                cat = "SUPPORT_ATTACHMENT"          # an attachment-properties page of a support item
            state = {"DATA_FLOW": ACTIVE}.get(cat, IDENTIFIABLE if keys and cat in
                                              ("ANALYTICS_PROFILE", "SUPPORT_MESSAGE", "ACCOUNT_TOMBSTONE") else PRESENT)
            obs.append(Obs(file_id, captured_at, sysid, rid, state, keys, kind="visual", detail=path.name, category=cat))
        # one observation per resource is enough
        uniq = {o.resource_id: o for o in obs}
        return list(uniq.values()), []
