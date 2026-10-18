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

#: 菜单列表行：``4.[普通] 张三``（只有名字）
_LIST_ROW = re.compile(r"^\s*(\d+)\.\[[^\]\r\n]+\]\s*(\S+)\s*$")
#: 搜索结果行：``4.[普通] 张三 13800138000``（名字 + 电话）
_SEARCH_ROW = re.compile(r"^\s*(\d+)\.\[[^\]\r\n]+\]\s*(\S+)\s+(\S+)\s*$")

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
