"""Deterministic test-model responses; no API or inferred reference answers."""
from __future__ import annotations
from cpp_assessor.domain.canonical import hash_json


def attach_evidence(payload, task, files, path, symbol):
    method = task.method
    payload["reference_learning"] = {
        "method_hash": hash_json(method.model_dump(mode="json")),
        "setup_steps": method.setup_steps, "action_steps": method.action_steps,
        "observations": method.observations,
        "assertion_ids": [a.assertion_id for a in method.assertions if a.required],
        "partition_ids": method.coverage_requirements.required_partitions,
        "explanation": "Initialize actual storage, invoke the target business operation, read actual records and compare every reference assertion without substituting the expected state.",
    }
    quote = next(line.strip() for line in files[path].splitlines() if symbol in line)
    payload["source_bindings"] = [
        {"role": role, "key": key, "file": path, "source_quote": quote, "symbol": symbol}
        for role, keys in (("ACTION", method.action_steps), ("OUTPUT", method.observations)) for key in keys
    ]
    for row in payload["migration_matrix"]:
        row["method_steps"] = method.setup_steps + method.action_steps
    return payload
