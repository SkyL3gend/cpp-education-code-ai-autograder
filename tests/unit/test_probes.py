"""单文件适配探针的纯数据检查（不编译、不运行程序）。"""

from __future__ import annotations

from datetime import date

from cpp_assessor.selfcheck.probes import PROBES, probe_for


def test_every_task_has_a_probe():
    assert set(PROBES) == {
        "t1_phone_validation",
        "t2_birthday_today",
        "t3_date_validation",
        "t4_name_search",
        "t5_id_allocation",
    }
    assert probe_for("t1_phone_validation") is not None
    assert probe_for("t9_unknown") is None


def test_probe_ids_and_rules_are_consistent():
    for task_id, probe in PROBES.items():
        assert probe.task_id == task_id
        assert probe.case_id == f"single_file_{probe.check_id}"
        assert probe.assertion_id == probe.case_id
        assert probe.rule_ids
        assert probe.expectation
        assert probe.expected
        assert probe.guard
        assert probe.script


def test_t2_probe_targets_todays_birthday():
    probe = PROBES["t2_birthday_today"]
    script = probe.resolved_script()
    today = date.today()
    birthday = f"{today.year} {today.month} {today.day}"
    # 新增联系人的生日与提醒查询的“当前日期”都必须落在今天。
    assert script.count(birthday) == 2
    assert "生日测试" in script
    assert "5" in script
    assert probe.expected == {"today_birthday_reminded": True}
