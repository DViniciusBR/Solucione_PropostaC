import logging
import time
from typing import Any, Optional

from fastapi import APIRouter

from app.api.schemas import PipedriveWebhookPayload
from app.core.config import settings
from app.services.pdf_service import gerar_pdf
from app.services.pipedrive_service import (
    PipedriveError,
    PipedriveInvalidResponseError,
    PipedriveNotFoundError,
    PipedriveRateLimitError,
    PipedriveUnavailableError,
    buscar_deal,
)
from app.services.proposal_state import has_generated, mark_generated

router = APIRouter()

logger = logging.getLogger("app.api.webhook")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
logger.setLevel(logging.INFO)

MQL_PIPELINE_ID = 4
ENTRADA_LEADS_STAGE_ID = 21


# ============================================================================
# Helpers de normalização
# ============================================================================

def _normalize_cliente(title: Any) -> str:
    if title is None:
        return "Cliente sem nome"

    title_str = str(title).strip()
    return title_str if title_str else "Cliente sem nome"


def _normalize_valor(value: Any) -> float:
    if value is None:
        return 0.0

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ============================================================================
# Helpers de resposta / logs
# ============================================================================

def _elapsed_ms(start_time: float) -> float:
    return (time.monotonic() - start_time) * 1000


def _webhook_response(
    status: str,
    *,
    gerado: bool,
    arquivo: Optional[str] = None,
) -> dict:
    response = {
        "status": status,
        "modo": settings.APP_ENV,
        "gerado": gerado,
    }
    if arquivo:
        response["arquivo"] = arquivo
    return response


def _log_context(
    *,
    deal_id: Optional[int] = None,
    pipeline_id: Optional[int] = None,
    stage_id: Optional[int] = None,
    prev_stage_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
) -> str:
    return (
        f"[deal_id={deal_id} pipeline_id={pipeline_id} "
        f"stage_id={stage_id} prev_stage_id={prev_stage_id} corr={correlation_id}]"
    )


def _build_pdf_payload(*, deal_id: int, title: Any, value: Any) -> dict:
    return {
        "id": deal_id,
        "cliente": _normalize_cliente(title),
        "valor": _normalize_valor(value),
    }


# ============================================================================
# Fluxos de domínio
# ============================================================================

def _handle_dev_mock_flow(start_time: float) -> dict:
    """
    Fluxo de desenvolvimento (mock) para facilitar testes locais sem depender do Pipedrive.
    """
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

    logger.info("%s Rodando em modo DEV (mock)", ctx)

    if has_generated(deal_id, stage_id):
        logger.info(
            "%s [DEV] Ignorado: já gerado. elapsed=%.1fms",
            ctx,
            _elapsed_ms(start_time),
        )
        return _webhook_response("Ignorado: proposta já gerada", gerado=False)

    dados = _build_pdf_payload(deal_id=deal_id, title="Cliente Teste", value="5000")

    logger.info("%s [DEV] Gerando PDF...", ctx)
    pdf_path = gerar_pdf(dados)
    mark_generated(deal_id, stage_id)

    logger.info(
        "%s [DEV] PDF gerado. arquivo=%s elapsed=%.1fms",
        ctx,
        pdf_path,
        _elapsed_ms(start_time),
    )
    return _webhook_response("PDF gerado com sucesso", gerado=True, arquivo=pdf_path)


def _extract_webhook_fields(payload: PipedriveWebhookPayload) -> dict:
    data = payload.data
    previous = payload.previous
    meta = payload.meta

    correlation_id = meta.correlation_id if meta else None
    pipeline_id = data.pipeline_id if data else None
    stage_id = data.stage_id if data else None
    prev_stage_id = previous.stage_id if previous else None

    # deal_id pode vir em data.id (int) ou meta.entity_id (string)
    raw_deal_id = data.id if data else None
    if raw_deal_id is None and meta:
        raw_deal_id = meta.entity_id

    deal_id = _to_int_or_none(raw_deal_id)

    return {
        "data": data,
        "previous": previous,
        "meta": meta,
        "deal_id": deal_id,
        "pipeline_id": pipeline_id,
        "stage_id": stage_id,
        "prev_stage_id": prev_stage_id,
        "correlation_id": correlation_id,
    }


def _should_ignore_by_target(pipeline_id: Optional[int], stage_id: Optional[int]) -> bool:
    return pipeline_id != MQL_PIPELINE_ID or stage_id != ENTRADA_LEADS_STAGE_ID


def _fetch_deal_if_needed(*, deal_id: int, title_from_webhook: Any, value_from_webhook: Any, ctx: str) -> Optional[dict]:
    """
    Busca no Pipedrive apenas quando title/value não vierem no webhook.
    """
    has_title = bool(title_from_webhook)
    has_value = value_from_webhook is not None

    if has_title or has_value:
        logger.info("%s Usando title/value do webhook (quando disponíveis).", ctx)
        return None

    logger.info("%s Buscando dados no Pipedrive...", ctx)
    return buscar_deal(deal_id)


# ============================================================================
# Endpoint
# ============================================================================

@router.post("/webhook")
async def receber_webhook(payload: PipedriveWebhookPayload):
    """
    Webhook do Pipedrive (v2.0), tipado com Pydantic.

    Regras:
    - Só gera quando pipeline_id == 4 e stage_id == 21
    - Idempotência por (deal_id, stage_id), via storage configurado (DEV/PROD)
    - Erros operacionais retornam 200 (erro controlado) para evitar retries em cascata
    """
    start_time = time.monotonic()

    try:
        # ------------------------------------------------------------------
        # DEV (mock)
        # ------------------------------------------------------------------
        if settings.APP_ENV == "DEV":
            return _handle_dev_mock_flow(start_time)

        # ------------------------------------------------------------------
        # PROD (payload real)
        # ------------------------------------------------------------------
        if payload.data is None:
            logger.warning("[WEBHOOK] Ignorado: payload sem 'data'.")
            return _webhook_response("Ignorado: payload sem data", gerado=False)

        fields = _extract_webhook_fields(payload)

        deal_id = fields["deal_id"]
        pipeline_id = fields["pipeline_id"]
        stage_id = fields["stage_id"]
        prev_stage_id = fields["prev_stage_id"]
        correlation_id = fields["correlation_id"]
        data = fields["data"]

        ctx = _log_context(
            deal_id=deal_id,
            pipeline_id=pipeline_id,
            stage_id=stage_id,
            prev_stage_id=prev_stage_id,
            correlation_id=correlation_id,
        )

        logger.info("%s Payload recebido (Pydantic).", ctx)
        logger.info(payload.model_dump_json(indent=2, by_alias=True))

        # 0) Campos mínimos
        if pipeline_id is None or stage_id is None:
            logger.warning("%s Ignorado: pipeline_id/stage_id ausentes.", ctx)
            return _webhook_response("Ignorado: pipeline/stage ausentes", gerado=False)

        # 1) Filtro pipeline/stage alvo
        if _should_ignore_by_target(pipeline_id, stage_id):
            logger.info(
                "%s Ignorado: fora do alvo (esperado pipeline_id=%s, stage_id=%s). elapsed=%.1fms",
                ctx,
                MQL_PIPELINE_ID,
                ENTRADA_LEADS_STAGE_ID,
                _elapsed_ms(start_time),
            )
            return _webhook_response("Ignorado: fora do alvo", gerado=False)

        # 2) deal_id obrigatório
        if deal_id is None:
            logger.error("%s Erro controlado: deal_id ausente (data.id/meta.entity_id).", ctx)
            return _webhook_response("Erro controlado: deal_id ausente", gerado=False)

        # 3) Idempotência
        if has_generated(deal_id, stage_id):
            logger.info(
                "%s Ignorado: proposta já gerada para deal/stage. elapsed=%.1fms",
                ctx,
                _elapsed_ms(start_time),
            )
            return _webhook_response("Ignorado: proposta já gerada", gerado=False)

        # 4) Ignorar edição dentro do mesmo stage (reduz ruído)
        if prev_stage_id is not None and prev_stage_id == ENTRADA_LEADS_STAGE_ID:
            logger.info(
                "%s Ignorado: edição no mesmo stage. elapsed=%.1fms",
                ctx,
                _elapsed_ms(start_time),
            )
            return _webhook_response("Ignorado: edição no mesmo stage", gerado=False)

        # 5) Obter dados (webhook ou Pipedrive)
        title_from_webhook = getattr(data, "title", None)
        value_from_webhook = getattr(data, "value", None)

        try:
            deal = _fetch_deal_if_needed(
                deal_id=deal_id,
                title_from_webhook=title_from_webhook,
                value_from_webhook=value_from_webhook,
                ctx=ctx,
            )
        except PipedriveNotFoundError as exc:
            logger.warning("%s Deal não encontrado: %s", ctx, exc)
            return _webhook_response("Erro controlado: deal não encontrado", gerado=False)
        except PipedriveRateLimitError as exc:
            logger.error("%s Rate limit Pipedrive: %s", ctx, exc)
            return _webhook_response("Erro controlado: rate limit pipedrive", gerado=False)
        except PipedriveUnavailableError as exc:
            logger.error("%s Pipedrive indisponível: %s", ctx, exc)
            return _webhook_response("Erro controlado: pipedrive indisponível", gerado=False)
        except PipedriveInvalidResponseError as exc:
            logger.error("%s Resposta inválida do Pipedrive: %s", ctx, exc)
            return _webhook_response("Erro controlado: resposta inválida", gerado=False)
        except PipedriveError as exc:
            logger.error("%s Erro genérico do Pipedrive: %s", ctx, exc)
            return _webhook_response("Erro controlado: falha no pipedrive", gerado=False)

        # 6) Montar dados finais para PDF
        source = deal or {}
        final_deal_id = source.get("id", deal_id)
        final_title = source.get("title", title_from_webhook)
        final_value = source.get("value", value_from_webhook)

        dados = _build_pdf_payload(
            deal_id=final_deal_id,
            title=final_title,
            value=final_value,
        )

        logger.info(
            "%s Gerando PDF... dados(id=%s, cliente=%s, valor=%s)",
            ctx,
            dados["id"],
            dados["cliente"],
            dados["valor"],
        )

        pdf_path = gerar_pdf(dados)

        # 7) Marca idempotência somente após sucesso
        mark_generated(deal_id, stage_id)

        logger.info(
            "%s PDF gerado e marcado. arquivo=%s elapsed=%.1fms",
            ctx,
            pdf_path,
            _elapsed_ms(start_time),
        )
        return _webhook_response("PDF gerado com sucesso", gerado=True, arquivo=pdf_path)

    except Exception:
        logger.exception(
            "Erro inesperado no webhook. elapsed=%.1fms",
            _elapsed_ms(start_time),
        )
        # Erro controlado (evita cascata de retries do webhook provider)
        return _webhook_response("Erro controlado", gerado=False)