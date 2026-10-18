# -*- coding: utf-8 -*-
"""单文件独立程序的“观察型”探针。

五道题的标准入口是基准工程（contactbook_base）的小改：提交物必须覆盖工程里的
同名文件，再由 test_entry.cpp 驱动 ``cb::`` 接口。仓库随附的
《参考基准程序/通讯录_v6最终版.cpp》是一个自带 main() 与菜单的完整程序，没有
``cb::`` 接口，因此无法链接测试驱动。

为了让这类单文件程序也能进入“问题测评”，标准运行在发现提交物与基准工程毫无
交集时退化为“单文件适配”：

1. 只编译提交的那个 .cpp，不链接测试驱动，也没有 JSON 协议；
2. 用 selfcheck 里已经验证过的固定菜单脚本驱动它（stdin 按键序列）；
3. 从 stdout 提取一条能用菜单直接观察的规则事实，按规则给出判定。

一个任务对应一个探针，只覆盖一条可观察规则。报告会显著标注“单文件适配
（观察型）”，并说明其余用例未执行——它不是完整评分，但能给出可核对的结果，
而不是在入口校验处直接拒绝。

本模块只被 ``orchestrator.runner`` 在进入单文件适配时惰性导入，避免
``selfcheck`` 包在 runner 初始化期间的反向导入（selfcheck.runner 依赖 runner）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping

from .checks import by_id, extract_birthday, extract_date, extract_phone, extract_search


@dataclass(frozen=True)
class LegacyProbe:
    """一条单文件探针：脚本 + 观察事实 + 需要核对的规则。"""

    task_id: str
    check_id: str
    title: str
    rule_ids: tuple[str, ...]
    expectation: str
    script: tuple[str, ...] | Callable[[], tuple[str, ...]]
    inspect: Callable[[str, str], dict[str, Any]]
    guard: Mapping[str, Any]
    expected: Mapping[str, Any]
    available: bool = True

    @property
    def case_id(self) -> str:
        return f"single_file_{self.check_id}"

    @property
    def assertion_id(self) -> str:
        return f"single_file_{self.check_id}"

    def resolved_script(self) -> tuple[str, ...]:
        """返回本次运行实际要喂给程序的按键序列（允许按运行日期生成）。"""
        return self.script() if callable(self.script) else self.script


# ---------------------------------------------------------------------------
# 观察值提取：只保留“能用菜单直接看到”的事实
# ---------------------------------------------------------------------------


def _phone_facts(stdout: str, stderr: str) -> dict[str, Any]:
    observed = extract_phone(stdout, stderr)
    return {
        "added": observed["added"],
        "search_entered": observed["search_entered"],
        "recorded_phone": observed["recorded_phone"],
        "invalid_phone_rejected": observed["recorded_phone"] == "13800138000",
    }


def _birthday_script() -> tuple[str, ...]:
    today = date.today()
    birthday = f"{today.year} {today.month} {today.day}"
    return (
        "20",
        # 新增一个生日就是“今天”的联系人。
        "2", "1", "1",
        "生日测试", "13800138013", birthday, "北京", "D路4号",
        "0",
        # 生日提醒：今天日期 + 提前 0 天 → 当天生日的人必须出现。
        "5", birthday, "0",
        "0",
    )


def _birthday_facts(stdout: str, stderr: str) -> dict[str, Any]:
    observed = extract_birthday(stdout, stderr)
    names = observed["reminder_names"]
    return {
        "section_present": observed["section_present"],
        "reminder_names": names,
        "today_birthday_reminded": "生日测试" in names,
    }


def _date_facts(stdout: str, stderr: str) -> dict[str, Any]:
    observed = extract_date(stdout, stderr)
    return {
        "added": observed["added"],
        "rejections": observed["rejections"],
        "invalid_dates_rejected": observed["rejections"] >= 2,
    }


def _search_facts(stdout: str, stderr: str) -> dict[str, Any]:
    observed = extract_search(stdout, stderr)
    names = observed["matched_names"]
    return {
        "section_present": observed["section_present"],
        "matched_names": names,
        "all_matches_returned": set(names) == {"Anna", "annabel"},
    }


def _id_facts(stdout: str, stderr: str) -> dict[str, Any]:
    pairs = re.findall(r"编号：(\d+)\s*\n\s*姓名：([^\r\n]+)", stdout)
    ids: dict[str, int] = {}
    for raw_id, name in pairs:
        ids.setdefault(name.strip(), int(raw_id))
    observed = "乙" in ids and "丙" in ids
    return {
        "observed_ids": ids,
        "both_ids_observed": observed,
        "ids_unique": len(set(ids.values())) == len(ids) if observed else False,
        "ids_strictly_increasing": ids.get("丙", 0) > ids.get("乙", 0) if observed else False,
    }


# T5 需要“新增 → 删除 → 再新增”才能看到编号复用；参考程序的三级菜单里有删除入口，
# 因此这里能真实观察。脚本顺序：加甲、加乙 → 搜索甲并删除 → 加丙 → 分别查看乙/丙
# 详情里的“编号：”行。
_T5_SCRIPT: tuple[str, ...] = (
    "20",
    "2", "1", "1", "甲", "13800138010", "2000 1 1", "北京", "A路1号", "0",
    "2", "1", "1", "乙", "13800138011", "2000 1 1", "北京", "B路2号", "0",
    "1", "9", "1", "甲", "4", "9", "0",
    "2", "1", "1", "丙", "13800138012", "2000 1 1", "北京", "C路3号", "0",
    "1", "9", "1", "丙", "4", "0",
    "9", "1", "乙", "5", "0",
    "0", "0",
)


# ---------------------------------------------------------------------------
# 任务 → 探针
# ---------------------------------------------------------------------------

PROBES: dict[str, LegacyProbe] = {
    "t1_phone_validation": LegacyProbe(
        task_id="t1_phone_validation",
        check_id="phone_non_digit",
        title="手机号必须为 11 位数字",
        rule_ids=("R1", "R2", "R3", "R4"),
        expectation="输入 1380013800a 后应被拒绝，最终保存 13800138000",
        script=by_id("phone_digits").script,
        inspect=_phone_facts,
        guard={"added": True, "search_entered": True},
        expected={"invalid_phone_rejected": True},
    ),
    "t2_birthday_today": LegacyProbe(
        task_id="t2_birthday_today",
        check_id="birthday_today",
        title="生日当天的联系人应出现在提醒里",
        rule_ids=("R1",),
        expectation="新增一个生日为今天的联系人，查询 0 天内的提醒，该联系人应出现",
        script=_birthday_script,
        inspect=_birthday_facts,
        guard={"section_present": True},
        expected={"today_birthday_reminded": True},
    ),
    "t3_date_validation": LegacyProbe(
        task_id="t3_date_validation",
        check_id="invalid_dates",
        title="日期校验应拒绝不存在的日期",
        rule_ids=("R3", "R4"),
        expectation="1900-02-29 与 2023-04-31 应被拒绝，2000-02-29 应被接受",
        script=by_id("date_validation").script,
        inspect=_date_facts,
        guard={"added": True},
        expected={"invalid_dates_rejected": True},
    ),
    "t4_name_search": LegacyProbe(
        task_id="t4_name_search",
        check_id="name_search_case",
        title="姓名关键字检索应不区分大小写且返回全部匹配",
        rule_ids=("R2", "R3"),
        expectation="关键字 an 应同时匹配 Anna 与 annabel 两个联系人",
        script=by_id("name_search_case").script,
        inspect=_search_facts,
        guard={"section_present": True},
        expected={"all_matches_returned": True},
    ),
    "t5_id_allocation": LegacyProbe(
        task_id="t5_id_allocation",
        check_id="delete_then_add_id",
        title="删除后新增不得复用编号，且新编号应递增",
        rule_ids=("R1", "R2", "R3"),
        expectation="加甲、乙后删除甲，再加丙：丙的编号应大于乙且不与乙重复",
        script=_T5_SCRIPT,
        inspect=_id_facts,
        guard={"both_ids_observed": True},
        expected={"ids_unique": True, "ids_strictly_increasing": True},
    ),
}


def probe_for(task_id: str) -> LegacyProbe | None:
    """返回任务的单文件探针；没有探针时返回 None（由调用方决定如何提示）。"""
    return PROBES.get(task_id)
