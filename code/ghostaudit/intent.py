"""Does the request actually authorize deletion?

Heuristic baseline (multilingual keyword cues). The LLM classifier in llm_intent
refines it; this module is also the offline fallback when no API key is set.
"""
from __future__ import annotations

import re

DELETE_CUES = re.compile(
    r"delet|remov|erase|erasure|wipe|purge|close[d]? (my|the|this) account|closed|deactivat|forget|"
    r"eliminar|elimin[eo]|borr(ar|e)|suprim|cancelar mi cuenta|"
    r"supprim|suppression|effac|clôtur|fermer|"
    r"lösch|entfern|"
    r"cancell?|exclu[ií]|exclus|excluir|apag|remov(er|a)|"
    r"usu[nń]|usuw|"
    r"cancell?are|cancellazione|elimina|"
    r"删除|刪除|注销|削除|退会|삭제|"
    r"удал|"
    r"حذف|"
    r"verwijder|ta bort|slett|poista|"
    r"(is|are|been|was) gone|no longer (exist|be|appear|identif)|cannot identify me|can(no|')t (be )?identif", re.I)

KEEP_CUES = re.compile(
    r"keep (my|the|this) account|leave (my|the|this)( \w+)* (active|in place|as it is|as is)|"
    r"keep the account|do not delete|don't delete|dont delete|please keep (it|everything|my (profile|account|records|history|data))|continue using|"
    r"no (quiero|deseo) (eliminar|borrar)|ne (pas )?supprim|nicht (löschen|entfernen)|"
    r"mantener (mi|la) cuenta|garder (mon|le) compte|account (active|open)|"
    r"(do not|don't|dont|never) (close|deactivate|cancel)|without (remov|delet|eras|clos)|"
    r"(lists?|emails?|messages?|newsletters?|marketing)\b.{0,25}\bonly|remove me from (the )?(marketing|mailing|promotional)|"
    r"still (need|want|use)", re.I)

NEGATED_DELETE = re.compile(
    r"\bnot (a |an )?(request|asking|requesting|wanting|looking)( to| for)? (delet|erase|erasure|remov)|"
    r"(do not|don't|dont) (want|wish|need|ask)( you)? (to )?(delet|erase|remov)", re.I)

OTHER_ACTION_CUES = re.compile(
    r"update|change|replace|correct(?! treatment)|rectif|download|export|copy of|invoice|receipt|opt[- ]?out|unsubscribe|"
    r"marketing|newsletter|access request|portable|"
    r"actualiz|cambi|modific|descarg|copia|factura|"
    r"mettre à jour|modifi|télécharg|facture|"
    r"ändern|aktualisier|herunterladen|rechnung", re.I)


def heuristic(text: str) -> tuple[bool, str]:
    """Return (authorizes_deletion, reason)."""
    has_delete = bool(DELETE_CUES.search(text))
    if NEGATED_DELETE.search(text) and not re.search(r"\b(please|kindly|do) (delete|erase|remove)", text, re.I):
        return False, "explicitly says this is not a deletion request"
    keeps = bool(KEEP_CUES.search(text))
    other = bool(OTHER_ACTION_CUES.search(text))
    if keeps:
        return False, "asks to keep the account or limits the request to a non-deletion action"
    if not has_delete:
        return False, "no deletion language"
    return True, "deletion language present"


_ENGLISH_HINT = re.compile(r"\b(the|my|please|account|and|this|your|delete|request)\b", re.I)


def classify(text: str) -> tuple[bool, str, bool]:
    """(authorizes_deletion, reason, confident). Unconfident texts are the ones worth a model call:
    non-English wording, or deletion language mixed with keep / other-action cues."""
    ok, why = heuristic(text)
    english = bool(_ENGLISH_HINT.search(text)) and text.isascii()
    mixed = bool(OTHER_ACTION_CUES.search(text)) or bool(KEEP_CUES.search(text)) or bool(NEGATED_DELETE.search(text))
    confident = english and not mixed if ok else english
    return ok, why, confident
