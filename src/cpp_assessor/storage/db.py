"""SQLite 持久化（架构实施方案 §10）。

SQLite 只存索引与状态，不存大对象；报告与日志以文件工件形式落在
``data/runs/<run_id>/`` 下，数据库里保存相对路径与 sha256。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..domain.canonical import utc_now

DB_SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    task_version TEXT NOT NULL,
    task_package_hash TEXT NOT NULL,
    mode TEXT NOT NULL,
    workflow_state TEXT NOT NULL,
    execution_status TEXT,
    assessment_status TEXT,
    grade_eligible INTEGER,
    backend TEXT,
    submission_id TEXT,
    plan_hash TEXT,
    report_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TEXT NOT NULL,
    state TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_versions (
    task_id TEXT NOT NULL,
    version TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    publication_status TEXT NOT NULL,
    validation_level TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, version)
);

CREATE TABLE IF NOT EXISTS calibrations (
    calibration_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    task_version TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    passed INTEGER NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class RunRow:
    run_id: str
    task_id: str
    task_version: str
    workflow_state: str
    execution_status: str | None
    assessment_status: str | None
    grade_eligible: bool | None
    backend: str | None
    submission_id: str | None
    plan_hash: str | None
    report_path: str | None
    error: str | None
    created_at: str
    updated_at: str
    finished_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "RunRow":
        return cls(
            run_id=row["run_id"],
            task_id=row["task_id"],
            task_version=row["task_version"],
            workflow_state=row["workflow_state"],
            execution_status=row["execution_status"],
            assessment_status=row["assessment_status"],
            grade_eligible=(
                None if row["grade_eligible"] is None else bool(row["grade_eligible"])
            ),
            backend=row["backend"],
            submission_id=row["submission_id"],
            plan_hash=row["plan_hash"],
            report_path=row["report_path"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            finished_at=row["finished_at"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "task_version": self.task_version,
            "workflow_state": self.workflow_state,
            "execution_status": self.execution_status,
            "assessment_status": self.assessment_status,
            "grade_eligible": self.grade_eligible,
            "backend": self.backend,
            "submission_id": self.submission_id,
            "plan_hash": self.plan_hash,
            "report_path": self.report_path,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (DB_SCHEMA_VERSION,),
            )
        legacy = self.path.with_name("assessor.db")
        if self.path.name == "app.sqlite3" and legacy.is_file():
            with self._connect() as connection:
                connection.execute("ATTACH DATABASE ? AS legacy", (legacy.resolve().as_uri() + "?mode=ro",))
                for table in ("runs", "run_events", "artifacts"):
                    exists = connection.execute("SELECT 1 FROM legacy.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                    if exists:
                        columns = [r[1] for r in connection.execute(f"PRAGMA main.table_info({table})")]
                        old_columns = {r[1] for r in connection.execute(f"PRAGMA legacy.table_info({table})")}
                        if set(columns) <= old_columns:
                            names = ",".join(columns)
                            connection.execute(f"INSERT OR IGNORE INTO main.{table} ({names}) SELECT {names} FROM legacy.{table}")

    # -- runs ---------------------------------------------------------------
    def create_run(
        self,
        *,
        run_id: str,
        task_id: str,
        task_version: str,
        task_package_hash: str,
        mode: str,
        backend: str,
        submission_id: str | None,
    ) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs(run_id, task_id, task_version, task_package_hash, mode,
                                 workflow_state, backend, submission_id, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    task_id,
                    task_version,
                    task_package_hash,
                    mode,
                    "CREATED",
                    backend,
                    submission_id,
                    now,
                    now,
                ),
            )

    def set_state(self, run_id: str, state: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET workflow_state=?, updated_at=? WHERE run_id=?",
                (state, utc_now(), run_id),
            )

    def set_plan_hash(self, run_id: str, plan_hash: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET plan_hash=?, updated_at=? WHERE run_id=?",
                (plan_hash, utc_now(), run_id),
            )

    def set_submission_id(self, run_id: str, submission_id: str) -> None:
        """导入完成后回填被测快照 ID（创建 run 时还不知道）。"""
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET submission_id=?, updated_at=? WHERE run_id=?",
                (submission_id, utc_now(), run_id),
            )

    def finish_run(
        self,
        run_id: str,
        *,
        workflow_state: str,
        execution_status: str | None = None,
        assessment_status: str | None = None,
        grade_eligible: bool | None = None,
        report_path: str | None = None,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs SET workflow_state=?, execution_status=?, assessment_status=?,
                    grade_eligible=?, report_path=?, error=?, updated_at=?, finished_at=?
                WHERE run_id=?
                """,
                (
                    workflow_state,
                    execution_status,
                    assessment_status,
                    None if grade_eligible is None else int(grade_eligible),
                    report_path,
                    error,
                    now,
                    now,
                    run_id,
                ),
            )

    def get_run(self, run_id: str) -> RunRow | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return RunRow.from_row(row) if row else None

    def list_runs(self, limit: int = 20) -> list[RunRow]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [RunRow.from_row(row) for row in rows]

    # -- events -------------------------------------------------------------
    def append_event(
        self, run_id: str, state: str, kind: str, payload: dict[str, Any] | None = None
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            seq = int(row[0])
            connection.execute(
                "INSERT INTO run_events(run_id, seq, at, state, kind, payload) VALUES(?,?,?,?,?,?)",
                (
                    run_id,
                    seq,
                    utc_now(),
                    state,
                    kind,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.execute(
                "UPDATE runs SET updated_at=? WHERE run_id=?", (utc_now(), run_id)
            )
        return seq

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT seq, at, state, kind, payload FROM run_events WHERE run_id=? ORDER BY seq",
                (run_id,),
            ).fetchall()
        return [
            {
                "seq": row["seq"],
                "at": row["at"],
                "state": row["state"],
                "kind": row["kind"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    # -- artifacts ----------------------------------------------------------
    def add_artifact(
        self,
        *,
        artifact_id: str,
        run_id: str,
        kind: str,
        rel_path: str,
        sha256: str,
        size: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO artifacts(artifact_id, run_id, kind, rel_path, sha256, size, created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (artifact_id, run_id, kind, rel_path, sha256, size, utc_now()),
            )

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE run_id=? ORDER BY artifact_id", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # -- task versions ------------------------------------------------------
    def upsert_task_version(
        self,
        *,
        task_id: str,
        version: str,
        package_hash: str,
        publication_status: str,
        validation_level: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO task_versions
                    (task_id, version, package_hash, publication_status, validation_level, validated_at)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    task_id,
                    version,
                    package_hash,
                    publication_status,
                    validation_level,
                    utc_now(),
                ),
            )

    def task_versions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_versions ORDER BY task_id, version"
            ).fetchall()
        return [dict(row) for row in rows]

    # -- calibrations -------------------------------------------------------
    def save_calibration(
        self,
        *,
        calibration_id: str,
        task_id: str,
        task_version: str,
        package_hash: str,
        passed: bool,
        detail: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO calibrations
                    (calibration_id, task_id, task_version, package_hash, passed, detail, created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    calibration_id,
                    task_id,
                    task_version,
                    package_hash,
                    int(passed),
                    json.dumps(detail, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )

    def calibrations(self, task_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM calibrations"
        params: Iterable[Any] = ()
        if task_id:
            query += " WHERE task_id=?"
            params = (task_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["passed"] = bool(item["passed"])
            item["detail"] = json.loads(item["detail"])
            result.append(item)
        return result
