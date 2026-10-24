"""deletion_rules.csv -> executable policy.

The rules file is the source of truth: the required action for each data
category is read from it, not hard-coded, so changing the CSV changes behavior.
"""
from __future__ import annotations

from .dataio import load_rules

DELETE, REDACT, ANONYMIZE, RETAIN = "DELETE", "REDACT", "ANONYMIZE", "RETAIN"


class Policy:
    def __init__(self, rules: list[dict] | None = None) -> None:
        self.rules = rules or load_rules()
        self.by_category: dict[str, dict] = {}
        for r in self.rules:
            self.by_category.setdefault(r["data_category"], r)

    def action(self, category: str) -> str | None:
        r = self.by_category.get(category)
        return r["required_action"] if r else None

    def rule_id(self, category: str) -> str | None:
        r = self.by_category.get(category)
        return r["rule_id"] if r else None

    def describe(self, category: str) -> str:
        r = self.by_category.get(category)
        return f"{r['rule_id']}: {r['data_category']} -> {r['required_action']} ({r['condition']})" if r else "no rule"


# Categories whose compliance is about a running process, not a record's content.
FLOW_CATEGORY = "DATA_FLOW"
BACKUP_CATEGORY = "BACKUP_ARCHIVE"
