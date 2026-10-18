# -*- coding: utf-8 -*-
"""生成五道小改题的任务包。

任务包目录：task_library/tasks/<task_id>/1.0.0/
每个任务包含：task.json、io_template.json、test_method.json、cases.json、
reference_suite.json、reference_verifier/、variants/{correct,faulty}/。

脚本是可重复运行的：每次都会重写任务包内容并重新计算引用哈希。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "task_library"
BASE_PROJECT = LIBRARY / "projects" / "contactbook_base" / "1.0.0"
TASKS_ROOT = LIBRARY / "tasks"

SCHEMA = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_VERSION = "1.0"
TASK_VERSION = "1.0.0"

BUILD = {
    "toolchain_id": "gxx_minqw64_ucrt_x64",
    "exclude_globs": ["main_menu.cpp"],
    "harness_files": [
        "shared/harness/test_entry.cpp",
        "shared/harness/json_lite.h",
    ],
    "harness_include_dir": "shared/harness",
    "compiler_flags": ["-std=c++17", "-O2", "-Wall", "-Wextra"],
}

LIMITS = {
    "limits_id": "std_default",
    "compile_wall_ms": 60000,
    "case_wall_ms": 3000,
    "total_wall_ms": 900000,
    "compile_cpu_ms": 60000,
    "case_cpu_ms": 2000,
    "cpu_rate_percent": 80,
    "compile_memory_bytes": 1073741824,
    "case_memory_bytes": 268435456,
    "compile_process_count": 8,
    "case_process_count": 4,
    "output_bytes": 1048576,
    "workdir_bytes": 104857600,
}

CAPABILITIES = {
    "needs_cpu_limit": True,
    "needs_memory_limit": True,
    "needs_wall_timeout": True,
    "needs_process_tree_kill": True,
    "needs_network_block": True,
}


# ---------------------------------------------------------------------------
# 基础构件
# ---------------------------------------------------------------------------


def birthday(year: int, month: int, day: int) -> dict[str, int]:
    return {"year": year, "month": month, "day": day}


def contact(cid: int, name: str, phone: str, birth: dict[str, int]) -> dict:
    return {"id": cid, "name": name, "phone": phone, "birthday": birth}


def observed(c: dict) -> dict:
    b = c["birthday"]
    return {
        "id": c["id"],
        "name": c["name"],
        "phone": c["phone"],
        "birthday": f"{b['year']:04d}-{b['month']:02d}-{b['day']:02d}",
    }


def observed_all(items: list[dict]) -> list[dict]:
    return [observed(item) for item in items]


def birthday_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["year", "month", "day"],
        "properties": {
            "year": {"type": "integer", "minimum": 0, "maximum": 9999},
            "month": {"type": "integer", "minimum": 1, "maximum": 12},
            "day": {"type": "integer", "minimum": 1, "maximum": 31},
        },
    }


def contact_input_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "name", "phone", "birthday"],
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "phone": {"type": "string"},
            "birthday": birthday_schema(),
        },
    }


def contact_output_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "name", "phone", "birthday"],
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "phone": {"type": "string"},
            "birthday": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
        },
    }


def create_contact_input() -> dict:
    return {
        "$schema": SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "required": ["initial_contacts", "candidate"],
        "properties": {
            "initial_contacts": {
                "type": "array",
                "items": {"$ref": "#/$defs/contact"},
            },
            "candidate": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "phone", "birthday"],
                "properties": {
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                    "birthday": birthday_schema(),
                },
            },
        },
        "$defs": {"contact": contact_input_schema()},
    }


def create_contact_output() -> dict:
    return {
        "$schema": SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "required": ["created", "contacts_after"],
        "properties": {
            "created": {"type": "boolean"},
            "contacts_after": {"type": "array", "items": {"$ref": "#/$defs/contact"}},
        },
        "$defs": {"contact": contact_output_schema()},
    }


def reminders_input() -> dict:
    return {
        "$schema": SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "required": ["initial_contacts", "today", "days_ahead"],
        "properties": {
            "initial_contacts": {
                "type": "array",
                "items": {"$ref": "#/$defs/contact"},
            },
            "today": birthday_schema(),
            "days_ahead": {"type": "integer", "minimum": -1, "maximum": 3660},
        },
        "$defs": {"contact": contact_input_schema()},
    }


def reminders_output() -> dict:
    return {
        "$schema": SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "required": ["names", "contacts_after"],
        "properties": {
            "names": {"type": "array", "items": {"type": "string"}},
            "contacts_after": {"type": "array", "items": {"$ref": "#/$defs/contact"}},
        },
        "$defs": {"contact": contact_output_schema()},
    }


def search_input() -> dict:
    return {
        "$schema": SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "required": ["initial_contacts", "keyword"],
        "properties": {
            "initial_contacts": {
                "type": "array",
                "items": {"$ref": "#/$defs/contact"},
            },
            "keyword": {"type": "string"},
        },
        "$defs": {"contact": contact_input_schema()},
    }


def search_output() -> dict:
    return {
        "$schema": SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "required": ["matches", "contacts_after"],
        "properties": {
            "matches": {"type": "array", "items": {"$ref": "#/$defs/contact"}},
            "contacts_after": {"type": "array", "items": {"$ref": "#/$defs/contact"}},
        },
        "$defs": {"contact": contact_output_schema()},
    }


def id_allocation_input() -> dict:
    add_op = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "name", "phone", "birthday"],
        "properties": {
            "kind": {"const": "add"},
            "name": {"type": "string"},
            "phone": {"type": "string"},
            "birthday": birthday_schema(),
        },
    }
    delete_op = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "id"],
        "properties": {"kind": {"const": "delete"}, "id": {"type": "integer"}},
    }
    return {
        "$schema": SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "required": ["initial_contacts", "ops"],
        "properties": {
            "initial_contacts": {
                "type": "array",
                "items": {"$ref": "#/$defs/contact"},
            },
            "ops": {"type": "array", "items": {"oneOf": [add_op, delete_op]}},
        },
        "$defs": {"contact": contact_input_schema()},
    }


def id_allocation_output() -> dict:
    return {
        "$schema": SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "required": ["id_list", "contacts_after"],
        "properties": {
            "id_list": {"type": "array", "items": {"type": "integer"}},
            "contacts_after": {"type": "array", "items": {"$ref": "#/$defs/contact"}},
        },
        "$defs": {"contact": contact_output_schema()},
    }


def assertion(
    assertion_id: str, rules: list[str], operator: str, key: str, pointer: str
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "assertion_id": assertion_id,
        "rule_ids": rules,
        "operator": operator,
        "actual_observation_key": key,
        "expected_source": {"kind": "CASE_EXPECTED", "pointer": pointer},
        "normalization_id": "identity_v1",
        "required": True,
    }


def task_case(
    case_id: str,
    rules: list[str],
    partitions: list[str],
    assertions: list[str],
    input_obj: dict,
    expected: dict,
    purpose: str = "ASSESSMENT",
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "rule_ids": rules,
        "partition_ids": partitions,
        "assertion_ids": assertions,
        "purpose": purpose,
        "input": input_obj,
        "expected": expected,
        "fixture_ids": [],
        "limits_override": None,
        "oracle_status": "CONFIRMED",
    }


def partition(partition_id: str, description: str, kind: str, rules: list[str]) -> dict:
    return {
        "partition_id": partition_id,
        "description": description,
        "kind": kind,
        "rule_ids": rules,
    }


def template(
    task_id: str,
    *,
    operation_id: str,
    input_schema: dict,
    output_schema: dict,
    observations: list[str],
    semantics: dict[str, str],
    preconditions: list[str],
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "template_id": f"tpl_{task_id}",
        "version": TASK_VERSION,
        "operation_id": operation_id,
        "subject_level": "FUNCTION",
        "input_schema": input_schema,
        "output_schema": output_schema,
        "field_semantics": semantics,
        "preconditions": preconditions,
        "steps": {
            "setup": [
                {"step_id": "seed_initial_contacts", "action": "seed_contacts"}
            ],
            "action": [{"step_id": "invoke_operation", "action": operation_id}],
            "observe": [
                {
                    "step_id": f"read_{key}",
                    "action": "read_json_field",
                    "pointer": f"/{key}",
                }
                for key in observations
            ],
            "teardown": [],
        },
        "pre_operations": [],
        "required_observations": observations,
        "allowed_transforms": ["identity_v1"],
        "encoding": "utf-8",
        "exit_contract": {
            "success_exit_codes": [0],
            "observation_channel": "stdout_line",
        },
        "capability_requirements": dict(CAPABILITIES),
    }


def method(
    task_id: str,
    *,
    rule_ids: list[str],
    partitions: list[dict],
    boundaries: list[dict],
    assertions: list[dict],
    observations: list[str],
    action: str,
    faults: list[dict],
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "method_id": f"method_{task_id}",
        "version": TASK_VERSION,
        "subject_level": "FUNCTION",
        "rule_refs": rule_ids,
        "input_partitions": partitions,
        "boundary_requirements": boundaries,
        "setup_steps": ["seed_initial_contacts"],
        "action_steps": [action],
        "observations": observations,
        "assertions": assertions,
        "coverage_requirements": {
            "required_partitions": [p["partition_id"] for p in partitions],
            "required_assertions": [a["assertion_id"] for a in assertions],
        },
        "reference_verifier_ref": {},  # 由 write_task 填充
        "known_fault_matrix": faults,
    }


# ---------------------------------------------------------------------------
# 五道题
# ---------------------------------------------------------------------------


def task_t1() -> dict:
    task_id = "t1_phone_validation"
    rules = [
        ("R1", "手机号必须恰好 11 个字符。"),
        ("R2", "手机号必须以 '1' 开头。"),
        ("R3", "手机号第 2—11 位必须全部是数字字符 '0'—'9'。"),
        ("R4", "手机号不合法时 addContact 必须返回 false，且通讯录内容保持不变。"),
    ]
    assertions = [
        assertion("a_created", ["R1", "R2", "R3", "R4"], "EQUALS", "created", "/created"),
        assertion("a_contacts", ["R4"], "MULTISET_EQUALS", "contacts_after", "/contacts_after"),
    ]
    partitions = [
        partition("p_valid_11_ascii", "合法：11 位、以 1 开头、全数字", "valid", ["R1", "R2", "R3"]),
        partition("p_short_10", "边界：10 位手机号", "boundary", ["R1"]),
        partition("p_long_12", "边界：12 位手机号", "boundary", ["R1"]),
        partition("p_non_digit_11", "业务非法：11 位但含非数字字符", "business_invalid", ["R3", "R4"]),
    ]
    existing = contact(7, "已有联系人", "13900139000", birthday(1995, 5, 5))
    valid = contact(1, "张三", "13800138000", birthday(2000, 1, 1))
    cases = [
        task_case(
            "c_valid_11_ascii",
            ["R1", "R2", "R3"],
            ["p_valid_11_ascii"],
            ["a_created", "a_contacts"],
            {"initial_contacts": [], "candidate": {"name": "张三", "phone": "13800138000", "birthday": birthday(2000, 1, 1)}},
            {"created": True, "contacts_after": [observed(valid)]},
        ),
        task_case(
            "c_short_10",
            ["R1", "R4"],
            ["p_short_10"],
            ["a_created", "a_contacts"],
            {"initial_contacts": [], "candidate": {"name": "李四", "phone": "1380013800", "birthday": birthday(2000, 2, 2)}},
            {"created": False, "contacts_after": []},
        ),
        task_case(
            "c_long_12",
            ["R1", "R4"],
            ["p_long_12"],
            ["a_created", "a_contacts"],
            {"initial_contacts": [], "candidate": {"name": "王五", "phone": "138001380000", "birthday": birthday(2000, 3, 3)}},
            {"created": False, "contacts_after": []},
        ),
        task_case(
            "c_non_digit_11",
            ["R3", "R4"],
            ["p_non_digit_11"],
            ["a_created", "a_contacts"],
            {
                "initial_contacts": [existing],
                "candidate": {"name": "赵六", "phone": "1380013800a", "birthday": birthday(2000, 4, 4)},
            },
            {"created": False, "contacts_after": [observed(existing)]},
        ),
    ]
    return {
        "task_id": task_id,
        "version": TASK_VERSION,
        "title": "T1 手机号校验：11 位数字且以 1 开头",
        "description": (
            "简化通讯录的 addContact 在新增联系人前校验手机号。"
            "当前实现只检查长度与首位，无法识别后 10 位里的非数字字符。"
            "要求修正 isValidPhone，使 11 位非全数字的手机号被拒绝，且拒绝时通讯录不变。"
        ),
        "rules": rules,
        "operation_id": "create_contact",
        "template": template(
            task_id,
            operation_id="create_contact",
            input_schema=create_contact_input(),
            output_schema=create_contact_output(),
            observations=["created", "contacts_after"],
            semantics={
                "initial_contacts": "动作开始前通讯录中已有的联系人，按给定顺序写入。",
                "candidate": "本次尝试新增的联系人；phone 原样传入，不做预校验。",
                "created": "addContact 的返回值：是否成功创建。",
                "contacts_after": "动作执行后通讯录的完整投影，不得过滤或补全。",
            },
            preconditions=["通讯录可以先写入 initial_contacts", "动作只执行一次创建尝试"],
        ),
        "method": method(
            task_id,
            rule_ids=[r for r, _ in rules],
            partitions=partitions,
            boundaries=[
                {"boundary_id": "b_length", "description": "长度边界：10 / 11 / 12", "required_partitions": ["p_short_10", "p_long_12"]},
                {"boundary_id": "b_digit_class", "description": "字符类别边界：数字与非数字混排", "required_partitions": ["p_non_digit_11"]},
            ],
            assertions=assertions,
            observations=["created", "contacts_after"],
            action="create_contact",
            faults=[
                {"fault_id": "f_digit_not_checked", "description": "只校验长度与首位，未校验后 10 位是否为数字", "detected_by_cases": ["c_non_digit_11"]}
            ],
        ),
        "cases": cases,
        "overrides": ["validators.cpp"],
        "correct_patch": [
            (
                """bool isValidPhone(const std::string& phone) {
    if (phone.size() != 11) {
        return false;
    }
    return phone[0] == '1';
}""",
                """bool isValidPhone(const std::string& phone) {
    if (phone.size() != 11) {
        return false;
    }
    if (phone[0] != '1') {
        return false;
    }
    for (std::size_t i = 0; i < phone.size(); ++i) {
        if (phone[i] < '0' || phone[i] > '9') {
            return false;
        }
    }
    return true;
}""",
            )
        ],
        "must_fail_cases": ["c_non_digit_11"],
    }


def task_t2() -> dict:
    task_id = "t2_birthday_today"
    rules = [
        ("R1", "今天是联系人生日时，daysUntilBirthday 必须返回 0，该联系人必须出现在提醒列表中。"),
        ("R2", "生日尚未到达且距离不超过 days_ahead 的联系人必须出现在提醒列表中。"),
        ("R3", "生日已经过去的联系人必须按下一年的生日计算，不得出现在本次提醒中。"),
        ("R4", "days_ahead 为负数时提醒列表必须为空。"),
        ("R5", "生日提醒操作不得修改通讯录内容。"),
    ]
    assertions = [
        assertion("a_names", ["R1", "R2", "R3", "R4"], "MULTISET_EQUALS", "names", "/names"),
        assertion("a_contacts", ["R5"], "MULTISET_EQUALS", "contacts_after", "/contacts_after"),
    ]
    partitions = [
        partition("p_today_exact", "当天生日，窗口 0 天", "valid", ["R1"]),
        partition("p_today_window", "当天与未来 7 天内都有生日", "valid", ["R1", "R2"]),
        partition("p_already_passed", "生日已过，应算到明年", "boundary", ["R3"]),
        partition("p_leap_day", "闰日 2 月 29 日的生日", "boundary", ["R1", "R2"]),
        partition("p_window_edge", "窗口边界：距离恰好大于 days_ahead", "boundary", ["R2"]),
        partition("p_negative_window", "业务非法：days_ahead 为负数", "business_invalid", ["R4"]),
    ]
    zhangwei = contact(1, "张伟", "13800138001", birthday(1990, 10, 18))
    lina = contact(2, "李娜", "13800138002", birthday(1990, 12, 1))
    wangqiang = contact(3, "王强", "13800138003", birthday(1990, 10, 17))
    qianqi = contact(4, "钱七", "13800138004", birthday(1992, 2, 29))
    zhouba = contact(5, "周八", "13800138005", birthday(1991, 10, 25))
    everyone = [zhangwei, lina, wangqiang, qianqi, zhouba]

    def reminder_case(
        case_id: str,
        rules_used: list[str],
        parts: list[str],
        contacts: list[dict],
        today: dict,
        days_ahead: int,
        names: list[str],
    ) -> dict:
        return task_case(
            case_id,
            rules_used,
            parts,
            ["a_names", "a_contacts"],
            {"initial_contacts": contacts, "today": today, "days_ahead": days_ahead},
            {"names": names, "contacts_after": observed_all(contacts)},
        )

    cases = [
        reminder_case("c_today_exact", ["R1", "R5"], ["p_today_exact"], everyone, birthday(2026, 10, 18), 0, ["张伟"]),
        reminder_case("c_today_window", ["R1", "R2", "R5"], ["p_today_window"], everyone, birthday(2026, 10, 18), 7, ["张伟", "周八"]),
        reminder_case("c_already_passed", ["R3", "R5"], ["p_already_passed"], [wangqiang], birthday(2026, 10, 18), 7, []),
        reminder_case("c_leap_day", ["R1", "R2", "R5"], ["p_leap_day"], everyone, birthday(2024, 2, 29), 1, ["钱七"]),
        reminder_case("c_window_edge", ["R2", "R5"], ["p_window_edge"], [zhouba], birthday(2026, 10, 18), 6, []),
        reminder_case("c_negative_window", ["R4", "R5"], ["p_negative_window"], [zhangwei], birthday(2026, 10, 18), -1, []),
    ]
    return {
        "task_id": task_id,
        "version": TASK_VERSION,
        "title": "T2 生日提醒：当天生日必须出现",
        "description": (
            "生日提醒按“距离今天还有多少天”筛选。当前实现把“生日等于今天”误判为已经过去，"
            "从而把当天生日推到明年，导致当天生日的人收不到提醒。"
            "要求修正 daysUntilBirthday，使当天生日返回 0，已过的生日才算到明年。"
        ),
        "rules": rules,
        "operation_id": "birthday_reminders",
        "template": template(
            task_id,
            operation_id="birthday_reminders",
            input_schema=reminders_input(),
            output_schema=reminders_output(),
            observations=["names", "contacts_after"],
            semantics={
                "initial_contacts": "动作开始前通讯录中已有的联系人。",
                "today": "由系统提供的“今天”，年/月/日。",
                "days_ahead": "提醒窗口天数；负数表示非法输入。",
                "names": "进入提醒列表的联系人姓名，顺序不限。",
                "contacts_after": "动作执行后通讯录的完整投影，本操作不得修改通讯录。",
            },
            preconditions=["通讯录可以先写入 initial_contacts", "today 表示调用当天的日期"],
        ),
        "method": method(
            task_id,
            rule_ids=[r for r, _ in rules],
            partitions=partitions,
            boundaries=[
                {"boundary_id": "b_zero", "description": "距离 0 天（当天生日）", "required_partitions": ["p_today_exact"]},
                {"boundary_id": "b_window", "description": "窗口边界 days_ahead 与 days_ahead+1", "required_partitions": ["p_window_edge"]},
                {"boundary_id": "b_leap", "description": "闰日 2 月 29 日", "required_partitions": ["p_leap_day"]},
            ],
            assertions=assertions,
            observations=["names", "contacts_after"],
            action="birthday_reminders",
            faults=[
                {"fault_id": "f_today_rolls_over", "description": "birth <= today 时把生日推到明年，导致当天生日被漏掉", "detected_by_cases": ["c_today_exact", "c_today_window", "c_leap_day"]}
            ],
        ),
        "cases": cases,
        "overrides": ["validators.cpp"],
        "correct_patch": [("    if (birth <= today) {", "    if (birth < today) {")],
        "must_fail_cases": ["c_today_exact"],
    }


def task_t3() -> dict:
    task_id = "t3_date_validation"
    rules = [
        ("R1", "年份必须在 1900—2100 之间（含端点）。"),
        ("R2", "月份必须在 1—12 之间。"),
        ("R3", "日期必须真实存在：2 月在闰年为 29 天、平年为 28 天，其它月份按实际天数。"),
        ("R4", "日期不合法时 addContact 必须返回 false，且通讯录内容保持不变。"),
    ]
    assertions = [
        assertion("a_created", ["R1", "R2", "R3", "R4"], "EQUALS", "created", "/created"),
        assertion("a_contacts", ["R4"], "MULTISET_EQUALS", "contacts_after", "/contacts_after"),
    ]
    partitions = [
        partition("p_valid_leap", "合法：闰年 2 月 29 日", "valid", ["R3"]),
        partition("p_valid_common", "合法：普通日期", "valid", ["R2", "R3"]),
        partition("p_invalid_1900_02_29", "业务非法：1900 年不是闰年，2 月 29 日不存在", "business_invalid", ["R3", "R4"]),
        partition("p_invalid_year_low", "业务非法：年份低于 1900", "business_invalid", ["R1", "R4"]),
        partition("p_invalid_year_high", "业务非法：年份高于 2100", "business_invalid", ["R1", "R4"]),
        partition("p_invalid_april_31", "业务非法：4 月没有 31 日", "business_invalid", ["R3", "R4"]),
    ]

    def date_case(case_id: str, rules_used: list[str], parts: list[str], name: str, phone: str, birth: dict, created: bool) -> dict:
        expected_contacts = [] if not created else [observed(contact(1, name, phone, birth))]
        return task_case(
            case_id,
            rules_used,
            parts,
            ["a_created", "a_contacts"],
            {"initial_contacts": [], "candidate": {"name": name, "phone": phone, "birthday": birth}},
            {"created": created, "contacts_after": expected_contacts},
        )

    cases = [
        date_case("c_valid_leap", ["R2", "R3"], ["p_valid_leap"], "闰年宝宝", "13800138000", birthday(2000, 2, 29), True),
        date_case("c_valid_common", ["R2", "R3"], ["p_valid_common"], "普通日期", "13800138001", birthday(1990, 5, 5), True),
        date_case("c_invalid_1900_02_29", ["R3", "R4"], ["p_invalid_1900_02_29"], "平年二月", "13800138002", birthday(1900, 2, 29), False),
        date_case("c_invalid_year_low", ["R1", "R4"], ["p_invalid_year_low"], "年份过低", "13800138003", birthday(1899, 5, 10), False),
        date_case("c_invalid_year_high", ["R1", "R4"], ["p_invalid_year_high"], "年份过高", "13800138004", birthday(2101, 1, 1), False),
        date_case("c_invalid_april_31", ["R3", "R4"], ["p_invalid_april_31"], "四月三十一", "13800138005", birthday(2023, 4, 31), False),
    ]
    return {
        "task_id": task_id,
        "version": TASK_VERSION,
        "title": "T3 生日日期校验：闰年与年份范围",
        "description": (
            "新增联系人时校验生日是否为真实存在的日期。当前实现只限制月份和日下界，"
            "2 月一律允许 29 日，也不限制年份范围，因此 1900-02-29、1899-05-10 等非法日期会被接受。"
            "要求修正 isValidDate。"
        ),
        "rules": rules,
        "operation_id": "create_contact",
        "template": template(
            task_id,
            operation_id="create_contact",
            input_schema=create_contact_input(),
            output_schema=create_contact_output(),
            observations=["created", "contacts_after"],
            semantics={
                "initial_contacts": "动作开始前通讯录中已有的联系人。",
                "candidate": "本次尝试新增的联系人；birthday 原样传入，不做预校验。",
                "created": "addContact 的返回值：是否成功创建。",
                "contacts_after": "动作执行后通讯录的完整投影。",
            },
            preconditions=["通讯录可以先写入 initial_contacts", "动作只执行一次创建尝试"],
        ),
        "method": method(
            task_id,
            rule_ids=[r for r, _ in rules],
            partitions=partitions,
            boundaries=[
                {"boundary_id": "b_leap_year", "description": "闰年边界：1900 / 2000", "required_partitions": ["p_valid_leap", "p_invalid_1900_02_29"]},
                {"boundary_id": "b_year_range", "description": "年份范围边界：1899 / 2101", "required_partitions": ["p_invalid_year_low", "p_invalid_year_high"]},
                {"boundary_id": "b_month_length", "description": "月份天数边界：4 月 31 日", "required_partitions": ["p_invalid_april_31"]},
            ],
            assertions=assertions,
            observations=["created", "contacts_after"],
            action="create_contact",
            faults=[
                {"fault_id": "f_feb_always_29", "description": "2 月一律允许 29 日，未按闰年判断", "detected_by_cases": ["c_invalid_1900_02_29"]},
                {"fault_id": "f_year_unbounded", "description": "未限制年份范围 1900—2100", "detected_by_cases": ["c_invalid_year_low", "c_invalid_year_high"]},
            ],
        ),
        "cases": cases,
        "overrides": ["validators.cpp"],
        "correct_patch": [
            (
                """bool isValidDate(int year, int month, int day) {
    if (month < 1 || month > 12) {
        return false;
    }
    if (day < 1) {
        return false;
    }
    if (month == 2) {
        return day <= 29;
    }
    return day <= daysInMonth(year, month);
}""",
                """bool isValidDate(int year, int month, int day) {
    if (year < 1900 || year > 2100) {
        return false;
    }
    if (month < 1 || month > 12) {
        return false;
    }
    if (day < 1 || day > daysInMonth(year, month)) {
        return false;
    }
    return true;
}""",
            )
        ],
        "must_fail_cases": ["c_invalid_1900_02_29"],
    }


def task_t4() -> dict:
    task_id = "t4_name_search"
    rules = [
        ("R1", "关键字按姓名子串匹配（包含即可，不要求前缀）。"),
        ("R2", "匹配不区分 ASCII 字母大小写。"),
        ("R3", "必须返回全部匹配的联系人，不能只返回第一个。"),
        ("R4", "关键字为空时返回空列表。"),
        ("R5", "姓名检索不得修改通讯录内容。"),
    ]
    assertions = [
        assertion("a_matches", ["R1", "R2", "R3", "R4"], "MULTISET_EQUALS", "matches", "/matches"),
        assertion("a_contacts", ["R5"], "MULTISET_EQUALS", "contacts_after", "/contacts_after"),
    ]
    partitions = [
        partition("p_case_insensitive", "两个联系人都含小写关键字，但姓名大小写不同", "valid", ["R1", "R2", "R3"]),
        partition("p_chinese", "中文姓名子串匹配", "valid", ["R1", "R3"]),
        partition("p_upper_keyword", "边界：全大写关键字", "boundary", ["R2"]),
        partition("p_empty_keyword", "业务非法：空关键字", "business_invalid", ["R4"]),
        partition("p_no_match", "边界：无任何匹配", "boundary", ["R1"]),
    ]
    anna = contact(1, "Anna Liu", "13800138001", birthday(1990, 1, 1))
    annabel = contact(2, "annabel", "13800138002", birthday(1990, 1, 2))
    wang = contact(3, "王强", "13800138003", birthday(1990, 1, 3))
    everyone = [anna, annabel, wang]

    def search_case(case_id: str, rules_used: list[str], parts: list[str], keyword: str, matches: list[dict]) -> dict:
        return task_case(
            case_id,
            rules_used,
            parts,
            ["a_matches", "a_contacts"],
            {"initial_contacts": everyone, "keyword": keyword},
            {"matches": observed_all(matches), "contacts_after": observed_all(everyone)},
        )

    cases = [
        search_case("c_case_insensitive", ["R1", "R2", "R3"], ["p_case_insensitive"], "an", [anna, annabel]),
        search_case("c_chinese", ["R1", "R3"], ["p_chinese"], "王", [wang]),
        search_case("c_upper_keyword", ["R2", "R3"], ["p_upper_keyword"], "LIU", [anna]),
        search_case("c_empty_keyword", ["R4"], ["p_empty_keyword"], "", []),
        search_case("c_no_match", ["R1", "R4"], ["p_no_match"], "zzz", []),
    ]
    return {
        "task_id": task_id,
        "version": TASK_VERSION,
        "title": "T4 姓名检索：不区分大小写并返回全部匹配",
        "description": (
            "按姓名关键字检索联系人。当前实现区分大小写，且找到第一个匹配就 break，"
            "导致“返回全部匹配”和“大小写不敏感”两条要求都不满足。"
            "要求修正 searchByName。"
        ),
        "rules": rules,
        "operation_id": "search_contact",
        "template": template(
            task_id,
            operation_id="search_contact",
            input_schema=search_input(),
            output_schema=search_output(),
            observations=["matches", "contacts_after"],
            semantics={
                "initial_contacts": "动作开始前通讯录中已有的联系人。",
                "keyword": "检索关键字，原样传入；空字符串是合法输入。",
                "matches": "命中的联系人完整对象，顺序不限。",
                "contacts_after": "动作执行后通讯录的完整投影，本操作不得修改通讯录。",
            },
            preconditions=["通讯录可以先写入 initial_contacts", "只执行一次检索调用"],
        ),
        "method": method(
            task_id,
            rule_ids=[r for r, _ in rules],
            partitions=partitions,
            boundaries=[
                {"boundary_id": "b_empty", "description": "空关键字", "required_partitions": ["p_empty_keyword"]},
                {"boundary_id": "b_case", "description": "大小写组合", "required_partitions": ["p_case_insensitive", "p_upper_keyword"]},
            ],
            assertions=assertions,
            observations=["matches", "contacts_after"],
            action="search_contact",
            faults=[
                {"fault_id": "f_case_sensitive", "description": "使用 std::string::find 直接比较，区分大小写", "detected_by_cases": ["c_case_insensitive", "c_upper_keyword"]},
                {"fault_id": "f_first_only", "description": "命中第一个后 break，未返回全部匹配", "detected_by_cases": ["c_case_insensitive"]},
            ],
        ),
        "cases": cases,
        "overrides": ["contactbook.cpp"],
        "correct_patch": [
            (
                """    for (std::size_t i = 0; i < contacts_.size(); ++i) {
        if (contacts_[i].name.find(keyword) != std::string::npos) {
            result.push_back(contacts_[i]);
            break;
        }
    }
    return result;
}""",
                """    std::string key;
    key.reserve(keyword.size());
    for (std::size_t i = 0; i < keyword.size(); ++i) {
        unsigned char ch = static_cast<unsigned char>(keyword[i]);
        key.push_back(static_cast<char>(std::tolower(ch)));
    }
    for (std::size_t i = 0; i < contacts_.size(); ++i) {
        std::string name = contacts_[i].name;
        for (std::size_t j = 0; j < name.size(); ++j) {
            unsigned char ch = static_cast<unsigned char>(name[j]);
            name[j] = static_cast<char>(std::tolower(ch));
        }
        if (name.find(key) != std::string::npos) {
            result.push_back(contacts_[i]);
        }
    }
    return result;
}""",
            ),
            ("#include <cstdio>", "#include <cctype>\n#include <cstdio>"),
        ],
        "must_fail_cases": ["c_case_insensitive"],
    }


def task_t5() -> dict:
    task_id = "t5_id_allocation"
    rules = [
        ("R1", "新增联系人的编号必须大于当前所有联系人的编号。"),
        ("R2", "删除联系人后不得复用仍被占用的编号。"),
        ("R3", "连续新增多个联系人时编号必须严格递增且唯一。"),
        ("R4", "删除不存在的编号不改变通讯录。"),
        ("R5", "编号分配不得修改已有联系人的编号。"),
    ]
    assertions = [
        assertion("a_id_list", ["R1", "R2", "R3"], "EQUALS", "id_list", "/id_list"),
        assertion("a_contacts", ["R5"], "MULTISET_EQUALS", "contacts_after", "/contacts_after"),
    ]
    partitions = [
        partition("p_delete_then_add", "删除中间编号后再新增", "valid", ["R1", "R2"]),
        partition("p_empty_book", "空通讯录新增", "valid", ["R1"]),
        partition("p_max_seed", "现有编号不连续且最大值大于条数", "boundary", ["R1"]),
        partition("p_delete_nonexistent", "业务非法：删除不存在的编号", "business_invalid", ["R4"]),
        partition("p_multi_add", "删除后连续新增两个", "boundary", ["R2", "R3"]),
    ]
    a1 = contact(1, "A", "13800000001", birthday(1990, 1, 1))
    b2 = contact(2, "B", "13800000002", birthday(1990, 1, 2))
    c3 = contact(3, "C", "13800000003", birthday(1990, 1, 3))
    e5 = contact(5, "E", "13800000005", birthday(1990, 1, 5))
    f9 = contact(9, "F", "13800000009", birthday(1990, 1, 9))

    def add_op(name: str, phone: str, birth: dict) -> dict:
        return {"kind": "add", "name": name, "phone": phone, "birthday": birth}

    def del_op(cid: int) -> dict:
        return {"kind": "delete", "id": cid}

    d4 = contact(4, "D", "13800000004", birthday(1990, 1, 4))
    g1 = contact(1, "G", "13800000011", birthday(1990, 2, 1))
    h10 = contact(10, "H", "13800000010", birthday(1990, 2, 2))
    i3 = contact(3, "I", "13800000013", birthday(1990, 2, 3))
    j3 = contact(3, "J", "13800000021", birthday(1990, 2, 4))
    k4 = contact(4, "K", "13800000022", birthday(1990, 2, 5))

    def id_case(case_id: str, rules_used: list[str], parts: list[str], initial: list[dict], ops: list[dict], id_list: list[int], after: list[dict]) -> dict:
        return task_case(
            case_id,
            rules_used,
            parts,
            ["a_id_list", "a_contacts"],
            {"initial_contacts": initial, "ops": ops},
            {"id_list": id_list, "contacts_after": observed_all(after)},
        )

    cases = [
        id_case(
            "c_delete_then_add",
            ["R1", "R2", "R5"],
            ["p_delete_then_add"],
            [a1, b2, c3],
            [del_op(2), add_op("D", "13800000004", birthday(1990, 1, 4))],
            [1, 3, 4],
            [a1, c3, d4],
        ),
        id_case(
            "c_empty_book",
            ["R1"],
            ["p_empty_book"],
            [],
            [add_op("G", "13800000011", birthday(1990, 2, 1))],
            [1],
            [g1],
        ),
        id_case(
            "c_max_seed",
            ["R1"],
            ["p_max_seed"],
            [e5, f9],
            [add_op("H", "13800000010", birthday(1990, 2, 2))],
            [5, 9, 10],
            [e5, f9, h10],
        ),
        id_case(
            "c_delete_nonexistent",
            ["R4", "R5"],
            ["p_delete_nonexistent"],
            [a1, b2],
            [del_op(99), add_op("I", "13800000013", birthday(1990, 2, 3))],
            [1, 2, 3],
            [a1, b2, i3],
        ),
        id_case(
            "c_multi_add",
            ["R2", "R3", "R5"],
            ["p_multi_add"],
            [a1, b2],
            [
                del_op(1),
                add_op("J", "13800000021", birthday(1990, 2, 4)),
                add_op("K", "13800000022", birthday(1990, 2, 5)),
            ],
            [2, 3, 4],
            [b2, j3, k4],
        ),
    ]
    return {
        "task_id": task_id,
        "version": TASK_VERSION,
        "title": "T5 编号分配：删除后新增不得重复",
        "description": (
            "新增联系人时分配自增编号。当前实现用“当前条数 + 1”生成编号，"
            "删除中间编号后再新增就会产生重复编号。要求修正编号分配逻辑，"
            "保证编号大于现有最大编号且已有联系人的编号不变。"
        ),
        "rules": rules,
        "operation_id": "id_allocation",
        "template": template(
            task_id,
            operation_id="id_allocation",
            input_schema=id_allocation_input(),
            output_schema=id_allocation_output(),
            observations=["id_list", "contacts_after"],
            semantics={
                "initial_contacts": "动作开始前通讯录中已有的联系人，id 按给定值写入。",
                "ops": "按顺序执行的动作列表：add 新增、delete 按编号删除。",
                "id_list": "全部动作执行完后的编号列表，按存储顺序。",
                "contacts_after": "全部动作执行完后的完整通讯录投影。",
            },
            preconditions=["通讯录可以先写入 initial_contacts", "ops 按给定顺序执行"],
        ),
        "method": method(
            task_id,
            rule_ids=[r for r, _ in rules],
            partitions=partitions,
            boundaries=[
                {"boundary_id": "b_gap", "description": "编号有空隙时的最大值边界", "required_partitions": ["p_max_seed"]},
                {"boundary_id": "b_multi", "description": "连续新增的递增性", "required_partitions": ["p_multi_add"]},
            ],
            assertions=assertions,
            observations=["id_list", "contacts_after"],
            action="id_allocation",
            faults=[
                {"fault_id": "f_size_plus_one", "description": "用 contacts_.size() + 1 分配编号，删除后产生重复", "detected_by_cases": ["c_delete_then_add", "c_max_seed", "c_multi_add"]}
            ],
        ),
        "cases": cases,
        "overrides": ["contactbook.cpp"],
        "correct_patch": [
            (
                """    Contact c;
    c.id = static_cast<int>(contacts_.size()) + 1;""",
                """    int nextId = 1;
    for (std::size_t i = 0; i < contacts_.size(); ++i) {
        if (contacts_[i].id >= nextId) {
            nextId = contacts_[i].id + 1;
        }
    }
    Contact c;
    c.id = nextId;""",
            )
        ],
        "must_fail_cases": ["c_delete_then_add"],
    }


TASK_BUILDERS = [task_t1, task_t2, task_t3, task_t4, task_t5]


# ---------------------------------------------------------------------------
# 写出任务包
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return sha256_bytes(path.read_bytes())


def apply_patch(source: str, patches: list[tuple[str, str]], label: str) -> str:
    result = source
    for old, new in patches:
        if old not in result:
            raise SystemExit(f"[{label}] 待替换片段不存在：\n{old[:120]}")
        result = result.replace(old, new, 1)
    return result


def write_variants(task_dir: Path, task: dict) -> None:
    for variant_id in ("correct", "faulty"):
        for relative in task["overrides"]:
            base_file = BASE_PROJECT / relative
            source = base_file.read_text(encoding="utf-8")
            if variant_id == "correct":
                source = apply_patch(source, task["correct_patch"], f"{task['task_id']}/{relative}")
            out = task_dir / "variants" / variant_id / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(source, encoding="utf-8", newline="\n")


def write_task(task: dict) -> Path:
    task_id = task["task_id"]
    task_dir = TASKS_ROOT / task_id / TASK_VERSION
    if task_dir.exists():
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True)

    write_variants(task_dir, task)

    assertions = task["method"]["assertions"]
    verifier_payload = {
        "schema_version": SCHEMA_VERSION,
        "verifier_id": f"rv_{task_id}",
        "task_id": task_id,
        "method_id": task["method"]["method_id"],
        "description": "模式一的参照判断程序：测试方法中的断言定义 + shared/harness 测试驱动。",
        "assertions": assertions,
    }
    verifier_sha = write_json(task_dir / "reference_verifier" / "assertions.json", verifier_payload)
    (task_dir / "reference_verifier" / "README.md").write_text(
        "# 参照判断程序（模式一）\n\n"
        "本目录的 `assertions.json` 是测试方法中断言定义的副本，用于固定“判断程序”的内容哈希。\n"
        "实际驱动来自 `task_library/shared/harness/test_entry.cpp`，它的哈希随任务包哈希一起固定。\n"
        "模式二会在此目录加入 LLM 生成的适配器与观测器；本原型只实现模式一。\n",
        encoding="utf-8",
        newline="\n",
    )
    verifier_ref = {"id": f"rv_{task_id}", "version": TASK_VERSION, "sha256": verifier_sha}

    io_sha = write_json(task_dir / "io_template.json", task["template"])

    method_payload = dict(task["method"])
    method_payload["reference_verifier_ref"] = verifier_ref
    method_sha = write_json(task_dir / "test_method.json", method_payload)

    cases_payload = {"schema_version": SCHEMA_VERSION, "cases": task["cases"]}
    write_json(task_dir / "cases.json", cases_payload)

    suite = {
        "schema_version": SCHEMA_VERSION,
        "suite_id": f"suite_{task_id}",
        "version": TASK_VERSION,
        "task_id": task_id,
        "variants": [
            {
                "variant_id": "correct",
                "role": "CORRECT",
                "description": "按功能要求修正后的实现，必须通过当前测试集",
                "overrides": task["overrides"],
                "expected_assessment_status": "PASS",
                "must_fail_cases": [],
            },
            {
                "variant_id": "faulty",
                "role": "FAULTY",
                "description": "保留已知缺陷的初始实现，必须被测试用例识别",
                "overrides": task["overrides"],
                "expected_assessment_status": "FAIL",
                "must_fail_cases": task["must_fail_cases"],
            },
        ],
    }
    write_json(task_dir / "reference_suite.json", suite)

    task_spec = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "version": TASK_VERSION,
        "title": task["title"],
        "description": task["description"],
        "rules": [{"rule_id": rid, "statement": text} for rid, text in task["rules"]],
        "case_ids": [case["case_id"] for case in task["cases"]],
        "io_template_ref": {"id": task["template"]["template_id"], "version": TASK_VERSION, "sha256": io_sha},
        "test_method_ref": {"id": task["method"]["method_id"], "version": TASK_VERSION, "sha256": method_sha},
        "reference_verifier_ref": dict(verifier_ref),
        "publication_status": "PUBLISHED",
        "oracle_provenance": (
            "模式一：期望输出由任务作者按规则推导，并经 reference_suite 的 correct 变体"
            "在本地沙箱后端上校准确认；判断依据是测试用例断言，不使用 LLM 判分。"
        ),
    }
    document = {
        "schema_version": SCHEMA_VERSION,
        "task_spec": task_spec,
        "project_ref": {
            "project_id": "contactbook_base",
            "version": "1.0.0",
            "root": "projects/contactbook_base/1.0.0",
        },
        "build": dict(BUILD),
        "limits": dict(LIMITS),
        "reference_suite": suite,
        "student_entry_note": (
            "学生从 task_library/projects/contactbook_base/1.0.0 复制工程后修改对应函数；"
            "不要修改 contactbook.h 中的函数签名（三版本头文件必须一致）。"
        ),
    }
    write_json(task_dir / "task.json", document)
    return task_dir


def main() -> int:
    if not BASE_PROJECT.is_dir():
        print(f"基准工程不存在：{BASE_PROJECT}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(ROOT / "src"))
    from cpp_assessor.tasks.loader import validate_task

    failed = 0
    for builder in TASK_BUILDERS:
        task = builder()
        task_dir = write_task(task)
        validation = validate_task(task_dir, LIBRARY)
        status = "OK" if validation.passed else "FAILED"
        print(f"[{status}] {task['task_id']} -> {task_dir} (hash {validation.package_hash[:12]})")
        if not validation.passed:
            failed += 1
            for check in validation.checks:
                if not check.passed:
                    print(f"    - {check.check_id}: {check.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
