import time
import random
import logging
from typing import Any, Dict, Optional

import requests
from requests import Response
from app.core.config import settings

log = logging.getLogger("pipedrive")

DEFAULT_TIMEOUT = 10  # segundos
RETRIES = 3
BACKOFF_SECONDS = 1.5
JITTER_MAX_SECONDS = 0.35  # adiciona aleatoriedade pequena no backoff


class PipedriveError(Exception):
    """Erro base para falhas na integração Pipedrive."""


class PipedriveAuthError(PipedriveError):
    """Falha de autenticação (token inválido/ausente)."""


class PipedriveNotFoundError(PipedriveError):
    """Recurso não encontrado (deal inexistente)."""


class PipedriveRateLimitError(PipedriveError):
    """Rate limit atingido (429)."""


def _validate_settings() -> None:
    if not settings.BASE_URL:
        raise PipedriveError("BASE_URL não configurada no .env (ex.: https://api.pipedrive.com/v1).")
    if not settings.PIPEDRIVE_TOKEN:
        raise PipedriveAuthError("PIPEDRIVE_TOKEN não configurado no .env.")


def _build_url(deal_id: int) -> str:
    return f"{settings.BASE_URL.rstrip('/')}/deals/{deal_id}"


def _parse_deal(resp: Response) -> Dict[str, Any]:
    try:
        payload = resp.json()
    except Exception as e:
        raise PipedriveError(f"Resposta do Pipedrive não é JSON válido: {e}") from e

    data = payload.get("data")
    if not data:
        # Pipedrive pode retornar success=false ou data=null
        raise PipedriveError("Deal não encontrado ou resposta sem campo 'data'.")
    return data


def buscar_deal(deal_id: int, *, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Busca um Deal no Pipedrive via API v1.
    - Retry com backoff em erros transitórios (5xx, timeouts, conexão) e 429.
    - Erros 401/403 e 404 não fazem retry (falhas definitivas).
    """
    _validate_settings()

    url = _build_url(deal_id)
    params = {"api_token": settings.PIPEDRIVE_TOKEN}

    last_err: Optional[Exception] = None

    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)

            # Tratar status codes com mais precisão
            if resp.status_code in (401, 403):
                raise PipedriveAuthError(f"Auth falhou (HTTP {resp.status_code}). Verifique o token.")
            if resp.status_code == 404:
                raise PipedriveNotFoundError(f"Deal {deal_id} não encontrado (HTTP 404).")
            if resp.status_code == 429:
                # Rate limit: respeitar Retry-After se existir
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else (BACKOFF_SECONDS * attempt)
                wait += random.random() * JITTER_MAX_SECONDS
                log.warning(f"Rate limit (429) ao buscar deal_id={deal_id}. Aguardando {wait:.2f}s e tentando novamente...")
                time.sleep(wait)
                continue

            # Para demais códigos, levanta erro se não for 2xx
            resp.raise_for_status()

            deal = _parse_deal(resp)
            log.info(f"Deal carregado com sucesso: deal_id={deal_id}")
            return deal

        except (PipedriveAuthError, PipedriveNotFoundError) as e:
            # Falhas definitivas: não adianta retry
            log.error(f"Falha definitiva ao buscar deal_id={deal_id}: {e}")
            raise

        except requests.Timeout as e:
            last_err = e
            log.warning(f"Timeout ao buscar deal_id={deal_id} (tentativa {attempt}/{RETRIES}).")

        except requests.RequestException as e:
            # Conexão, DNS, 5xx após raise_for_status, etc.
            last_err = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            log.warning(
                f"Erro de rede/HTTP ao buscar deal_id={deal_id} "
                f"(tentativa {attempt}/{RETRIES}) status={status}: {e}"
            )

        except PipedriveError as e:
            # Erro de parsing/JSON/data ausente — pode ser transitório dependendo do caso
            last_err = e
            log.warning(f"Erro ao interpretar resposta do Pipedrive deal_id={deal_id} (tentativa {attempt}/{RETRIES}): {e}")

        # Retry/backoff para erros transitórios
        if attempt < RETRIES:
            sleep_s = (BACKOFF_SECONDS * attempt) + (random.random() * JITTER_MAX_SECONDS)
            time.sleep(sleep_s)
        else:
            msg = f"Falha ao buscar deal no Pipedrive após {RETRIES} tentativas (deal_id={deal_id}): {last_err}"
            log.error(msg)
            raise PipedriveError(msg) from last_err