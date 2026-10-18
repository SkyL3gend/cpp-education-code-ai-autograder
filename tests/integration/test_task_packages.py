"""任务包静态校验：五个任务包必须能被加载并通过全部检查。"""

from __future__ import annotations

import pytest

from cpp_assessor.config import tasks_dir
from cpp_assessor.tasks.loader import load_task, validate_task

TASK_IDS = [
    "t1_phone_validation",
    "t2_birthday_today",
    "t3_date_validation",
    "t4_name_search",
    "t5_id_allocation",
]


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_task_package_passes_validation(task_id):
    result = validate_task(tasks_dir() / task_id / "1.0.0")
    assert result.passed, "\n".join(result.failures)


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_task_package_loads_with_cases_and_assertions(task_id):
    task = load_task(f"{task_id}@1.0.0")
    assert task.task_spec.task_id == task_id
    assert len(task.cases) >= 4
    assert task.assertions
    assert task.template.operation_id == task.method.action_steps[0]
    # 每个用例引用的分区与断言都必须存在
    for case in task.cases.values():
        assert set(case.partition_ids) <= {
            partition.partition_id for partition in task.method.input_partitions
        }
        assert set(case.assertion_ids) <= set(task.assertions)
