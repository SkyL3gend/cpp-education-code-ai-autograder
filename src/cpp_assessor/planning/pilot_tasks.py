"""Exact task versions supported by the bounded WASM teaching pilot.

Metadata is shared by execution and historical-result presentation. This module
does not import execution backends or grant a scoring qualification.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class PilotTaskProfile:
    task_id: str
    version: str
    title: str
    calibration_policy: str
    challenge_policy: str
    qualification: str
    formal_case_ids: tuple[str, ...]
    challenge_labels: Mapping[str, str]
    assertion_ids: tuple[str, ...]
    scope: str
    challenge_scope: str
    coverage_limits: tuple[str, ...]
    mutable_signature: str
    declared_faulty_cases: tuple[str, ...]
    faulty_name_exclusions: Mapping[str, tuple[str, ...]]

    @property
    def policy_version(self) -> str:
        """Compatibility name for the supplementary-check policy."""
        return self.challenge_policy

    @property
    def task_ref(self) -> str:
        return f"{self.task_id}@{self.version}"

    @property
    def supplemental_case_ids(self) -> tuple[str, ...]:
        return tuple(self.challenge_labels)

    def expected_assertions(self, variant_id: str, case_id: str) -> dict[str, str]:
        """Exact reference signatures available to execution and history UI."""
        if variant_id not in {"correct", "faulty"} or case_id not in self.formal_case_ids + self.supplemental_case_ids:
            raise ValueError("未知参照夹具或用例")
        result = {identifier: "MATCH" for identifier in self.assertion_ids}
        if variant_id == "faulty":
            if self.task_id == "t1_phone_validation" and case_id == "c_non_digit_11":
                return {identifier: "MISMATCH" for identifier in self.assertion_ids}
            if case_id in self.faulty_name_exclusions:
                result["a_names"] = "MISMATCH"
        return result


_PROFILES = (
    PilotTaskProfile(
        task_id="t1_phone_validation", version="1.0.0", title="T1 手机号校验",
        calibration_policy="t1-scaffold-regression-v1",
        challenge_policy="t1-target-challenges-v1",
        qualification="T1_REGRESSION_CALIBRATED",
        formal_case_ids=("c_valid_11_ascii", "c_short_10", "c_long_12", "c_non_digit_11"),
        challenge_labels=MappingProxyType({"challenge_prefix_2": "11 位号码以 2 开头", "challenge_empty": "空手机号", "challenge_fullwidth": "全角数字手机号"}),
        assertion_ids=("a_created", "a_contacts"),
        scope="T1 固定工程的独立正确/缺陷夹具自动回归；不等同权威签审，不授予正式评分资格",
        challenge_scope="当前目标程序的额外规则观测；不改写封存任务或四项正式用例计数，不授予正式评分资格",
        coverage_limits=("封存 task@1.0.0 的 4 用例未单独覆盖 R2 的错误首位；本次独立挑战补充验证，不改写任务包", "有限回归不能证明对所有程序、所有输入和所有适配形态正确"),
        mutable_signature="bool isValidPhone(const std::string& phone) {",
        declared_faulty_cases=("c_non_digit_11",), faulty_name_exclusions=MappingProxyType({}),
    ),
    PilotTaskProfile(
        task_id="t2_birthday_today", version="1.0.0", title="T2 生日提醒",
        calibration_policy="t2-scaffold-regression-v1",
        challenge_policy="t2-target-challenges-v1",
        qualification="T2_REGRESSION_CALIBRATED",
        formal_case_ids=("c_today_exact", "c_today_window", "c_already_passed", "c_leap_day", "c_window_edge", "c_negative_window"),
        challenge_labels=MappingProxyType({"challenge_year_boundary": "跨年提醒窗口", "challenge_leap_boundary": "闰年二月边界", "challenge_duplicate_birthdays": "同日生日与重复姓名"}),
        assertion_ids=("a_names", "a_contacts"),
        scope="T2 固定工程的独立正确/缺陷夹具自动回归；不等同权威签审，不授予正式评分资格",
        challenge_scope="当前目标程序的额外规则观测；不改写封存任务或六项正式用例计数，不授予正式评分资格",
        coverage_limits=("补充检查覆盖跨年短窗口、合法闰日边界和重复姓名多重集；非闰年 2 月 29 日的提醒约定未在题意中声明，不能据此新增缺陷结论", "有限回归不能证明对所有程序、所有输入和所有适配形态正确"),
        mutable_signature="int daysUntilBirthday(int todayYear, int todayMonth, int todayDay,\n                      int birthMonth, int birthDay) {",
        declared_faulty_cases=("c_today_exact",),
        faulty_name_exclusions=MappingProxyType({"c_today_exact": ("张伟",), "c_today_window": ("张伟",), "c_leap_day": ("钱七",), "challenge_year_boundary": ("岁末",), "challenge_leap_boundary": ("二月今日",)}),
    ),
)

PILOT_TASK_REFS = frozenset(profile.task_ref for profile in _PROFILES)


def get_pilot_profile(task_id: str, version: str = "1.0.0") -> PilotTaskProfile | None:
    """Return only an explicitly supported task/version; unknowns fail closed."""
    return next((profile for profile in _PROFILES if (profile.task_id, profile.version) == (task_id, str(version))), None)
