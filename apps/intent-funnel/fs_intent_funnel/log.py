"""One row per final decision. Promoting old fallbacks is a separate pass over this log."""

from __future__ import annotations

from typing import Protocol

from fs_common.contracts.intent import IntentDecisionRecord, IntentLayer
from fs_orchestrator.postgres import connect, migrate
from psycopg.types.json import Json


class DecisionLog(Protocol):
    def write(self, record: IntentDecisionRecord) -> None: ...

    def attach_run(self, decision_id: str, run_id: str) -> None: ...

    def mark_false_positive(
        self, decision_id: str, *, tenant_id: str, value: bool
    ) -> None: ...

    def repeated_fallbacks(self, *, min_repeats: int) -> list[tuple[str, str]]: ...


class MemoryDecisionLog:
    def __init__(self) -> None:
        self.rows: list[IntentDecisionRecord] = []

    def write(self, record: IntentDecisionRecord) -> None:
        self.rows.append(record)

    def attach_run(self, decision_id: str, run_id: str) -> None:
        for index, row in enumerate(self.rows):
            if row.id == decision_id:
                self.rows[index] = row.model_copy(update={"run_id": run_id})
                return

    def mark_false_positive(
        self, decision_id: str, *, tenant_id: str, value: bool
    ) -> None:
        for index, row in enumerate(self.rows):
            if row.id == decision_id and row.tenant_id == tenant_id:
                self.rows[index] = row.model_copy(update={"false_positive": value})
                return

    def repeated_fallbacks(self, *, min_repeats: int) -> list[tuple[str, str]]:
        counts: dict[tuple[str, str], int] = {}
        for row in self.rows:
            if (
                row.layer_hit is not IntentLayer.LARGE_LLM
                or not row.passed
                or row.false_positive is True
            ):
                continue
            key = (row.raw_text, row.intent)
            counts[key] = counts.get(key, 0) + 1
        return [key for key, count in counts.items() if count >= min_repeats]


def _open():
    conn = connect()
    migrate(conn)
    # migrate may leave a read transaction open. Commit it so the next block is the real one.
    conn.commit()
    return conn


class PostgresDecisionLog:
    def write(self, record: IntentDecisionRecord) -> None:
        conn = _open()
        try:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO intent_decisions (
                        id, run_id, tenant_id, raw_text, intent, route, confidence,
                        layer_hit, passed, latency_ms, false_positive, failure_reason,
                        attempt_reasons
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.id,
                        record.run_id,
                        record.tenant_id,
                        record.raw_text,
                        record.intent,
                        record.route.value,
                        record.confidence,
                        record.layer_hit.value,
                        record.passed,
                        record.latency_ms,
                        record.false_positive,
                        record.failure_reason,
                        Json(record.attempt_reasons),
                    ),
                )
        finally:
            conn.close()

    def attach_run(self, decision_id: str, run_id: str) -> None:
        conn = _open()
        try:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE intent_decisions
                    SET run_id = %s
                    WHERE id = %s
                      AND EXISTS (SELECT 1 FROM runs WHERE id = %s)
                    """,
                    (run_id, decision_id, run_id),
                )
        finally:
            conn.close()

    def mark_false_positive(
        self, decision_id: str, *, tenant_id: str, value: bool
    ) -> None:
        conn = _open()
        try:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE intent_decisions
                    SET false_positive = %s
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (value, decision_id, tenant_id),
                )
        finally:
            conn.close()

    def repeated_fallbacks(self, *, min_repeats: int) -> list[tuple[str, str]]:
        conn = _open()
        try:
            rows = conn.execute(
                """
                SELECT raw_text, intent
                FROM intent_decisions
                WHERE layer_hit = 'large_llm'
                  AND passed = TRUE
                  AND false_positive IS DISTINCT FROM TRUE
                GROUP BY raw_text, intent
                HAVING COUNT(*) >= %s
                """,
                (min_repeats,),
            ).fetchall()
            conn.commit()
        finally:
            conn.close()
        return [(row["raw_text"], row["intent"]) for row in rows]
