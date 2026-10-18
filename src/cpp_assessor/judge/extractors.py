"""宿主注册的观测提取器。

模式二里 LLM 不能自己写"从输出里抠事实"的代码（那等于让它写判据），
它只能从这里的注册表里选一个提取器，并给出参数。提取器只做两件事：

- 从被测程序的原始输出里读出"可观察事实"；
- 把事实整理成一个 ``dict``，键名与任务断言的 ``actual_observation_key`` 对齐。

提取器不认识期望值，也不做 PASS/FAIL 判断。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

Extractor = Callable[[str, dict[str, Any]], dict[str, Any]]

#: 菜单列表行：``4.[普通] 张三``（只有名字，名字里可能有空格）
_LIST_ROW = re.compile(r"^\s*(\d+)\.\[[^\]\r\n]+\]\s*(.+?)\s*$")
#: 搜索结果行：``4.[普通] 张三 13800138000``（名字 + 电话；名字允许空格）
_SEARCH_ROW = re.compile(r"^\s*(\d+)\.\[[^\]\r\n]+\]\s*(.+?)\s+(\d{6,})\s*$")
#: 另一种列表/检索格式：``  #4 张三  手机:13800138000  生日:2000-01-01``
_HASH_ROW = re.compile(
    r"^\s*#(\d+)\s+(.+?)\s+(?:手机|电话)\s*[:：]\s*(\d{6,})\s+生日\s*[:：]\s*"
    r"([0-9]{4}\D{1,3}[0-9]{1,2}\D{1,3}[0-9]{1,2})\s*$"
)

_BIRTHDAY = re.compile(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})")


def _parse_birthday(value: Any) -> str:
    if isinstance(value, dict):
        year = int(value.get("year") or 0)
        month = int(value.get("month") or 0)
        day = int(value.get("day") or 0)
        if year and month and day:
            return f"{year:04d}-{month:02d}-{day:02d}"
        return ""
    if isinstance(value, str):
        match = _BIRTHDAY.search(value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def _rows(stdout: str) -> tuple[dict[str, str], dict[str, str]]:
    """返回（列表里的 名字→行号，搜索里的 名字→电话）。"""
    listed: dict[str, str] = {}
    found: dict[str, str] = {}
    for line in stdout.splitlines():
        search = _SEARCH_ROW.match(line)
        if search:
            found.setdefault(search.group(2), search.group(3))
            continue
        hashed = _HASH_ROW.match(line)
        if hashed:
            listed.setdefault(hashed.group(2), hashed.group(1))
            found.setdefault(hashed.group(2), hashed.group(3))
            continue
        plain = _LIST_ROW.match(line)
        if plain:
            listed.setdefault(plain.group(2), plain.group(1))
    return listed, found


def contactbook_menu_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    """通讯录菜单程序：观测"这次操作后通讯录里有没有这个联系人、存的是什么电话"。

    params：
    - ``name`` / ``phone``：本次要添加的候选人（宿主用用例输入填充）；
    - ``birthday``：候选人生日（dict 或 ``YYYY-MM-DD`` 字符串）；
    - ``baseline_names``：程序自带的演示联系人名字，比对时排除；
    - ``initial_contacts``：用例声明的操作前通讯录（宿主自动注入）。菜单程序通常
      无法导入用例里的初始数据，这份声明就是"操作前"的语义基线；
    - ``renumber_ids``：把新增联系人重新编号（默认 true）。
    """
    name = str(params.get("name") or "")
    phone = str(params.get("phone") or "")
    birthday = _parse_birthday(params.get("birthday"))
    baseline = {str(item) for item in (params.get("baseline_names") or [])}
    renumber = bool(params.get("renumber_ids", True))
    initial = [dict(item) for item in (params.get("initial_contacts") or []) if isinstance(item, dict)]
    for item in initial:
        if "birthday" in item:
            normalized = _parse_birthday(item["birthday"])
            if normalized:
                item["birthday"] = normalized

    listed, found = _rows(stdout)
    stored_phone = found.get(name) or ""
    created = bool(name) and (name in found or name in listed)

    contacts: list[dict[str, Any]] = [dict(item) for item in initial]
    names = {str(item.get("name") or "") for item in contacts}
    next_id = 1 + max((int(item.get("id") or 0) for item in contacts), default=0)

    if created and name not in names:
        contacts.append(
            {
                "id": next_id,
                "name": name,
                "phone": stored_phone or phone,
                "birthday": birthday,
            }
        )
        names.add(name)
        next_id += 1

    # 程序里还出现了别的条目：说明这次操作不该出现的副作用，也如实记下来。
    for listed_name in listed:
        if listed_name in baseline or listed_name in names:
            continue
        contacts.append(
            {
                "id": next_id if renumber else int(listed[listed_name]),
                "name": listed_name,
            }
        )
        names.add(listed_name)
        next_id += 1

    return {
        "created": created,
        "saved_phone": stored_phone or None,
        "contacts_after": contacts,
        "rejected": not created,
        # 宿主探针用：这次剧本到底有没有读到列表/搜索里的行。
        # 没有任何行说明剧本没走到能读出事实的界面，观测不算数。
        "observed_rows": len(listed) + len(found),
    }


#: 生日提醒行：``  [普通] 张伟  →  1990-10-18``
_REMINDER_ROW = re.compile(r"^\s*\[[^\]\r\n]+\]\s*(\S+)\s*→")
#: 详情块：``编号：4`` / ``姓名：张三``
_DETAIL_ID = re.compile(r"编号\s*[：:]\s*(\d+)")
_DETAIL_NAME = re.compile(r"姓名\s*[：:]\s*(\S+)")
_DETAIL_PHONE = re.compile(r"(?:电话|手机)\s*[：:]\s*(\S+)")
_DETAIL_BIRTHDAY = re.compile(r"生日\s*[：:]\s*([0-9]{4}\D{1,3}[0-9]{1,2}\D{1,3}[0-9]{1,2})")


def _normalized_initial(params: dict[str, Any]) -> list[dict[str, Any]]:
    initial = [dict(item) for item in (params.get("initial_contacts") or []) if isinstance(item, dict)]
    for item in initial:
        if "birthday" in item:
            normalized = _parse_birthday(item["birthday"])
            item["birthday"] = normalized or item["birthday"]
    return initial


def birthday_menu_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    """生日提醒菜单：观测"提醒名单里出现了谁"。

    params：
    - ``baseline_names``：程序自带演示联系人，比对时排除；
    - ``initial_contacts``：用例声明的通讯录（宿主自动注入）；提醒不修改数据，
      因此 ``contacts_after`` 就是这份基线。
    """
    baseline = {str(item) for item in (params.get("baseline_names") or [])}
    names: list[str] = []
    for line in stdout.splitlines():
        reminder = _REMINDER_ROW.match(line)
        if reminder:
            name = reminder.group(1)
            if name not in baseline and name not in names:
                names.append(name)
    return {
        "names": names,
        "reminded_names": names,
        "section_present": "生日提醒" in stdout,
        "contacts_after": _normalized_initial(params),
        "observed_rows": len(names),
    }


def keyword_search_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    """按名字搜索：观测命中名单（``i.[类型] 姓名 电话`` 行），对齐到用例声明的联系人。

    params：
    - ``initial_contacts``：用例声明的通讯录（宿主自动注入）；
    - ``baseline_names``：程序自带演示联系人，比对时排除；
    - ``keyword``：本次搜索关键字（仅用于说明，不参与判定）。
    """
    baseline = {str(item) for item in (params.get("baseline_names") or [])}
    initial = _normalized_initial(params)
    by_name = {str(item.get("name") or ""): dict(item) for item in initial}
    _, found = _rows(stdout)
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()
    next_id = 1 + max((int(item.get("id") or 0) for item in initial), default=0)
    for name, phone in found.items():
        if name in baseline or name in seen:
            continue
        seen.add(name)
        if name in by_name:
            matches.append(dict(by_name[name]))
        else:
            matches.append({"id": next_id, "name": name, "phone": phone, "birthday": ""})
            next_id += 1
    return {
        "matches": matches,
        "search_hits": [item["name"] for item in matches],
        "contacts_after": initial,
        "observed_rows": len(found),
    }


def contact_ids_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    """详情页观测：从 ``编号：`` / ``姓名：`` 块里读编号。

    params：
    - ``initial_contacts``：用例声明的通讯录（宿主自动注入）；
    - ``watch_names``：只关心这些名字（通常是声明的初始联系人与新增联系人）；
    - ``id_offset``：程序编号与用例编号相差一个常数时做平移（默认 0）。
    """
    initial = _normalized_initial(params)
    offset = int(params.get("id_offset") or 0)
    watch = {str(item) for item in (params.get("watch_names") or [])}
    if not watch:
        watch = {str(item.get("name") or "") for item in initial}

    seen: list[dict[str, Any]] = []
    pending_id: int | None = None
    pending: dict[str, Any] = {}
    for line in stdout.splitlines():
        match = _DETAIL_ID.search(line)
        if match:
            pending_id = int(match.group(1)) + offset
            pending = {}
            continue
        match = _DETAIL_NAME.search(line)
        if match and pending_id is not None:
            name = match.group(1)
            if name in watch or not watch:
                entry = {"id": pending_id, "name": name}
                seen.append(entry)
                pending = entry
            continue
        if pending is not None:
            match = _DETAIL_PHONE.search(line)
            if match:
                pending["phone"] = match.group(1)
                continue
            match = _DETAIL_BIRTHDAY.search(line)
            if match:
                pending["birthday"] = _parse_birthday(match.group(1))
    by_name = {str(item.get("name") or ""): dict(item) for item in initial}
    contacts = [dict(item) for item in initial]
    names = {str(item.get("name") or "") for item in contacts}
    for entry in seen:
        if entry["name"] in names:
            continue
        item = {"id": entry["id"], "name": entry["name"], "phone": entry.get("phone", ""), "birthday": entry.get("birthday", "")}
        contacts.append(item)
        names.add(entry["name"])
    id_list = sorted(entry["id"] for entry in seen)
    return {
        "id_list": id_list,
        "ids": [entry["id"] for entry in seen],
        "names": [entry["name"] for entry in seen],
        "contacts_after": contacts,
        "observed_rows": len(seen),
    }


def json_stdout_v1(stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    """函数驱动：被测程序按协议在最后一行输出观测信封，取其中的 ``actual``。"""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(envelope, dict) and isinstance(envelope.get("actual"), dict):
            return envelope["actual"]
    return {}


EXTRACTORS: dict[str, dict[str, Any]] = {
    "contactbook_menu_v1": {
        "function": contactbook_menu_v1,
        "description": "通讯录菜单程序：读列表与搜索结果，得到 created / saved_phone / contacts_after",
        "produces": ["created", "saved_phone", "contacts_after", "rejected"],
        "applies_to": "stdin_script",
    },
    "birthday_menu_v1": {
        "function": birthday_menu_v1,
        "description": "生日提醒菜单：读提醒名单，得到 names / contacts_after（提醒不改数据）",
        "produces": ["names", "reminded_names", "section_present", "contacts_after"],
        "applies_to": "stdin_script",
    },
    "keyword_search_v1": {
        "function": keyword_search_v1,
        "description": "按名字搜索：读搜索结果行，得到 matches（对齐到声明的联系人）/ contacts_after",
        "produces": ["matches", "search_hits", "contacts_after"],
        "applies_to": "stdin_script",
    },
    "contact_ids_v1": {
        "function": contact_ids_v1,
        "description": "逐个打开详情页：从编号/姓名块读 id_list / contacts_after，可用 id_offset 平移",
        "produces": ["id_list", "ids", "names", "contacts_after"],
        "applies_to": "stdin_script",
    },
    "json_stdout_v1": {
        "function": json_stdout_v1,
        "description": "取标准输出最后一行观测信封里的 actual（函数驱动）",
        "produces": [],
        "applies_to": "function_driver",
    },
}


def extractor_ids() -> set[str]:
    return set(EXTRACTORS)


def get_extractor(extractor_id: str) -> Extractor:
    entry = EXTRACTORS.get(extractor_id)
    if entry is None:
        raise KeyError(f"未注册的提取器：{extractor_id}")
    return entry["function"]


def describe_extractors() -> list[dict[str, Any]]:
    return [
        {
            "extractor_id": extractor_id,
            "description": entry["description"],
            "produces": entry["produces"],
            "applies_to": entry["applies_to"],
        }
        for extractor_id, entry in EXTRACTORS.items()
    ]


def extract(extractor_id: str, stdout: str, params: dict[str, Any]) -> dict[str, Any]:
    return get_extractor(extractor_id)(stdout, params)
