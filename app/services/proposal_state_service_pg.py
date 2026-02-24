import os
import logging
from datetime import datetime, timezone

import psycopg2

logger = logging.getLogger(__name__)

def _get_db_url() -> str:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL não configurada (use .env local ou env vars do servidor).")
    return db_url

def _connect():
    return psycopg2.connect(_get_db_url())

def _init_db() -> None:
    """
    Cria a tabela generated_proposals no Postgres.
    PK composta (deal_id, stage_id), igual ao SQLite.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS generated_proposals (
                    deal_id BIGINT NOT NULL,
                    stage_id BIGINT NOT NULL,
                    generated_at TEXT NOT NULL,
                    PRIMARY KEY (deal_id, stage_id)
                );
            """)
        conn.commit()

    logger.info("[PROPOSAL_STATE_PG] Tabela garantida no Postgres.")

# Inicializa ao importar
_init_db()

def has_generated(deal_id: int, stage_id: int) -> bool:
    """
    Verifica se já existe registro de proposta gerada para (deal_id, stage_id).
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM generated_proposals WHERE deal_id=%s AND stage_id=%s LIMIT 1;",
                (deal_id, stage_id)
            )
            exists = cur.fetchone() is not None

    logger.info(f"[PROPOSAL_STATE_PG] has_generated(deal_id={deal_id}, stage_id={stage_id}) -> {exists}")
    return exists

def mark_generated(deal_id: int, stage_id: int) -> None:
    """
    Marca como gerada a proposta para (deal_id, stage_id), com UPSERT.
    """
    ts = datetime.now(timezone.utc).isoformat()

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO generated_proposals (deal_id, stage_id, generated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (deal_id, stage_id)
                DO UPDATE SET generated_at = EXCLUDED.generated_at;
            """, (deal_id, stage_id, ts))
        conn.commit()

    logger.info(f"[PROPOSAL_STATE_PG] mark_generated(deal_id={deal_id}, stage_id={stage_id}, generated_at={ts})")