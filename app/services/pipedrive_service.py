import logging
import random
import time
from typing import Any, Dict, Optional

import requests
from requests import Response

from app.core.config import settings

log = logging.getLogger("pipedrive")

DEFAULT_TIMEOUT = 10  # segundos
RETRIES = 3
BACKOFF_SECONDS = 1.5
JITTER_MAX_SECONDS = 0.35


# ============================================================================
# Exceções de domínio da integração Pipedrive
# ============================================================================

class PipedriveError(Exception):
    """Erro base para falhas na integração com o Pipedrive."""


class PipedriveAuthError(PipedriveError):
    """Falha de autenticação (token inválido/ausente)."""


class PipedriveNotFoundError(PipedriveError):
    """Recurso não encontrado (ex.: deal inexistente)."""


class PipedriveRateLimitError(PipedriveError):
    """Rate limit atingido (HTTP 429)."""


class PipedriveUnavailableError(PipedriveError):
    """Indisponibilidade temporária da API (5xx, timeout, conexão)."""


class PipedriveInvalidResponseError(PipedriveError):
    """Resposta inválida da API (JSON inválido, payload sem 'data', etc.)."""


# ============================================================================
# Aliases de compatibilidade (temporários)
# Remova no futuro quando routes.py e demais módulos estiverem padronizados.
# ============================================================================

PipedriveDealNotFoundError = PipedriveNotFoundError
PipedriveAPIUnavailableError = PipedriveUnavailableError
PipedriveServiceUnavailableError = PipedriveUnavailableError


# ============================================================================
# Helpers internos
# ============================================================================

def _validate_settings() -> None:
    if not settings.BASE_URL:
        raise PipedriveError(
            "BASE_URL não configurada no .env (ex.: https://api.pipedrive.com/v1)."
        )
    if not settings.PIPEDRIVE_TOKEN:
        raise PipedriveAuthError("PIPEDRIVE_TOKEN não configurado no .env.")


def _build_deal_url(deal_id: int) -> str:
    return f"{settings.BASE_URL.rstrip('/')}/deals/{deal_id}"


def _retry_sleep_seconds(attempt: int) -> float:
    return (BACKOFF_SECONDS * attempt) + (random.random() * JITTER_MAX_SECONDS)


def _parse_deal_response(resp: Response) -> Dict[str, Any]:
    try:
        payload = resp.json()
    except Exception as exc:
        raise PipedriveInvalidResponseError(
            f"Resposta do Pipedrive não é JSON válido: {exc}"
        ) from exc

    data = payload.get("data")
    if not data:
        raise PipedriveInvalidResponseError(
            "Resposta sem campo 'data' (ou deal não encontrado)."
        )
    return data


def _handle_status_code(resp: Response, deal_id: int, attempt: int) -> Optional[float]:
    """
    Processa status HTTP conhecidos.
    Retorna:
      - float (segundos) para aguardar e retry no caso de 429
      - None para seguir o fluxo normal
    Lança exceções definitivas/transitórias quando aplicável.
    """
    status_code = resp.status_code

    if status_code in (401, 403):
        raise PipedriveAuthError(
            f"Falha de autenticação no Pipedrive (HTTP {status_code})."
        )

    if status_code == 404:
        raise PipedriveNotFoundError(f"Deal {deal_id} não encontrado (HTTP 404).")

    if status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        wait = float(retry_after) if retry_after and retry_after.isdigit() else _retry_sleep_seconds(attempt)
        log.warning(
            "Rate limit (429) ao buscar deal_id=%s. Aguardando %.2fs para retry.",
            deal_id,
            wait,
        )
        return wait

    if 500 <= status_code <= 599:
        raise PipedriveUnavailableError(
            f"Pipedrive indisponível (HTTP {status_code}) ao buscar deal_id={deal_id}."
        )

    # Demais status serão tratados por raise_for_status() no fluxo principal
    return None


# ============================================================================
# API pública
# ============================================================================

def buscar_deal(deal_id: int, *, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Busca um deal no Pipedrive (API v1) com retry/backoff para falhas transitórias.

    Regras:
    - Retry em: 429, timeout, erros de conexão, 5xx.
    - Sem retry em: 401/403, 404.
    """
    _validate_settings()

    url = _build_deal_url(deal_id)
    params = {"api_token": settings.PIPEDRIVE_TOKEN}

    last_error: Optional[Exception] = None
    last_status: Optional[int] = None

    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            last_status = resp.status_code

            retry_wait = _handle_status_code(resp, deal_id, attempt)
            if retry_wait is not None:
                last_error = PipedriveRateLimitError(
                    f"Rate limit ao buscar deal_id={deal_id}"
                )
                time.sleep(retry_wait)
                continue

            resp.raise_for_status()

            deal = _parse_deal_response(resp)
            log.info("Deal carregado com sucesso: deal_id=%s", deal_id)
            return deal

        except (PipedriveAuthError, PipedriveNotFoundError):
            # Falhas definitivas
            log.exception("Falha definitiva ao buscar deal_id=%s", deal_id)
            raise

        except PipedriveRateLimitError as exc:
            last_error = exc
            log.warning(
                "Rate limit persistente ao buscar deal_id=%s (tentativa %s/%s).",
                deal_id,
                attempt,
                RETRIES,
            )

        except requests.Timeout as exc:
            last_error = exc
            log.warning(
                "Timeout ao buscar deal_id=%s (tentativa %s/%s).",
                deal_id,
                attempt,
                RETRIES,
            )

        except requests.RequestException as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None:
                last_status = status
            log.warning(
                "Erro de rede/HTTP ao buscar deal_id=%s (tentativa %s/%s) status=%s: %s",
                deal_id,
                attempt,
                RETRIES,
                status,
                exc,
            )

        except (PipedriveUnavailableError, PipedriveInvalidResponseError) as exc:
            last_error = exc
            log.warning(
                "Falha transitória/parse ao buscar deal_id=%s (tentativa %s/%s): %s",
                deal_id,
                attempt,
                RETRIES,
                exc,
            )

        except PipedriveError as exc:
            last_error = exc
            log.warning(
                "Erro Pipedrive ao buscar deal_id=%s (tentativa %s/%s): %s",
                deal_id,
                attempt,
                RETRIES,
                exc,
            )

        # Backoff para retry (se ainda houver tentativa)
        if attempt < RETRIES:
            time.sleep(_retry_sleep_seconds(attempt))

    # Classificação final do erro após esgotar retries
    message = (
        f"Falha ao buscar deal no Pipedrive após {RETRIES} tentativas "
        f"(deal_id={deal_id}): {last_error}"
    )
    log.error(message)

    if last_status == 429 or isinstance(last_error, PipedriveRateLimitError):
        raise PipedriveRateLimitError(message) from last_error

    if isinstance(
        last_error,
        (requests.Timeout, requests.ConnectionError, PipedriveUnavailableError),
    ):
        raise PipedriveUnavailableError(message) from last_error

    if isinstance(last_error, requests.RequestException):
        status = getattr(getattr(last_error, "response", None), "status_code", None)
        if status and 500 <= status <= 599:
            raise PipedriveUnavailableError(message) from last_error

    if isinstance(last_error, PipedriveInvalidResponseError):
        raise PipedriveInvalidResponseError(message) from last_error

    raise PipedriveError(message) from last_error