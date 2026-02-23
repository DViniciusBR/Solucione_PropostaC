import sqlite3
from pathlib import Path
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

# Caminho do arquivo SQLite
# Usando base no próprio arquivo para evitar problemas de diretório de execução
DB_PATH = Path(__file__).resolve().parents[1] / "data" / "proposals.db"


def _init_db() -> None:
    """
    Cria o diretório e a tabela de controle de propostas, se ainda não existirem.

    Tabela: generated_proposals
      - deal_id      INTEGER  (parte da PK)
      - stage_id     INTEGER  (parte da PK)
      - generated_at TEXT     (ISO8601 em UTC)
      - PRIMARY KEY (deal_id, stage_id)
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS generated_proposals (
                deal_id INTEGER NOT NULL,
                stage_id INTEGER NOT NULL,
                generated_at TEXT NOT NULL,
                PRIMARY KEY (deal_id, stage_id)
            )
            """
        )
        conn.commit()

    logger.info(f"[PROPOSAL_STATE] Banco inicializado em {DB_PATH}")


# Inicializa o banco uma vez na importação do módulo
_init_db()


def has_generated(deal_id: int, stage_id: int) -> bool:
    """
    Verifica se já existe registro de proposta gerada para (deal_id, stage_id).
    """
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            SELECT 1
            FROM generated_proposals
            WHERE deal_id = ? AND stage_id = ?
            LIMIT 1
            """,
            (deal_id, stage_id),
        )
        exists = cur.fetchone() is not None

    logger.info(
        f"[PROPOSAL_STATE] has_generated(deal_id={deal_id}, stage_id={stage_id}) -> {exists}"
    )
    return exists


def mark_generated(deal_id: int, stage_id: int) -> None:
    """
    Marca como gerada a proposta para (deal_id, stage_id).
    Usa INSERT OR REPLACE para atualizar o generated_at se repetir.
    """
    ts = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO generated_proposals (deal_id, stage_id, generated_at)
            VALUES (?, ?, ?)
            """,
            (deal_id, stage_id, ts),
        )
        conn.commit()

    logger.info(
        f"[PROPOSAL_STATE] mark_generated(deal_id={deal_id}, stage_id={stage_id}, generated_at={ts})"
    )