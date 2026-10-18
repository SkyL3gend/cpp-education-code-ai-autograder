"""注册提取器的解析单元测试（不编译、不运行程序）。"""

from __future__ import annotations

from cpp_assessor.judge.extractors import (
    birthday_menu_v1,
    contact_ids_v1,
    contactbook_menu_v1,
    extractor_ids,
    keyword_search_v1,
)


def test_all_menu_extractors_are_registered():
    assert {
        "contactbook_menu_v1",
        "birthday_menu_v1",
        "keyword_search_v1",
        "contact_ids_v1",
        "json_stdout_v1",
    } <= extractor_ids()


def test_birthday_extractor_reads_reminder_rows():
    stdout = (
        "========== 未来 0 天内生日提醒 ==========\n"
        "  [普通] 张伟  →  1990-10-18\n"
        "  [学生] 李娜  →  1990-12-01\n"
    )
    facts = birthday_menu_v1(
        stdout,
        {"initial_contacts": [{"id": 1, "name": "张伟", "phone": "1", "birthday": {"year": 1990, "month": 10, "day": 18}}]},
    )
    assert facts["names"] == ["张伟", "李娜"]
    assert facts["section_present"] is True
    assert facts["contacts_after"][0]["birthday"] == "1990-10-18"


def test_birthday_extractor_skips_baseline_names():
    stdout = "[普通] 测试联系人2  →  2004-10-08\n[普通] 张伟  →  1990-10-18\n"
    facts = birthday_menu_v1(stdout, {"baseline_names": ["测试联系人2"]})
    assert facts["names"] == ["张伟"]


def test_keyword_search_aligns_matches_to_declared_contacts():
    stdout = "1.[普通] Anna Liu 13800138001\n2.[普通] annabel 13800138002\n"
    initial = [
        {"id": 1, "name": "Anna Liu", "phone": "13800138001", "birthday": {"year": 1990, "month": 1, "day": 1}},
        {"id": 2, "name": "annabel", "phone": "13800138002", "birthday": {"year": 1990, "month": 1, "day": 2}},
        {"id": 3, "name": "王强", "phone": "13800138003", "birthday": {"year": 1990, "month": 1, "day": 3}},
    ]
    facts = keyword_search_v1(stdout, {"initial_contacts": initial})
    assert [item["id"] for item in facts["matches"]] == [1, 2]
    assert [item["birthday"] for item in facts["contacts_after"]] == ["1990-01-01", "1990-01-02", "1990-01-03"]


def test_contact_ids_reads_detail_blocks_with_offset():
    stdout = (
        "编号：4\n姓名：张三\n电话：13800138000\n生日：2000 1 1\n"
        "编号：5\n姓名：李四\n电话：13800138001\n生日：2001 2 3\n"
    )
    facts = contact_ids_v1(
        stdout,
        {
            "initial_contacts": [{"id": 1, "name": "张三", "phone": "13800138000", "birthday": "2000-01-01"}],
            "watch_names": ["张三", "李四"],
            "id_offset": -3,
        },
    )
    assert facts["id_list"] == [1, 2]
    assert facts["contacts_after"][-1] == {"id": 2, "name": "李四", "phone": "13800138001", "birthday": "2001-02-03"}


def test_contactbook_extractor_uses_declared_initial_contacts():
    stdout = "4.[普通] 张三 13800138000\n"
    facts = contactbook_menu_v1(
        stdout,
        {
            "name": "张三",
            "phone": "13800138000",
            "birthday": "2000-01-01",
            "initial_contacts": [{"id": 7, "name": "已有联系人", "phone": "13900139000", "birthday": "1995-05-05"}],
            "baseline_names": ["测试联系人1", "测试联系人2", "测试联系人3"],
        },
    )
    assert facts["created"] is True
    assert [item["id"] for item in facts["contacts_after"]] == [7, 8]


def test_contactbook_extractor_reads_hash_row_format():
    stdout = (
        "1 列表  2 添加  3 检索  4 生日提醒  5 删除  0 退出\n"
        ">   #1 张伟  手机:13800138001  生日:1990-10-18\n"
        "  #4 张三  手机:13800138000  生日:2000-01-01\n"
        "姓名关键字: 共找到 1 条:\n"
        "  #4 张三  手机:13800138000  生日:2000-01-01\n"
    )
    facts = contactbook_menu_v1(
        stdout,
        {
            "name": "张三",
            "phone": "13800138000",
            "birthday": "2000-01-01",
            "baseline_names": ["张伟"],
        },
    )
    assert facts["created"] is True
    assert facts["saved_phone"] == "13800138000"
    assert facts["contacts_after"] == [
        {"id": 1, "name": "张三", "phone": "13800138000", "birthday": "2000-01-01"}
    ]
