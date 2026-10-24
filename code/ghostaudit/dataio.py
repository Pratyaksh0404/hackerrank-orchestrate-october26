"""Paths and CSV loaders. Everything resolves relative to the repository root."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .models import Case

def _find_root() -> Path:
    """Repository root = the nearest parent holding dataset/ (override with GHOST_REPO_ROOT)."""
    import os
    if os.environ.get("GHOST_REPO_ROOT"):
        return Path(os.environ["GHOST_REPO_ROOT"]).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "dataset").is_dir():
            return parent
    return here.parents[2]


REPO_ROOT = _find_root()
DATASET = REPO_ROOT / "dataset"


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_cases(path: Path | None = None) -> list[Case]:
    path = path or DATASET / "deletion_requests.csv"
    return [Case(**{k: r[k] for k in Case.__dataclass_fields__}) for r in read_csv(path)]


@dataclass
class Manifest:
    """evidence_files.csv: file_id -> path / origin / capture time."""
    rows: dict[str, dict]

    @classmethod
    def load(cls) -> "Manifest":
        return cls({r["file_id"]: r for r in read_csv(DATASET / "evidence_files.csv")})

    def path(self, file_id: str) -> Path:
        return DATASET / self.rows[file_id]["file_path"]

    def captured_at(self, file_id: str) -> str:
        return self.rows[file_id]["captured_at"]

    def __iter__(self):
        return iter(self.rows.values())


def load_rules() -> list[dict]:
    return read_csv(DATASET / "deletion_rules.csv")


def load_activity() -> list[dict]:
    return read_csv(DATASET / "system_activity.csv")


def load_identities() -> list[dict]:
    return read_csv(DATASET / "user_identities.csv")


def load_audit_log() -> list[dict]:
    return read_csv(DATASET / "user_audit_log.csv")
