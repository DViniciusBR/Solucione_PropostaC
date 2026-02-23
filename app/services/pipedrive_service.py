import logging
from typing import Any, Dict

import requests
from requests.exceptions import RequestException, Timeout

from app.core.config import settings

logger = logging.getLogger(__name__)

# Reaproveitando exatamente o que você já usa hoje
BASE_URL = settings.BASE_URL
PIPEDRIVE_TOKEN = settings.PIPEDRIVE_TOKEN

# Timeout padrão (se quiser, pode ajustar)
REQUEST_TIMEOUT_SECONDS = 10


# =========================
# Exceções específicas
# =========================

class PipedriveError(Exception):
    """Erro genérico ao falar com a API do Pipedrive."""


class PipedriveUnavailableError(PipedriveError):
    """API indisponível (timeout, erro de rede ou HTTP 5xx)."""


class PipedriveDealNotFoundError(PipedriveError):
    """Deal não encontrado (HTTP 404 ou data vazia)."""


class PipedriveInvalidResponseError(PipedriveError):
    """Resposta inesperada ou malformada da API (JSON inválido, sem campo data etc.)."""


# =========================
# Funções de serviço
# =========================

def _get_auth_params() -> Dict[str, Any]:
    """
    Monta os parâmetros de autenticação da API.
    """
    if not PIPEDRIVE_TOKEN:
        logger.error("PIPEDRIVE_TOKEN não está configurado nas variáveis de ambiente.")
        raise PipedriveError("Token da API do Pipedrive não configurado.")
    return {"api_token": PIPEDRIVE_TOKEN}


def buscar_deal(deal_id: int) -> Dict[str, Any]:
    """
    Busca um negócio (deal) no Pipedrive.

    Retorna:
        dict com os dados do deal (campo 'data' da resposta do Pipedrive)

    Erros possíveis:
        - PipedriveDealNotFoundError: deal não existe (404 ou data vazia)
        - PipedriveUnavailableError: timeout, erro de rede ou HTTP 5xx
        - PipedriveInvalidResponseError: JSON malformado ou sem 'data'
        - PipedriveError: outros erros gerais (configuração, HTTP 4xx genérico)
    """

    url = f"{BASE_URL}/deals/{deal_id}"
    params = _get_auth_params()

    logger.info(f"[PIPEDRIVE] Buscando deal_id={deal_id} em {url}")

    # 1) Chamada HTTP com timeout e tratamento de erro de rede
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except Timeout as e:
        logger.exception(f"[PIPEDRIVE] Timeout ao consultar deal_id={deal_id}")
        raise PipedriveUnavailableError("Timeout ao consultar a API do Pipedrive.") from e
    except RequestException as e:
        logger.exception(f"[PIPEDRIVE] Erro de rede ao consultar deal_id={deal_id}")
        raise PipedriveUnavailableError("Erro de rede ao consultar a API do Pipedrive.") from e

    status = response.status_code

    # 2) HTTP 404 → negócio não encontrado
    if status == 404:
        logger.warning(f"[PIPEDRIVE] Deal {deal_id} não encontrado (HTTP 404).")
        raise PipedriveDealNotFoundError(f"Negócio {deal_id} não foi encontrado no Pipedrive.")

    # 3) HTTP 5xx → indisponibilidade temporária
    if 500 <= status <= 599:
        logger.error(
            f"[PIPEDRIVE] Erro 5xx ao consultar deal_id={deal_id}: "
            f"status={status}, body={response.text}"
        )
        raise PipedriveUnavailableError(
            f"Pipedrive retornou erro {status} ao buscar o negócio."
        )

    # 4) Outros códigos não-ok (4xx ≠ 404, 3xx estranhos, etc.)
    if not response.ok:
        logger.error(
            f"[PIPEDRIVE] Erro HTTP ao consultar deal_id={deal_id}: "
            f"status={status}, body={response.text}"
        )
        raise PipedriveError(
            f"Erro na API do Pipedrive (HTTP {status}) ao buscar o negócio."
        )

    # 5) Validar JSON
    try:
        body = response.json()
    except ValueError as e:
        logger.exception(
            f"[PIPEDRIVE] Resposta inválida (JSON) ao buscar deal_id={deal_id}: {response.text}"
        )
        raise PipedriveInvalidResponseError(
            "Resposta inválida do Pipedrive (JSON malformado)."
        ) from e

    # 6) Verificar campo 'data' (equivalente ao seu if not data.get("data"))
    if not body.get("data"):
        logger.warning(
            f"[PIPEDRIVE] Resposta sem dados para deal_id={deal_id}: {body}"
        )
        raise PipedriveDealNotFoundError("Deal não encontrado.")

    deal = body["data"]

    logger.info(f"[PIPEDRIVE] Deal {deal_id} obtido com sucesso.")
    return deal