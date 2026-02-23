from pydantic import BaseModel, Field
from typing import Optional


class PipedriveDealData(BaseModel):
    """
    Representa o bloco 'data' do webhook do Pipedrive.
    Deixamos tudo Optional porque já tratamos ausência na rota.
    """
    id: Optional[int] = None
    title: Optional[str] = None
    value: Optional[float] = None
    pipeline_id: Optional[int] = Field(default=None, alias="pipeline_id")
    stage_id: Optional[int] = Field(default=None, alias="stage_id")


class PipedriveDealPrevious(BaseModel):
    """
    Representa o bloco 'previous' do webhook.
    Normalmente precisamos só do stage_id anterior.
    """
    stage_id: Optional[int] = Field(default=None, alias="stage_id")


class PipedriveMeta(BaseModel):
    """
    Bloco 'meta' do webhook – usamos pelo menos entity_id e correlation_id.
    """
    entity_id: Optional[int] = Field(default=None, alias="entity_id")
    correlation_id: Optional[str] = Field(default=None, alias="correlation_id")


class PipedriveWebhookPayload(BaseModel):
    """
    Payload completo esperado do webhook v2.0 do Pipedrive.
    """
    event: Optional[str] = None
    retry: Optional[int] = 0
    meta: Optional[PipedriveMeta] = None
    data: Optional[PipedriveDealData] = None
    previous: Optional[PipedriveDealPrevious] = None