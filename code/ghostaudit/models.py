"""Core data models and resource taxonomy shared by every module."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- normalized record states -------------------------------------------------
PRESENT = "PRESENT"            # a record exists (and carries data)
ABSENT = "ABSENT"              # record confirmed missing (404 / NOT_FOUND / no row)
IDENTIFIABLE = "IDENTIFIABLE"  # record exists and still exposes direct identifiers
REDACTED = "REDACTED"          # record exists, direct identifiers removed
ANONYMIZED = "ANONYMIZED"      # record exists, irreversibly unlinked
ACTIVE = "ACTIVE"              # data flow enabled / restore still allowed
DISABLED = "DISABLED"          # data flow disabled
BLOCKED = "BLOCKED"            # restore blocked

# --- resource taxonomy (prefix -> system_id, category) ------------------------
RESOURCE_TABLE: dict[str, tuple[str, str]] = {
    "account": ("account_service", "USER_PROFILE"),
    "search": ("search_service", "SEARCH_INDEX_ENTRY"),
    "support": ("support_platform", "SUPPORT_MESSAGE"),
    "analytics": ("analytics_platform", "ANALYTICS_PROFILE"),
    "telemetry": ("analytics_platform", "PRODUCT_TELEMETRY"),
    "export": ("export_service", "USER_EXPORT"),
    "audit_record": ("security_service", "SECURITY_AUDIT_LOG"),
    "data_flow": ("integration_service", "DATA_FLOW"),
    "backup": ("backup_service", "BACKUP_ARCHIVE"),
    "tombstone": ("identity_service", "ACCOUNT_TOMBSTONE"),
    "legal_hold": ("legal_service", "LEGAL_HOLD_RECORD"),
    "cache": ("cache_service", "CACHE_ENTRY"),
    "email_delivery": ("email_delivery_service", "EMAIL_DELIVERY_LOG"),
    "marketing": ("marketing_platform", "MARKETING_PROFILE"),
    "object": ("object_storage", "SUPPORT_ATTACHMENT"),
}

_PREFIX_RE = re.compile(r"^([a-z_]+?)_(\d+)$")


def split_resource(resource_id: str) -> tuple[str, str] | None:
    """Return (system_id, category) for a resource id like 'search_052'."""
    m = _PREFIX_RE.match(resource_id or "")
    if not m:
        return None
    return RESOURCE_TABLE.get(m.group(1))


def system_of(resource_id: str) -> str | None:
    r = split_resource(resource_id)
    return r[0] if r else None


def category_of(resource_id: str) -> str | None:
    r = split_resource(resource_id)
    return r[1] if r else None


def norm(value: str | None) -> str:
    """Normalize an identifier for matching (emails are case-insensitive)."""
    return (value or "").strip().lower()


@dataclass(frozen=True)
class Obs:
    """One observation: a resource seen in a given state by one piece of evidence."""
    evidence_id: str               # manifest file_id (or activity/audit id)
    observed_at: str               # ISO timestamp the state was observed
    system_id: str
    resource_id: str
    state: str                     # one of the state constants above
    ids: frozenset[str] = frozenset()   # normalized identifiers tied to this record
    kind: str = ""                 # which parser produced it
    detail: str = ""               # topology/table/etc, useful in explanations
    category: str = ""             # overrides the prefix-derived category when set

    @property
    def cat(self) -> str:
        return self.category or category_of(self.resource_id) or ""


@dataclass(frozen=True)
class Coverage:
    """Evidence that fully enumerates one system at a point in time.

    A snapshot that covers a system but lacks a record proves that record is
    absent there; this is how COMPLETE cases get evidence for absence checks.
    """
    evidence_id: str
    captured_at: str
    system_id: str
    detail: str = ""
    primary: bool = True   # False for replicas / secondary copies


@dataclass
class Identity:
    user_id: str
    name: str
    emails: set[str] = field(default_factory=set)      # current + historical
    phones: set[str] = field(default_factory=set)
    hashes: set[str] = field(default_factory=set)
    current_email: str = ""

    def all_keys(self) -> set[str]:
        return {self.user_id.lower(), *self.emails, *self.phones, *self.hashes}


@dataclass
class Case:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str = ""
    step_no: int = 0

    def to_json(self) -> dict:
        return {
            "step_no": self.step_no,
            "phase": self.phase,
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "operation": self.operation,
            "state": self.state,
            "evidence_id": self.evidence_id,
        }
