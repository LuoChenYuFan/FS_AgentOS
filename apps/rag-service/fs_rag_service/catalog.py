"""PostgreSQL keeps document metadata and the object-storage key."""

from __future__ import annotations

from fs_orchestrator.postgres import connect, migrate

from fs_rag_service.memory import Chunk


class PostgresCatalog:
    def save_document(
        self,
        *,
        document_id: str,
        tenant_id: str,
        title: str,
        oss_key: str,
        source_uri: str,
        media_type: str,
        index_version: str,
        content_hash: str,
    ) -> None:
        conn = connect()
        try:
            migrate(conn)
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO documents (
                        id, tenant_id, title, oss_key, source_uri,
                        media_type, index_version, content_hash
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        oss_key = EXCLUDED.oss_key,
                        source_uri = EXCLUDED.source_uri,
                        media_type = EXCLUDED.media_type,
                        index_version = EXCLUDED.index_version,
                        content_hash = EXCLUDED.content_hash
                    """,
                    (
                        document_id,
                        tenant_id,
                        title,
                        oss_key,
                        source_uri,
                        media_type,
                        index_version,
                        content_hash,
                    ),
                )
        finally:
            conn.close()

    def list_documents(self, *, tenant_id: str) -> list[dict[str, str | int]]:
        conn = connect()
        try:
            migrate(conn)
            rows = conn.execute(
                """
                SELECT d.id, d.title, d.media_type, d.created_at, COUNT(c.id) AS chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.id
                WHERE d.tenant_id = %s
                GROUP BY d.id
                ORDER BY d.created_at DESC
                """,
                (tenant_id,),
            ).fetchall()
            conn.commit()
        finally:
            conn.close()
        listed: list[dict[str, str | int]] = []
        for row in rows:
            created = row["created_at"]
            listed.append(
                {
                    "document_id": row["id"],
                    "title": row["title"],
                    "media_type": row["media_type"],
                    "chunk_count": int(row["chunk_count"]),
                    "created_at": created.isoformat(timespec="seconds") if created else "",
                }
            )
        return listed

    def get_document(self, document_id: str, *, tenant_id: str) -> dict[str, str] | None:
        conn = connect()
        try:
            migrate(conn)
            row = conn.execute(
                """
                SELECT id, title, oss_key
                FROM documents
                WHERE id = %s AND tenant_id = %s
                """,
                (document_id, tenant_id),
            ).fetchone()
            conn.commit()
        finally:
            conn.close()
        if row is None:
            return None
        return {
            "document_id": row["id"],
            "title": row["title"],
            "oss_key": row["oss_key"],
        }

    def delete_document(self, document_id: str, *, tenant_id: str) -> None:
        conn = connect()
        try:
            migrate(conn)
            with conn.transaction():
                found = conn.execute(
                    "SELECT id FROM documents WHERE id = %s AND tenant_id = %s",
                    (document_id, tenant_id),
                ).fetchone()
                if found is None:
                    return
                conn.execute(
                    "DELETE FROM document_chunks WHERE document_id = %s",
                    (document_id,),
                )
                conn.execute(
                    "DELETE FROM documents WHERE id = %s AND tenant_id = %s",
                    (document_id, tenant_id),
                )
        finally:
            conn.close()

    def find_by_title(self, *, tenant_id: str, title: str) -> dict[str, str] | None:
        conn = connect()
        try:
            migrate(conn)
            row = conn.execute(
                """
                SELECT id, oss_key, content_hash
                FROM documents
                WHERE tenant_id = %s AND title = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (tenant_id, title),
            ).fetchone()
            conn.commit()
        finally:
            conn.close()
        if row is None:
            return None
        return {
            "document_id": row["id"],
            "oss_key": row["oss_key"],
            "content_hash": row["content_hash"],
        }

    def chunk_ids(self, document_id: str) -> list[str]:
        conn = connect()
        try:
            migrate(conn)
            rows = conn.execute(
                "SELECT id FROM document_chunks WHERE document_id = %s ORDER BY ordinal",
                (document_id,),
            ).fetchall()
            conn.commit()
        finally:
            conn.close()
        return [row["id"] for row in rows]

    def delete_chunks(self, document_id: str) -> None:
        conn = connect()
        try:
            migrate(conn)
            with conn.transaction():
                conn.execute(
                    "DELETE FROM document_chunks WHERE document_id = %s",
                    (document_id,),
                )
        finally:
            conn.close()

    def save_chunk(self, chunk: Chunk) -> None:
        conn = connect()
        try:
            migrate(conn)
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO document_chunks (
                        id, document_id, tenant_id, ordinal, body, token_size, entity_id, source_uri
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.document_id,
                        chunk.tenant_id,
                        chunk.ordinal,
                        chunk.text,
                        len(chunk.text),
                        chunk.entity_id,
                        chunk.source_uri,
                    ),
                )
        finally:
            conn.close()

    def get_chunk(self, chunk_id: str, *, tenant_id: str) -> Chunk | None:
        conn = connect()
        try:
            migrate(conn)
            row = conn.execute(
                """
                SELECT id, document_id, tenant_id, ordinal, body, entity_id, source_uri
                FROM document_chunks
                WHERE id = %s AND tenant_id = %s
                """,
                (chunk_id, tenant_id),
            ).fetchone()
            conn.commit()
        finally:
            conn.close()
        if row is None:
            return None
        return Chunk(
            chunk_id=row["id"],
            document_id=row["document_id"],
            tenant_id=row["tenant_id"],
            text=row["body"],
            ordinal=row["ordinal"],
            source_uri=row["source_uri"],
            entity_id=row["entity_id"],
        )
