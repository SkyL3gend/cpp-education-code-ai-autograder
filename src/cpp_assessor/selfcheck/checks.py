# -*- coding: utf-8 -*-
"""基准程序的固定观察脚本与判定规则。

每个检查只做一件事：把事先写好的按键序列喂给程序（stdin），从 stdout 里提取一个
可核对的事实，再按规则给出 PASS / DEFECT / NOT_OBSERVED。

设计约束：
- 脚本只依赖菜单编号（1 查找 / 2 添加 / 5 生日提醒 / 0 退出），不依赖措辞；
- 提取失败或程序超时/崩溃 → NOT_OBSERVED，绝不当成 PASS；
- 观察值原样写进报告（``observed``），结论必须能由观察值复现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

PASS = "PASS"
DEFECT = "DEFECT"
NOT_OBSERVED = "NOT_OBSERVED"

MENU_MARKERS = ("1.查找联系人", "2.添加联系人", "0.退出")

# 添加一个“普通联系人”的按键序列（菜单编号，不依赖提示文字）。
_ADD_COMMON = ("2", "1", "1")


def _joined(*parts: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for part in parts:
        out.extend(part)
    return tuple(out)


# ---------------------------------------------------------------------------
# 观察值提取
# ---------------------------------------------------------------------------

def extract_smoke(stdout: str, stderr: str) -> dict[str, Any]:
    return {"menu_markers": [m for m in MENU_MARKERS if m in stdout]}


def extract_phone(stdout: str, stderr: str) -> dict[str, Any]:
    section = re.search(r"搜索结果-+(.*?)(?:输入编号查看|$)", stdout, re.S)
    match = (
        re.search(r"\[[^\]\r\n]+\]\s*张三\s+(\S+)", section.group(1))
        if section
        else None
    )
    return {
        "recorded_phone": match.group(1) if match else None,
        "rejection_prompts": stdout.count("不合法的电话号码"),
        "added": "*联系人添加成功!" in stdout,
        "search_entered": bool(section),
    }


def extract_birthday(stdout: str, stderr: str) -> dict[str, Any]:
    names = [
        m.group(2)
        for m in re.finditer(r"\[([^\]\r\n]+)\]\s*(\S+)\s*→\s*(\S+)", stdout)
    ]
    return {
        "reminder_names": names,
        "section_present": "生日提醒" in stdout,
    }


def extract_search(stdout: str, stderr: str) -> dict[str, Any]:
    section = re.search(r"搜索结果-+(.*?)(?:输入编号查看|请输入|$)", stdout, re.S)
    names = (
        re.findall(r"\d+\.\[[^\]\r\n]+\]\s*(\S+)\s+\S+", section.group(1))
        if section
        else []
    )
    return {"matched_names": names, "section_present": bool(section)}


def extract_date(stdout: str, stderr: str) -> dict[str, Any]:
    return {
        "rejections": stdout.count("输入的日期不合法"),
        "added": "*联系人添加成功!" in stdout,
    }


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def _terminated_normally(observed: dict[str, Any]) -> bool:
    return observed.get("termination") == "EXITED"


def judge_smoke(observed: dict[str, Any]) -> tuple[str, str]:
    if not _terminated_normally(observed):
        return NOT_OBSERVED, f"程序未正常退出（终止类型 {observed.get('termination')}），无法观察菜单"
    missing = [m for m in MENU_MARKERS if m not in observed["menu_markers"]]
    if missing:
        return NOT_OBSERVED, "主菜单缺少预期入口：" + "、".join(missing)
    return PASS, "主菜单入口齐全，程序能在输入 0 后正常退出"


def judge_phone(observed: dict[str, Any]) -> tuple[str, str]:
    if not _terminated_normally(observed):
        return NOT_OBSERVED, f"程序未正常退出（终止类型 {observed.get('termination')}）"
    if not observed["added"] or not observed["search_entered"]:
        return NOT_OBSERVED, "添加联系人流程没有跑完，未能观察到实际保存的电话号码"
    phone = observed["recorded_phone"]
    if phone is None:
        return NOT_OBSERVED, "搜索结果里没有找到“张三”，无法观察保存的电话号码"
    if phone == "13800138000":
        return PASS, "非数字手机号被拒绝，实际保存的是合法号码 13800138000"
    return DEFECT, f"非数字手机号 1380013800a 被接受，实际保存为 {phone}"


def judge_birthday(observed: dict[str, Any]) -> tuple[str, str]:
    if not _terminated_normally(observed):
        return NOT_OBSERVED, f"程序未正常退出（终止类型 {observed.get('termination')}）"
    if not observed["section_present"]:
        return NOT_OBSERVED, "没有出现生日提醒查询结果，无法观察"
    names = observed["reminder_names"]
    if "测试联系人2" not in names:
        return DEFECT, "今天是 10 月 8 日，生日当天的“测试联系人2”没有出现在提醒里"
    return PASS, f"生日当天的联系人被提醒：{', '.join(names) or '(无)'}"


def judge_search(observed: dict[str, Any]) -> tuple[str, str]:
    if not _terminated_normally(observed):
        return NOT_OBSERVED, f"程序未正常退出（终止类型 {observed.get('termination')}）"
    if not observed["section_present"]:
        return NOT_OBSERVED, "没有出现搜索结果区，无法观察"
    names = observed["matched_names"]
    expected = {"Anna", "annabel"}
    if set(names) == expected:
        return PASS, "关键字 an 同时匹配 Anna 与 annabel（不区分大小写）"
    return DEFECT, f"关键字 an 的匹配结果为 {names or '(无)'}，预期同时包含 Anna 与 annabel"


def judge_date(observed: dict[str, Any]) -> tuple[str, str]:
    if not _terminated_normally(observed):
        return NOT_OBSERVED, f"程序未正常退出（终止类型 {observed.get('termination')}）"
    if not observed["added"]:
        return NOT_OBSERVED, "添加联系人流程没有跑完，无法观察日期校验"
    rejections = observed["rejections"]
    if rejections >= 2:
        return PASS, "1900-02-29 与 2023-04-31 都被拒绝，随后接受了 2000-02-29"
    return DEFECT, f"非法日期被接受的次数超过预期（拒绝提示 {rejections} 次，应为 2 次）"


# ---------------------------------------------------------------------------
# 规则与脚本
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    title: str
    rule_ref: str | None
    expectation: str
    script: tuple[str, ...]
    extract: Callable[[str, str], dict[str, Any]]
    judge: Callable[[dict[str, Any]], tuple[str, str]]


CHECKS: tuple[CheckSpec, ...] = (
    CheckSpec(
        check_id="smoke_menu",
        title="程序可启动、主菜单可读取",
        rule_ref=None,
        expectation="程序启动后打印 1/2/0 三个菜单入口，并在输入 0 后退出",
        script=("20", "0"),
        extract=extract_smoke,
        judge=judge_smoke,
    ),
    CheckSpec(
        check_id="phone_digits",
        title="手机号必须为 11 位数字",
        rule_ref="R1",
        expectation="输入 1380013800a 应被拒绝；随后保存 13800138000",
        script=_joined(("20",), _ADD_COMMON, (
            "张三",
            "1380013800a",
            "13800138000",
            "2000 1 1",
            "北京",
            "朝阳路1号",
            "0",
            "1",
            "9",
            "1",
            "张三",
            "0",
            "0",
            "0",
        )),
        extract=extract_phone,
        judge=judge_phone,
    ),
    CheckSpec(
        check_id="birthday_today",
        title="生日当天的联系人应出现在提醒里",
        rule_ref="R2",
        expectation="以 2026-10-08 为今天、提前 0 天查询，应提醒生日为 10-08 的“测试联系人2”",
        script=("20", "5", "2026 10 8", "0", "0"),
        extract=extract_birthday,
        judge=judge_birthday,
    ),
    CheckSpec(
        check_id="name_search_case",
        title="姓名关键字检索应不区分大小写且返回全部匹配",
        rule_ref="R3",
        expectation="关键字 an 应同时匹配 Anna 与 annabel 两个联系人",
        script=_joined(
            ("20",),
            _ADD_COMMON,
            ("Anna", "13800138001", "2000 1 1", "北京", "A路1号"),
            ("1", "1"),
            ("annabel", "13800138002", "2000 1 1", "北京", "B路2号"),
            ("0", "1", "9", "1", "an", "0", "0", "0"),
        ),
        extract=extract_search,
        judge=judge_search,
    ),
    CheckSpec(
        check_id="date_validation",
        title="日期校验应拒绝不存在的日期",
        rule_ref="R4",
        expectation="1900-02-29 与 2023-04-31 应被拒绝，2000-02-29 应被接受",
        script=_joined(
            ("20",),
            _ADD_COMMON,
            (
                "李四",
                "13800138000",
                "1900 2 29",
                "2023 4 31",
                "2000 2 29",
                "上海",
                "南京路1号",
                "0",
                "0",
            ),
        ),
        extract=extract_date,
        judge=judge_date,
    ),
)


def by_id(check_id: str) -> CheckSpec:
    for spec in CHECKS:
        if spec.check_id == check_id:
            return spec
    raise KeyError(check_id)
