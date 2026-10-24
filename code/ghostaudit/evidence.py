"""EvidenceStore: runs every parser over the manifest once and indexes the result."""
from __future__ import annotations

import logging
from collections import defaultdict

from .dataio import Manifest
from .models import Coverage, Obs
from .parsers.jsonfiles import parse_json
from .parsers.logs import parse_log_file
from .parsers.sql import parse_sql
from .parsers.text import parse_text

log = logging.getLogger("ghostaudit.evidence")


class EvidenceStore:
    def __init__(self, manifest: Manifest | None = None, visual=None) -> None:
        self.manifest = manifest or Manifest.load()
        self.obs: list[Obs] = []
        self.coverage: list[Coverage] = []
        self.visual = visual                      # optional callable(path, file_id, ts) -> (obs, coverage)
        self.errors: list[tuple[str, str]] = []
        self._load()
        self._index()

    def _load(self) -> None:
        for row in self.manifest:
            fid, path, ts = row["file_id"], self.manifest.path(row["file_id"]), row["captured_at"]
            suffix = path.suffix.lower()
            try:
                if suffix == ".sql":
                    o, c = parse_sql(path, fid, ts)
                elif suffix == ".json":
                    o, c = parse_json(path, fid, ts)
                elif suffix == ".log":
                    o, c = parse_log_file(path, fid), []
                elif suffix == ".txt":
                    o, c = parse_text(path, fid, ts)
                elif suffix in (".png", ".pdf", ".jpg", ".jpeg") and self.visual:
                    o, c = self.visual(path, fid, ts)
                else:
                    continue
            except Exception as exc:                   # one bad file must not sink the run
                self.errors.append((fid, f"{type(exc).__name__}: {exc}"))
                log.warning("failed to parse %s: %s", fid, exc)
                continue
            self.obs.extend(o)
            self.coverage.extend(c)

    def _index(self) -> None:
        self.by_key: dict[str, list[Obs]] = defaultdict(list)
        self.by_resource: dict[str, list[Obs]] = defaultdict(list)
        for o in self.obs:
            self.by_resource[o.resource_id].append(o)
            for k in o.ids:
                self.by_key[k].append(o)
        self.cov_by_system: dict[str, list[Coverage]] = defaultdict(list)
        for c in self.coverage:
            self.cov_by_system[c.system_id].append(c)

    def captured_at(self, evidence_id: str) -> str:
        return self.manifest.captured_at(evidence_id) if evidence_id in self.manifest.rows else ""
