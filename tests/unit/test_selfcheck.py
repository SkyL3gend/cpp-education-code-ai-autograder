# -*- coding: utf-8 -*-
"""基准程序实测的提取器与判定规则（用固定输出，不启动真实程序）。"""

from __future__ import annotations

from cpp_assessor.selfcheck.checks import (
    by_id,
    extract_birthday,
    extract_date,
    extract_phone,
    extract_search,
    extract_smoke,
)


def _observe(spec_id: str, stdout: str, stderr: str = "", termination: str = "EXITED"):
    spec = by_id(spec_id)
    observed = spec.extract(stdout, stderr)
    observed["termination"] = termination
    observed["exit_code"] = 0
    return spec.judge(observed)


MENU = """-------------通讯录-------------

1.查找联系人
2.添加联系人
3.导出与导入联系人
4.排序联系人(将按照姓氏拼音排序)
5.生日提醒
6.统计联系人
0.退出

Make your choice :"""


def test_smoke_menu_pass() -> None:
    verdict, _ = _observe("smoke_menu", MENU)
    assert verdict == "PASS"


def test_smoke_menu_not_observed_on_crash() -> None:
    verdict, detail = _observe("smoke_menu", MENU, termination="CRASH")
    assert verdict == "NOT_OBSERVED"
    assert "CRASH" in detail


def test_phone_defect_when_non_digit_accepted() -> None:
    stdout = (
        "*联系人添加成功!\n"
        "搜索结果---------\n4.[普通] 张三 1380013800a\n\n"
        "输入编号查看具体信息,输入0返回上一步:"
    )
    verdict, detail = _observe("phone_digits", stdout)
    assert verdict == "DEFECT"
    assert "1380013800a" in detail


def test_phone_pass_when_fixed_version_rejects() -> None:
    stdout = (
        "不合法的电话号码，请重新输入：\n*联系人添加成功!\n"
        "搜索结果---------\n4.[普通] 张三 13800138000\n\n"
        "输入编号查看具体信息,输入0返回上一步:"
    )
    verdict, _ = _observe("phone_digits", stdout)
    assert verdict == "PASS"


def test_phone_not_observed_without_search_section() -> None:
    verdict, _ = _observe("phone_digits", MENU)
    assert verdict == "NOT_OBSERVED"


def test_birthday_pass_and_defect() -> None:
    good = "========== 未来 0 天内生日提醒 ==========\n  [学生] 测试联系人2  →  2004-10-8\n"
    assert _observe("birthday_today", good)[0] == "PASS"
    bad = "========== 未来 0 天内生日提醒 ==========\n  [学生] 测试联系人1  →  2005-03-12\n"
    verdict, detail = _observe("birthday_today", bad)
    assert verdict == "DEFECT"
    assert "测试联系人2" in detail


def test_search_defect_when_case_sensitive() -> None:
    stdout = "---------搜索结果---------\n2.[普通] annabel 13800138002\n\n输入编号查看具体信息,输入0返回上一步:"
    verdict, detail = _observe("name_search_case", stdout)
    assert verdict == "DEFECT"
    assert "annabel" in detail


def test_search_pass_when_both_matched() -> None:
    stdout = (
        "---------搜索结果---------\n"
        "1.[普通] Anna 13800138001\n2.[普通] annabel 13800138002\n\n"
        "输入编号查看具体信息,输入0返回上一步:"
    )
    assert _observe("name_search_case", stdout)[0] == "PASS"


def test_date_pass_and_defect() -> None:
    good = "输入的日期不合法，请重新输入(如2000 1 1)：输入的日期不合法，请重新输入(如2000 1 1)：\n*联系人添加成功!"
    assert _observe("date_validation", good)[0] == "PASS"
    bad = "*联系人添加成功!"
    verdict, detail = _observe("date_validation", bad)
    assert verdict == "DEFECT"
    assert "拒绝提示 0 次" in detail


def test_extractors_are_pure_functions() -> None:
    assert extract_smoke(MENU, "")["menu_markers"] == list(
        ("1.查找联系人", "2.添加联系人", "0.退出")
    )
    assert extract_phone("", "")["recorded_phone"] is None
    assert extract_birthday("", "")["reminder_names"] == []
    assert extract_search("", "")["section_present"] is False
    assert extract_date("", "")["rejections"] == 0
