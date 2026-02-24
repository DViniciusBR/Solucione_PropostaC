from pydantic import BaseModel, Field
from typing import Optional, Any, Dict


class PipedriveDealData(BaseModel):
    """
    Representa o bloco 'data' do webhook v2.0 do Pipedrive.
    Campos Optional para não quebrar em variações de payload.
    """
    id: Optional[int] = None
    title: Optional[str] = None
    value: Optional[float] = None
    pipeline_id: Optional[int] = None
    stage_id: Optional[int] = None

    # permite campos adicionais dentro de data
    model_config = {"extra": "allow"}


class PipedriveDealPrevious(BaseModel):
    """
    Representa o bloco 'previous' do webhook.
    """
    stage_id: Optional[int] = None

    model_config = {"extra": "allow"}


class PipedriveMeta(BaseModel):
    """
    Bloco 'meta' do webhook – útil para rastreabilidade (correlation_id) e fallback de deal_id (entity_id).
    Observação: entity_id costuma vir como string no webhook v2.0.
    """
    entity_id: Optional[str] = None
    correlation_id: Optional[str] = None

    # opcionais úteis para debug
    entity: Optional[str] = None
    version: Optional[str] = None
    webhook_id: Optional[str] = None

    model_config = {"extra": "allow"}


class PipedriveWebhookPayload(BaseModel):
    """
    Payload completo esperado do webhook v2.0 do Pipedrive.
    Mantém campos extras para compatibilidade futura.
    """
    event: Optional[str] = None
    retry: Optional[int] = 0

    meta: Optional[PipedriveMeta] = None
    data: Optional[PipedriveDealData] = None
    previous: Optional[PipedriveDealPrevious] = None

    # qualquer campo adicional no root não quebra
    model_config = {"extra": "allow"}