"""Regressions for fabricated observations, duplicate loss and history merging."""
from cpp_assessor.judge.extractors import contactbook_menu_v1, birthday_menu_v1, keyword_search_v1, contact_ids_v1, extractor_ids

BOUNDS = {"contacts_start": "[STATE]", "contacts_end": "[/STATE]", "contacts_empty_marker": "EMPTY"}


def test_name_only_cannot_prove_stored_phone_or_state():
    facts = contactbook_menu_v1("4.[普通] 张三\n", {"name": "张三", "phone": "13800138000", "birthday": "2000-01-01"})
    assert "contacts_after" not in facts
    assert "saved_phone" not in facts


def test_initial_contacts_are_not_action_after_evidence():
    facts = contactbook_menu_v1("1.[普通] Other\n", {"name": "Candidate", "initial_contacts": [{"id": 7, "name": "Original"}]})
    assert "contacts_after" not in facts
    assert "created" not in facts


def test_actual_phone_never_replaced_from_input():
    facts = keyword_search_v1("[SEARCH]\n#4 Anna 手机:99999999999 生日:2000-01-01\n[/SEARCH]", {"search_start": "[SEARCH]", "search_end": "[/SEARCH]", "initial_contacts": [{"id": 1, "name": "Anna", "phone": "13800138000"}]})
    assert facts["matches"][0]["phone"] == "99999999999"
    assert facts["matches"][0]["id"] == 4
    assert "contacts_after" not in facts


def test_complete_actual_state_keeps_duplicates_and_identifiers():
    stdout = "[STATE]\n#4 Anna 手机:13800138000 生日:2000-01-01\n#5 Anna 手机:13800138000 生日:2000-01-01\n[/STATE]"
    facts = contactbook_menu_v1(stdout, {**BOUNDS, "name": "Anna", "renumber_ids": True})
    assert [r["id"] for r in facts["contacts_after"]] == [4, 5]
    assert len(facts["contacts_after"]) == 2
    assert "saved_phone" not in facts  # ambiguous record, don't select arbitrarily


def test_only_final_complete_section_is_used():
    stdout = "[STATE]\n#1 Anna 手机:13800138000 生日:2000-01-01\n[/STATE]\n[STATE]\nEMPTY\n[/STATE]"
    facts = contactbook_menu_v1(stdout, {**BOUNDS, "name": "Anna"})
    assert facts["contacts_after"] == []
    assert facts["created"] is False


def test_no_output_is_not_an_observed_empty_book():
    assert "contacts_after" not in contactbook_menu_v1("", BOUNDS)
    assert "contacts_after" not in contactbook_menu_v1("[STATE][/STATE]", BOUNDS)
    assert "contacts_after" not in contactbook_menu_v1("[STATE]\nunknown format EMPTY\n[/STATE]", BOUNDS)
    assert "contacts_after" not in contactbook_menu_v1("[STATE]\n#4 Anna 手机:13800138000 生日:2000-01-01\nunknown record\n[/STATE]", BOUNDS)


def test_reminder_requires_its_own_section_and_keeps_duplicate_names():
    facts = birthday_menu_v1("[REM]\n[普通] Anna → 2000-01-01\n[普通] Anna → 2000-01-01\n[/REM]", {"reminders_start": "[REM]", "reminders_end": "[/REM]", "initial_contacts": [{"name": "NotObserved"}]})
    assert facts["names"] == ["Anna", "Anna"]
    assert "contacts_after" not in facts


def test_identifier_observation_does_not_apply_offsets_or_watch_filter():
    text = "[STATE]\n编号：4\n姓名：Anna\n电话：13800138000\n生日：2000 1 1\n编号：5\n姓名：Other\n电话：13900139000\n生日：2001 2 3\n[/STATE]"
    facts = contact_ids_v1(text, {**BOUNDS, "id_offset": -3, "watch_names": ["Anna"]})
    assert facts["id_list"] == [4, 5]


def test_all_extractors_still_registered():
    assert {"contactbook_menu_v1", "birthday_menu_v1", "keyword_search_v1", "contact_ids_v1", "json_stdout_v1"} <= extractor_ids()
