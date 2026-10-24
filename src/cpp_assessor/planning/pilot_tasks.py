"""Exact task versions supported by the bounded WASM teaching pilot.

Metadata is shared by execution and historical-result presentation. This module
does not import execution backends or grant a scoring qualification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    required_seed_case_ids: tuple[str, ...]
    faulty_created_cases: tuple[str, ...]
    faulty_name_exclusions: Mapping[str, tuple[str, ...]]
    mutable_file: str = "validators.cpp"
    fixture_overrides: tuple[str, ...] = ("validators.cpp",)
    faulty_match_ids: Mapping[str, tuple[int, ...]] = field(default_factory=lambda: MappingProxyType({}))
    state_preservation_policy: str = "all_initial_contacts_v1"
    ordered_observation_keys: tuple[str, ...] = ()
    faulty_id_lists: Mapping[str, tuple[int, ...]] = field(default_factory=lambda: MappingProxyType({}))

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
            if case_id in self.faulty_created_cases:
                return {identifier: "MISMATCH" for identifier in self.assertion_ids}
            if case_id in self.faulty_name_exclusions:
                result["a_names"] = "MISMATCH"
            if case_id in self.faulty_match_ids:
                result["a_matches"] = "MISMATCH"
            if case_id in self.faulty_id_lists:
                return {identifier: "MISMATCH" for identifier in self.assertion_ids}
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
        declared_faulty_cases=("c_non_digit_11",), required_seed_case_ids=("c_non_digit_11",), faulty_created_cases=("c_non_digit_11",), faulty_name_exclusions=MappingProxyType({}),
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
        required_seed_case_ids=("c_today_exact",),
        faulty_created_cases=(),
        faulty_name_exclusions=MappingProxyType({"c_today_exact": ("张伟",), "c_today_window": ("张伟",), "c_leap_day": ("钱七",), "challenge_year_boundary": ("岁末",), "challenge_leap_boundary": ("二月今日",)}),
    ),
    PilotTaskProfile(
        task_id="t3_date_validation", version="1.0.0", title="T3 生日日期校验",
        calibration_policy="t3-scaffold-regression-v1",
        challenge_policy="t3-target-challenges-v1",
        qualification="T3_REGRESSION_CALIBRATED",
        formal_case_ids=("c_valid_leap", "c_valid_common", "c_invalid_1900_02_29", "c_invalid_year_low", "c_invalid_year_high", "c_invalid_april_31"),
        challenge_labels=MappingProxyType({
            "challenge_year_1900_valid": "1900 年下界合法日期",
            "challenge_year_2100_valid": "2100 年上界合法日期",
            "challenge_2100_feb29": "2100 年不是闰年",
            "challenge_common_feb29": "普通平年二月二十九日",
            "challenge_leap_feb30": "闰年二月三十日",
            "challenge_april_30_valid": "四月三十日合法边界",
            "challenge_january_31_valid": "一月三十一日合法边界",
            "challenge_february_31": "二月三十一日",
        }),
        assertion_ids=("a_created", "a_contacts"),
        scope="T3 固定工程的独立正确/缺陷夹具自动回归；不等同权威签审，不授予正式评分资格",
        challenge_scope="当前目标程序的八项额外日期规则与完整状态观测；不改写封存任务或六项正式用例计数，不授予正式评分资格",
        coverage_limits=("补充检查覆盖合法年份端点、世纪/普通平年的闰日、二月日数与大小月边界，并保留非空通讯录和原编号", "封存输入契约仅允许月份 1—12、日期 1—31；月份 0/13 和日期 0 未执行，不能宣称已验证这些输入", "有限回归不能证明对所有程序、所有输入和所有适配形态正确"),
        mutable_signature="bool isValidDate(int year, int month, int day) {",
        declared_faulty_cases=("c_invalid_1900_02_29",),
        required_seed_case_ids=("challenge_2100_feb29", "challenge_common_feb29", "challenge_year_1900_valid"),
        faulty_created_cases=("c_invalid_1900_02_29", "c_invalid_year_low", "c_invalid_year_high", "challenge_2100_feb29", "challenge_common_feb29"),
        faulty_name_exclusions=MappingProxyType({}),
    ),
    PilotTaskProfile(
        task_id="t4_name_search", version="1.0.0", title="T4 姓名检索",
        calibration_policy="t4-scaffold-regression-v1",
        challenge_policy="t4-target-challenges-v1",
        qualification="T4_REGRESSION_CALIBRATED",
        formal_case_ids=("c_case_insensitive", "c_chinese", "c_upper_keyword", "c_empty_keyword", "c_no_match"),
        challenge_labels=MappingProxyType({
            "challenge_infix": "纯中缀子串匹配",
            "challenge_all_duplicates": "全部匹配与重复姓名记录",
            "challenge_mixed_ascii": "混合 ASCII 大小写",
            "challenge_chinese_multiple": "中文姓名全部匹配",
            "challenge_empty_book": "空通讯录检索",
            "challenge_empty_keyword": "空关键字保留原通讯录",
            "challenge_space_literal": "空格关键字原值匹配",
            "challenge_punctuation_literal": "标点关键字原值匹配",
        }),
        assertion_ids=("a_matches", "a_contacts"),
        scope="T4 固定工程的独立正确/缺陷夹具自动回归；不等同权威签审，不授予正式评分资格",
        challenge_scope="当前目标程序的八项额外检索规则与完整状态观测；不改写封存任务或五项正式用例计数，不授予正式评分资格",
        coverage_limits=("补充检查覆盖非前缀子串、ASCII 大小写、全部匹配、重复姓名与原编号、中文及空格/标点原值；空关键字和空通讯录分开验证", "仅要求 ASCII 字母不区分大小写；未扩张为任意 Unicode 大小写或区域语言排序规则", "有限回归不能证明对所有程序、所有输入和所有适配形态正确"),
        mutable_signature="std::vector<Contact> ContactBook::searchByName(const std::string& keyword) const {",
        mutable_file="contactbook.cpp", fixture_overrides=("contactbook.cpp",),
        declared_faulty_cases=("c_case_insensitive",),
        required_seed_case_ids=("c_case_insensitive", "challenge_all_duplicates", "challenge_infix"),
        faulty_created_cases=(), faulty_name_exclusions=MappingProxyType({}),
        faulty_match_ids=MappingProxyType({"c_case_insensitive": (2,), "c_upper_keyword": (),
            "challenge_all_duplicates": (7,), "challenge_mixed_ascii": (), "challenge_chinese_multiple": (7,),
            "challenge_space_literal": (7,), "challenge_punctuation_literal": (7,)}),
    ),
    PilotTaskProfile(
        task_id="t5_id_allocation", version="1.0.0", title="T5 编号分配",
        calibration_policy="t5-scaffold-regression-v1",
        challenge_policy="t5-target-challenges-v1",
        qualification="T5_REGRESSION_CALIBRATED",
        formal_case_ids=("c_delete_then_add", "c_empty_book", "c_max_seed", "c_delete_nonexistent", "c_multi_add"),
        challenge_labels=MappingProxyType({
            "challenge_unsorted_seed": "乱序原编号取当前最大值",
            "challenge_delete_first": "删除首项后新增",
            "challenge_delete_middle": "删除中项后新增",
            "challenge_delete_last": "删除尾项后新增",
            "challenge_delete_max_reuse": "删除最高编号后连续新增",
            "challenge_multiple_add": "乱序通讯录连续新增",
            "challenge_delete_noop_only": "乱序通讯录仅删除不存在编号",
            "challenge_clear_then_add": "删除到空后重新新增",
        }),
        assertion_ids=("a_id_list", "a_contacts"),
        scope="T5 固定工程的独立正确/缺陷夹具自动回归；不等同权威签审，不授予正式评分资格",
        challenge_scope="当前目标程序的八项额外编号状态转换观测；保留编号列表的存储顺序，不改写封存任务或五项正式用例计数，不授予正式评分资格",
        coverage_limits=("补充检查覆盖乱序原编号、首中尾删除、连续新增、仅删除不存在编号及清空后新增；核验幸存原联系人全部字段和编号列表原序", "按作者正确实现取当前最大编号分配；删除最高编号后允许使用已空闲编号，清空后从 1 开始，不要求永久历史计数器", "仅验证已列小范围正整数编号；INT_MAX 溢出等边界未测试，有限回归不证明所有程序或输入正确"),
        mutable_signature="bool ContactBook::addContact(const std::string& name, const std::string& phone,\n                             int birthYear, int birthMonth, int birthDay) {",
        mutable_file="contactbook.cpp", fixture_overrides=("contactbook.cpp",),
        declared_faulty_cases=("c_delete_then_add",),
        required_seed_case_ids=("c_delete_then_add", "challenge_unsorted_seed", "challenge_delete_noop_only", "challenge_clear_then_add"),
        faulty_created_cases=(), faulty_name_exclusions=MappingProxyType({}),
        state_preservation_policy="surviving_initial_contacts_v1", ordered_observation_keys=("id_list",),
        faulty_id_lists=MappingProxyType({
            "c_delete_then_add": (1, 3, 3), "c_max_seed": (5, 9, 3), "c_multi_add": (2, 2, 3),
            "challenge_unsorted_seed": (7, 42, 3, 4), "challenge_delete_first": (42, 9, 3),
            "challenge_delete_middle": (7, 9, 3), "challenge_delete_last": (3, 1, 3),
            "challenge_multiple_add": (9, 2, 3, 4),
        }),
    ),
)

PILOT_TASK_REFS = frozenset(profile.task_ref for profile in _PROFILES)


def get_pilot_profile(task_id: str, version: str = "1.0.0") -> PilotTaskProfile | None:
    """Return only an explicitly supported task/version; unknowns fail closed."""
    return next((profile for profile in _PROFILES if (profile.task_id, profile.version) == (task_id, str(version))), None)
