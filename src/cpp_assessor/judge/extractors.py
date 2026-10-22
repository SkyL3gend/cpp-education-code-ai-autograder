"""Extract observed facts, never fill outputs from requests or expected records."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

Extractor = Callable[[str, dict[str, Any]], dict[str, Any]]
_HASH_ROW = re.compile(r"^\s*#(\d+)\s+(.+?)\s+(?:手机|电话)\s*[:：]\s*(\S+)\s+生日\s*[:：]\s*(\d{4}\D{1,3}\d{1,2}\D{1,3}\d{1,2})\s*$")
_SEARCH_ROW = re.compile(r"^\s*(\d+)\.\[[^\]\r\n]+\]\s*(.+?)\s+(\S+)\s*$")
_LIST_ROW = re.compile(r"^\s*(\d+)\.\[[^\]\r\n]+\]\s*(.+?)\s*$")
_BIRTHDAY = re.compile(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})")
_REMINDER_ROW = re.compile(r"^\s*\[[^\]\r\n]+\]\s*(.+?)\s*→")


def _parse_birthday(value: str) -> str:
    match = _BIRTHDAY.fullmatch(value.strip())
    if not match:
        return value
    year, month, day = map(int, match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def _section(stdout: str, params: dict[str, Any], prefix: str) -> tuple[str, bool]:
    """Use the final declared section, not merged output from different actions."""
    start, end = params.get(f"{prefix}_start"), params.get(f"{prefix}_end")
    if not isinstance(start, str) or not start or not isinstance(end, str) or not end:
        return stdout, False
    begin = stdout.rfind(start)
    finish = stdout.find(end, begin + len(start)) if begin >= 0 else -1
    return (stdout[begin + len(start):finish], True) if begin >= 0 and finish >= 0 else ("", False)


def _records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    pending = None
    for line in text.splitlines():
        match = _HASH_ROW.fullmatch(line)
        if match:
            records.append({"id": int(match[1]), "name": match[2], "phone": match[3], "birthday": _parse_birthday(match[4])})
            continue
        match = _SEARCH_ROW.fullmatch(line)
        if match:
            records.append({"id": int(match[1]), "name": match[2], "phone": match[3]})
            continue
        match = _LIST_ROW.fullmatch(line)
        if match:
            records.append({"id": int(match[1]), "name": match[2]})
            continue
        match = re.fullmatch(r"\s*编号\s*[：:]\s*(\d+)\s*", line)
        if match:
            pending = {"id": int(match[1])}
            records.append(pending)
            continue
        if pending is not None:
            for label, key in (("姓名", "name"), ("(?:电话|手机)", "phone"), ("生日", "birthday")):
                match = re.fullmatch(rf"\s*{label}\s*[：:]\s*(.*?)\s*", line)
                if match:
                    pending[key] = _parse_birthday(match[1]) if key == "birthday" else match[1]
                    break
    return records


def _complete_records(stdout: str, params: dict[str, Any], prefix: str = "contacts"):
    text, bounded = _section(stdout, params, prefix)
    rows = _records(text)
    complete = bounded and all(set(row) == {"id", "name", "phone", "birthday"} for row in rows)
    if not rows:
        empty = params.get(f"{prefix}_empty_marker")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        complete = complete and isinstance(empty, str) and bool(empty) and lines == [empty.strip()]
    else:
        for line in text.splitlines():
            if line.strip() and not (_HASH_ROW.fullmatch(line) or re.fullmatch(r"\s*(?:编号|姓名|电话|手机|生日)\s*[：:].*", line)):
                complete = False
    return rows, complete


def contactbook_menu_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    rows, complete = _complete_records(stdout, params)
    facts: dict[str, Any] = {"observed_rows": len(rows)}
    matches = [row for row in rows if row.get("name") == params.get("name")]
    if matches or complete:
        facts.update(created=bool(matches), rejected=not bool(matches))
    if len(matches) == 1 and "phone" in matches[0]:
        facts["saved_phone"] = matches[0]["phone"]
    if complete:
        facts["contacts_after"] = rows
    return facts


def birthday_menu_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    text, complete = _section(stdout, params, "reminders")
    names = [match[1] for line in text.splitlines() if (match := _REMINDER_ROW.match(line))]
    facts: dict[str, Any] = {"observed_rows": len(names)}
    empty = params.get("reminders_empty_marker")
    if complete and (names or (isinstance(empty, str) and empty and [line.strip() for line in text.splitlines() if line.strip()] == [empty.strip()])):
        facts.update(names=names, reminded_names=names, section_present=True)
    rows, state_complete = _complete_records(stdout, params)
    if state_complete:
        facts["contacts_after"] = rows
    return facts


def keyword_search_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    rows, complete = _complete_records(stdout, params, "search")
    facts: dict[str, Any] = {"observed_rows": len(rows), "observed_records": rows}
    if complete:
        facts.update(matches=rows, search_hits=[row["name"] for row in rows])
    state, state_complete = _complete_records(stdout, params)
    if state_complete:
        facts["contacts_after"] = state
    return facts


def contact_ids_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    rows, complete = _complete_records(stdout, params)
    facts: dict[str, Any] = {"observed_rows": len(rows), "observed_records": rows}
    if complete:
        facts.update(id_list=sorted(row["id"] for row in rows), ids=[row["id"] for row in rows], names=[row["name"] for row in rows], contacts_after=rows)
    return facts


def json_stdout_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        envelope = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}
    return envelope["actual"] if isinstance(envelope, dict) and isinstance(envelope.get("actual"), dict) else {}


EXTRACTORS = {
    "contactbook_menu_v1": {"function": contactbook_menu_v1, "description": "Actual contact records; full state requires contacts_start/end and explicit empty marker", "produces": ["created", "contacts_after", "saved_phone"], "applies_to": "stdin_script"},
    "birthday_menu_v1": {"function": birthday_menu_v1, "description": "Bounded reminders and independently observed full state", "produces": ["names", "contacts_after"], "applies_to": "stdin_script"},
    "keyword_search_v1": {"function": keyword_search_v1, "description": "Bounded search records and independently observed full state", "produces": ["matches", "contacts_after"], "applies_to": "stdin_script"},
    "contact_ids_v1": {"function": contact_ids_v1, "description": "Actual identifiers, no renumbering or offsets", "produces": ["id_list", "contacts_after"], "applies_to": "stdin_script"},
    "json_stdout_v1": {"function": json_stdout_v1, "description": "Protocol actual; envelope must be validated separately", "produces": [], "applies_to": "function_driver"},
}


def extractor_ids() -> set[str]:
    return set(EXTRACTORS)


def get_extractor(extractor_id: str) -> Extractor:
    return EXTRACTORS[extractor_id]["function"]


def describe_extractors() -> list[dict[str, Any]]:
    return [{"extractor_id": key, **{k: v for k, v in item.items() if k != "function"}} for key, item in EXTRACTORS.items()]


def extract(extractor_id: str, stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    return get_extractor(extractor_id)(stdout, params)
