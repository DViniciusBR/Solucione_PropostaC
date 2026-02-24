import time
import logging
from fastapi import APIRouter
from app.services.pdf_service import gerar_pdf
from app.services.pipedrive_service import (
    buscar_deal,
    PipedriveError,
    PipedriveUnavailableError,
    PipedriveDealNotFoundError,
    PipedriveInvalidResponseError,
)
from app.services.proposal_state_service_pg import has_generated, mark_generated
from app.services.proposal_state import has_generated, mark_generated
from app.core.config import settings
from app.api.schemas import PipedriveWebhookPayload

router = APIRouter()

logger = logging.getLogger("app.api.webhook")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
logger.setLevel(logging.INFO)

MQL_PIPELINE_ID = 4
ENTRADA_LEADS_STAGE_ID = 21


def _normalize_cliente(title) -> str:
    if title is None:
        return "Cliente sem nome"
    title_str = str(title).strip()
    return title_str if title_str else "Cliente sem nome"


def _normalize_valor(value) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int_or_none(value):
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _log_context(
    *,
    deal_id=None,
    pipeline_id=None,
    stage_id=None,
    prev_stage_id=None,
    correlation_id=None,
) -> str:
    return (
        f"[deal_id={deal_id} pipeline_id={pipeline_id} "
        f"stage_id={stage_id} prev_stage_id={prev_stage_id} "
        f"corr={correlation_id}]"
    )


@router.post("/webhook")
async def receber_webhook(payload: PipedriveWebhookPayload):
    """
    Webhook do Pipedrive (v2.0), tipado com Pydantic.

    Regras:
    - Só gera quando pipeline_id == MQL_PIPELINE_ID e stage_id == ENTRADA_LEADS_STAGE_ID
    - Idempotência: por (deal_id, stage_id) em SQLite
    - Em caso de erro operacional (Pipedrive off, resposta inválida etc.): retorna 200 com "Erro controlado"
      para evitar retries em cascata.
    """
    start_time = time.monotonic()

    try:
        # ---------------------------------------------------------------------
        # DEV (mock)
        # ---------------------------------------------------------------------
        if settings.APP_ENV == "DEV":
            deal_id = 999
            stage_id = ENTRADA_LEADS_STAGE_ID
            pipeline_id = MQL_PIPELINE_ID
            prev_stage_id = None
            correlation_id = None

            ctx = _log_context(
                deal_id=deal_id,
                pipeline_id=pipeline_id,
                stage_id=stage_id,
                prev_stage_id=prev_stage_id,
                correlation_id=correlation_id,
            )
            logger.info(f"{ctx} Rodando em modo DEV (mock)")

            dados = {
                "id": deal_id,
                "cliente": _normalize_cliente("Cliente Teste"),
                "valor": _normalize_valor("5000"),
            }

            if has_generated(deal_id, stage_id):
                elapsed_ms = (time.monotonic() - start_time) * 1000
                logger.info(f"{ctx} [DEV] Ignorado: já gerado. elapsed={elapsed_ms:.1f}ms")
                return {"status": "Ignorado: proposta já gerada", "modo": settings.APP_ENV, "gerado": False}

            logger.info(f"{ctx} [DEV] Gerando PDF...")
            pdf_path = gerar_pdf(dados)
            mark_generated(deal_id, stage_id)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(f"{ctx} [DEV] PDF gerado. arquivo={pdf_path} elapsed={elapsed_ms:.1f}ms")
            return {"status": "PDF gerado com sucesso", "arquivo": pdf_path, "modo": settings.APP_ENV, "gerado": True}

        # ---------------------------------------------------------------------
        # PROD
        # ---------------------------------------------------------------------
        if payload.data is None:
            # ✅ Não devolver 400 para webhook em produção: devolve 200 e loga
            logger.warning("[WEBHOOK] Ignorado: payload sem 'data'.")
            return {"status": "Ignorado: payload sem data", "modo": settings.APP_ENV, "gerado": False}

        data = payload.data
        previous = payload.previous
        meta = payload.meta

        correlation_id = meta.correlation_id if meta is not None else None
        pipeline_id = data.pipeline_id
        stage_id = data.stage_id
        prev_stage_id = previous.stage_id if previous is not None else None

        # ✅ deal_id pode vir como int em data.id ou string em meta.entity_id
        deal_id = data.id
        if deal_id is None and meta is not None:
            deal_id = _to_int_or_none(meta.entity_id)
        else:
            deal_id = _to_int_or_none(deal_id)

        ctx = _log_context(
            deal_id=deal_id,
            pipeline_id=pipeline_id,
            stage_id=stage_id,
            prev_stage_id=prev_stage_id,
            correlation_id=correlation_id,
        )

        logger.info(f"{ctx} Payload recebido (Pydantic):")
        logger.info(payload.model_dump_json(indent=2, by_alias=True))

        # 0) Validar pipeline/stage presentes
        if pipeline_id is None or stage_id is None:
            logger.warning(f"{ctx} Ignorado: pipeline_id/stage_id ausentes.")
            return {"status": "Ignorado: pipeline/stage ausentes", "modo": settings.APP_ENV, "gerado": False}

        # 1) FILTRO alvo
        if pipeline_id != MQL_PIPELINE_ID or stage_id != ENTRADA_LEADS_STAGE_ID:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                f"{ctx} Ignorado: fora do alvo "
                f"(esperado pipeline_id={MQL_PIPELINE_ID}, stage_id={ENTRADA_LEADS_STAGE_ID}). "
                f"elapsed={elapsed_ms:.1f}ms"
            )
            return {"status": "Ignorado: fora do alvo", "modo": settings.APP_ENV, "gerado": False}

        # 2) Garantir deal_id
        if deal_id is None:
            logger.error(f"{ctx} Erro: deal_id ausente (data.id/meta.entity_id).")
            return {"status": "Erro controlado: deal_id ausente", "modo": settings.APP_ENV, "gerado": False}

        # 3) Idempotência forte
        if has_generated(deal_id, stage_id):
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(f"{ctx} Ignorado: já gerado para este deal/stage. elapsed={elapsed_ms:.1f}ms")
            return {"status": "Ignorado: proposta já gerada", "modo": settings.APP_ENV, "gerado": False}

        # 4) Bloqueio leve (reduz ruído)
        if prev_stage_id is not None and prev_stage_id == ENTRADA_LEADS_STAGE_ID:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(f"{ctx} Ignorado: edição no mesmo stage. elapsed={elapsed_ms:.1f}ms")
            return {"status": "Ignorado: edição no mesmo stage", "modo": settings.APP_ENV, "gerado": False}

        # 5) Otimização: usar dados do webhook se já vierem completos
        # (se title/value vierem nulos, cai no buscar_deal)
        title_from_webhook = getattr(data, "title", None)
        value_from_webhook = getattr(data, "value", None)

        if title_from_webhook is not None or value_from_webhook is not None:
            logger.info(f"{ctx} Usando title/value do webhook (quando disponíveis).")

        # Se não vierem dados suficientes, consulta o Pipedrive
        deal = None
        if not title_from_webhook and value_from_webhook is None:
            logger.info(f"{ctx} Buscando dados no Pipedrive...")
            try:
                deal = buscar_deal(deal_id)
            except PipedriveDealNotFoundError as e:
                logger.warning(f"{ctx} Deal não encontrado: {e}")
                return {"status": "Erro controlado: deal não encontrado", "modo": settings.APP_ENV, "gerado": False}
            except PipedriveUnavailableError as e:
                logger.error(f"{ctx} Pipedrive indisponível: {e}")
                return {"status": "Erro controlado: pipedrive indisponível", "modo": settings.APP_ENV, "gerado": False}
            except PipedriveInvalidResponseError as e:
                logger.error(f"{ctx} Resposta inválida do Pipedrive: {e}")
                return {"status": "Erro controlado: resposta inválida", "modo": settings.APP_ENV, "gerado": False}
            except PipedriveError as e:
                logger.error(f"{ctx} Erro genérico do Pipedrive: {e}")
                return {"status": "Erro controlado: falha no pipedrive", "modo": settings.APP_ENV, "gerado": False}

        # 6) Montar dados com fallback + normalização
        raw_id = deal.get("id") if deal else deal_id
        raw_title = (deal.get("title") if deal else title_from_webhook)
        raw_value = (deal.get("value") if deal else value_from_webhook)

        dados = {
            "id": raw_id,
            "cliente": _normalize_cliente(raw_title),
            "valor": _normalize_valor(raw_value),
        }

        logger.info(f"{ctx} Gerando PDF... dados(id={dados['id']}, cliente={dados['cliente']}, valor={dados['valor']})")
        pdf_path = gerar_pdf(dados)

        # 7) Marcar como gerado (só após sucesso)
        mark_generated(deal_id, stage_id)

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(f"{ctx} PDF gerado e marcado em SQLite. arquivo={pdf_path} elapsed={elapsed_ms:.1f}ms")
        return {"status": "PDF gerado com sucesso", "arquivo": pdf_path, "modo": settings.APP_ENV, "gerado": True}

    except Exception as e:
        # ✅ Webhook em produção: evitar 500 pro Pipedrive (reduz retry e duplicidade)
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.exception(f"Erro inesperado no webhook. elapsed={elapsed_ms:.1f}ms")
        return {"status": "Erro controlado", "modo": settings.APP_ENV, "gerado": False}