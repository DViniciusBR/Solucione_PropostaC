import time
import logging
from fastapi import APIRouter, HTTPException
from app.services.pdf_service import gerar_pdf
from app.services.pipedrive_service import (
    buscar_deal,
    PipedriveError,
    PipedriveUnavailableError,
    PipedriveDealNotFoundError,
    PipedriveInvalidResponseError,
)
from app.services.proposal_state_service import has_generated, mark_generated
from app.core.config import settings
from app.api.schemas import PipedriveWebhookPayload

router = APIRouter()

# Logger dedicado para este módulo
logger = logging.getLogger("app.api.webhook")
if not logger.handlers:
    # Se a app principal já configurar logging, isso não vai duplicar handlers
    logging.basicConfig(level=logging.INFO)
logger.setLevel(logging.INFO)

MQL_PIPELINE_ID = 4
ENTRADA_LEADS_STAGE_ID = 21


def _normalize_cliente(title) -> str:
    """
    Garante que o nome do cliente seja uma string legível.
    - Se vier None, vazio ou só espaços -> 'Cliente sem nome'
    """
    if title is None:
        return "Cliente sem nome"

    title_str = str(title).strip()
    if not title_str:
        return "Cliente sem nome"

    return title_str


def _normalize_valor(value) -> float:
    """
    Converte o valor recebido para float.
    - None, vazio, inválido -> 0.0
    - Aceita string ou número.
    """
    if value is None:
        return 0.0

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _log_context(
    *,
    deal_id=None,
    pipeline_id=None,
    stage_id=None,
    prev_stage_id=None,
    correlation_id=None,
) -> str:
    """
    Monta um prefixo de contexto padrão para log.
    """
    return (
        f"[deal_id={deal_id} pipeline_id={pipeline_id} "
        f"stage_id={stage_id} prev_stage_id={prev_stage_id} "
        f"corr={correlation_id}]"
    )


@router.post("/webhook")
async def receber_webhook(payload: PipedriveWebhookPayload):
    """
    Webhook do Pipedrive (v2.0), agora tipado com Pydantic.

    - DEV: fluxo mockado, usando deal_id fixo e dados de teste.
    - PROD: usa 'data', 'previous' e 'meta' do payload real.
    - Idempotência: por (deal_id, stage_id).
    - Gera PDF via gerar_pdf(dados: dict).
    """
    start_time = time.monotonic()

    try:
        # ---------------------------------------------------------------------
        # MODO DEV (mock)
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

            # Idempotência (por deal_id + stage_id)
            if has_generated(deal_id, stage_id):
                elapsed_ms = (time.monotonic() - start_time) * 1000
                logger.info(
                    f"{ctx} [DEV] Ignorado: proposta já gerada. "
                    f"elapsed={elapsed_ms:.1f}ms"
                )
                return {
                    "status": "Ignorado: proposta já gerada",
                    "modo": settings.APP_ENV,
                    "gerado": False,
                }

            logger.info(f"{ctx} [DEV] Gerando PDF de teste...")
            pdf_path = gerar_pdf(dados)
            mark_generated(deal_id, stage_id)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                f"{ctx} [DEV] PDF gerado e registrado em SQLite "
                f"(arquivo={pdf_path}) elapsed={elapsed_ms:.1f}ms"
            )
            return {
                "status": "PDF gerado com sucesso",
                "arquivo": pdf_path,
                "modo": settings.APP_ENV,
                "gerado": True,
            }

        # ---------------------------------------------------------------------
        # MODO PROD (Pipedrive real)
        # ---------------------------------------------------------------------
        logger.info("[WEBHOOK] Rodando em modo PROD (Pipedrive real)")

        # Garantir que temos 'data'
        if payload.data is None:
            logger.warning(
                "[WEBHOOK] Payload inválido: campo 'data' ausente."
            )
            raise HTTPException(
                status_code=400,
                detail="Payload inválido: campo 'data' ausente.",
            )

        data = payload.data
        previous = payload.previous
        meta = payload.meta

        correlation_id = meta.correlation_id if meta is not None else None
        pipeline_id = data.pipeline_id
        stage_id = data.stage_id
        prev_stage_id = previous.stage_id if previous is not None else None

        # ID pode vir em data.id ou meta.entity_id
        deal_id = data.id
        if deal_id is None and meta is not None:
            deal_id = meta.entity_id

        ctx = _log_context(
            deal_id=deal_id,
            pipeline_id=pipeline_id,
            stage_id=stage_id,
            prev_stage_id=prev_stage_id,
            correlation_id=correlation_id,
        )

        # Log bruto do payload (já parseado)
        logger.info(f"{ctx} Payload recebido (parseado pelo Pydantic):")
        logger.info(payload.model_dump_json(indent=2, by_alias=True))

        logger.info(
            f"{ctx} pipeline_id={pipeline_id}, stage_id={stage_id}, "
            f"prev_stage_id={prev_stage_id}"
        )

        # 1) FILTRO de pipeline/stage alvo
        if pipeline_id != MQL_PIPELINE_ID or stage_id != ENTRADA_LEADS_STAGE_ID:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                f"{ctx} ⏭ Ignorado: não está no funil/etapa alvo "
                f"(esperado pipeline_id={MQL_PIPELINE_ID}, "
                f"stage_id={ENTRADA_LEADS_STAGE_ID}). "
                f"elapsed={elapsed_ms:.1f}ms"
            )
            return {
                "status": "Ignorado: não está no funil/etapa alvo",
                "modo": settings.APP_ENV,
                "gerado": False,
            }

        # 2) Garantir deal_id
        if deal_id is None:
            logger.error(
                f"{ctx} Erro: não encontrei o ID do deal (data.id / meta.entity_id)."
            )
            raise KeyError(
                "Não encontrei o ID do deal (data.id / meta.entity_id)."
            )

        # 3) BLOQUEIO FORTE (por deal_id + stage_id)
        if has_generated(deal_id, stage_id):
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                f"{ctx} ⏭ Ignorado: proposta já gerada para este deal/stage. "
                f"elapsed={elapsed_ms:.1f}ms"
            )
            return {
                "status": "Ignorado: proposta já gerada",
                "modo": settings.APP_ENV,
                "gerado": False,
            }

        # 4) BLOQUEIO LEVE: alteração dentro da mesma etapa
        if prev_stage_id is not None and prev_stage_id == ENTRADA_LEADS_STAGE_ID:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                f"{ctx} ⏭ Ignorado: atualização dentro da mesma etapa "
                f"(não gerando PDF novamente). elapsed={elapsed_ms:.1f}ms"
            )
            return {
                "status": "Ignorado: alteração dentro da mesma etapa",
                "modo": settings.APP_ENV,
                "gerado": False,
            }

        logger.info(f"{ctx} ✅ Deal alvo confirmado. Buscando dados no Pipedrive...")

        # 5) Buscar dados completos do deal no Pipedrive, com tratamento fino de erro
        try:
            deal = buscar_deal(deal_id)
        except PipedriveDealNotFoundError as e:
            logger.warning(f"{ctx} Deal não encontrado no Pipedrive: {e}")
            raise HTTPException(status_code=404, detail=str(e))
        except PipedriveUnavailableError as e:
            logger.error(f"{ctx} Pipedrive indisponível: {e}")
            raise HTTPException(
                status_code=502,
                detail="Pipedrive está indisponível no momento. Tente novamente mais tarde.",
            )
        except PipedriveInvalidResponseError as e:
            logger.error(f"{ctx} Resposta inválida do Pipedrive: {e}")
            raise HTTPException(
                status_code=502,
                detail="Recebemos uma resposta inválida do Pipedrive.",
            )
        except PipedriveError as e:
            logger.error(f"{ctx} Erro genérico do Pipedrive: {e}")
            raise HTTPException(
                status_code=502,
                detail="Erro ao consultar a API do Pipedrive.",
            )

        # 6) Montar dict de dados para o PDF (com fallback + normalização)
        raw_id = deal.get("id", deal_id)
        raw_title = deal.get("title")
        raw_value = deal.get("value")

        dados = {
            "id": raw_id,
            "cliente": _normalize_cliente(raw_title),
            "valor": _normalize_valor(raw_value),
        }

        logger.info(
            f"{ctx} ✅ Gerando PDF (PROD)... "
            f"dados={{'id': {dados['id']}, 'cliente': '{dados['cliente']}', 'valor': {dados['valor']}}}"
        )
        pdf_path = gerar_pdf(dados)

        # 7) Marcar como gerado (só depois de gerar sem erro)
        mark_generated(deal_id, stage_id)

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            f"{ctx} ✅ PDF gerado e registrado em SQLite "
            f"(arquivo={pdf_path}) elapsed={elapsed_ms:.1f}ms"
        )
        return {
            "status": "PDF gerado com sucesso",
            "arquivo": pdf_path,
            "modo": settings.APP_ENV,
            "gerado": True,
        }

    except HTTPException:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.exception(
            f"Erro HTTP controlado no webhook. elapsed={elapsed_ms:.1f}ms"
        )
        raise
    except Exception as e:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.exception(
            f"Erro inesperado no webhook. elapsed={elapsed_ms:.1f}ms"
        )
        raise HTTPException(status_code=500, detail=str(e))