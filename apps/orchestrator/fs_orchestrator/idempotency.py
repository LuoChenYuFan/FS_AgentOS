"""PostgreSQL action keys. Commits happen before the external call and before the checkpoint."""

from __future__ import annotations

from fs_skill_runtime.effects import ClaimResult, ClaimState

from fs_orchestrator.postgres import connect, migrate


class PostgresIdempotencyStore:
    def claim(self, *, key: str, tenant_id: str, run_id: str, action_type: str) -> ClaimResult:
        conn = connect()
        try:
            migrate(conn)
            with conn.transaction():
                inserted = conn.execute(
                    """
                    INSERT INTO idempotency_keys (key, tenant_id, run_id, action_type, status)
                    VALUES (%s, %s, %s, %s, 'in_progress')
                    ON CONFLICT (tenant_id, key) DO NOTHING
                    RETURNING key
                    """,
                    (key, tenant_id, run_id, action_type),
                ).fetchone()
                if inserted:
                    result = ClaimResult(state=ClaimState.PROCEED, holder_run_id=run_id)
                else:
                    row = conn.execute(
                        """
                        SELECT status, result_ref, run_id
                        FROM idempotency_keys
                        WHERE tenant_id = %s AND key = %s
                        """,
                        (tenant_id, key),
                    ).fetchone()
                    result = _claim_from_row(row)
            return result
        finally:
            conn.close()

    def complete(self, *, key: str, tenant_id: str, result_ref: str) -> None:
        conn = connect()
        try:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE idempotency_keys
                    SET status = 'completed', result_ref = %s
                    WHERE tenant_id = %s AND key = %s AND status = 'in_progress'
                    """,
                    (result_ref, tenant_id, key),
                )
        finally:
            conn.close()

    def mark_unknown(self, *, key: str, tenant_id: str) -> None:
        conn = connect()
        try:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE idempotency_keys
                    SET status = 'unknown'
                    WHERE tenant_id = %s AND key = %s AND status = 'in_progress'
                    """,
                    (tenant_id, key),
                )
        finally:
            conn.close()


def _claim_from_row(row: dict | None) -> ClaimResult:
    if row is None:
        return ClaimResult(state=ClaimState.BUSY)
    status = row["status"]
    if status == "completed":
        return ClaimResult(
            state=ClaimState.COMPLETED,
            result_ref=row["result_ref"],
            holder_run_id=row["run_id"],
        )
    if status == "unknown":
        return ClaimResult(state=ClaimState.UNKNOWN, holder_run_id=row["run_id"])
    return ClaimResult(state=ClaimState.BUSY, holder_run_id=row["run_id"])
