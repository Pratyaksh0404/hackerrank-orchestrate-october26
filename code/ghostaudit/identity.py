"""Identity resolution: request email -> user_id -> every identifier the user ever had."""
from __future__ import annotations

import hashlib
from collections import defaultdict

from .dataio import load_audit_log, load_identities
from .models import Identity, norm


def email_hash(email: str) -> str:
    return hashlib.sha256(norm(email).encode()).hexdigest()


class IdentityResolver:
    """Builds the full identifier set for a person.

    Sources:
      * user_identities.csv  - the current snapshot (name, email, phone, hash)
      * user_audit_log.csv   - historical email / phone / email_hash changes
    The request email may be a *historical* address, so lookups go through an
    index of every email the audit log has ever recorded.
    """

    def __init__(self) -> None:
        self.by_user: dict[str, Identity] = {}
        self.email_index: dict[str, str] = {}
        self._build()

    def _build(self) -> None:
        for row in load_identities():
            ident = Identity(user_id=row["user_id"], name=row["name"])
            ident.current_email = norm(row["email"])
            ident.emails.add(ident.current_email)
            if row["phone"]:
                ident.phones.add(norm(row["phone"]))
            if row["email_hash"]:
                ident.hashes.add(norm(row["email_hash"]))
            self.by_user[ident.user_id] = ident

        history: dict[str, list[dict]] = defaultdict(list)
        for ev in load_audit_log():
            history[ev["user_id"]].append(ev)
        for uid, events in history.items():
            ident = self.by_user.get(uid)
            if ident is None:
                continue
            for ev in events:
                for v in (ev["old_value"], ev["new_value"]):
                    v = norm(v)
                    if not v:
                        continue
                    if ev["field_name"] == "email":
                        ident.emails.add(v)
                    elif ev["field_name"] == "phone":
                        ident.phones.add(v)
                    elif ev["field_name"] == "email_hash":
                        ident.hashes.add(v)

        for ident in self.by_user.values():
            for e in ident.emails:
                ident.hashes.add(email_hash(e))     # hash of every email ever used
                self.email_index.setdefault(e, ident.user_id)

        self.audit_by_user = history

    def resolve(self, email: str) -> Identity | None:
        uid = self.email_index.get(norm(email))
        return self.by_user.get(uid) if uid else None

    def describe(self, ident: Identity, request_email: str) -> dict:
        """Human-readable summary of how the request email matched (for reports)."""
        e = norm(request_email)
        return {
            "user_id": ident.user_id,
            "matched_as": "current_email" if e == ident.current_email else "historical_email",
            "current_email": ident.current_email,
            "historical_emails": sorted(ident.emails - {ident.current_email}),
            "phones": sorted(ident.phones),
        }
