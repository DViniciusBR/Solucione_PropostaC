from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class WebhookData(BaseModel):
    id: Optional[int] = None
    pipeline_id: Optional[int] = None
    stage_id: Optional[int] = None
    title: Optional[str] = None
    value: Optional[float] = None

class WebhookPrevious(BaseModel):
    stage_id: Optional[int] = None

class WebhookMeta(BaseModel):
    correlation_id: Optional[str] = None
    entity_id: Optional[str] = None
    entity: Optional[str] = None
    webhook_id: Optional[str] = None
    version: Optional[str] = None

class PipedriveWebhook(BaseModel):
    data: Optional[WebhookData] = None
    previous: Optional[WebhookPrevious] = None
    meta: Optional[WebhookMeta] = None

    # Se o Pipedrive enviar campos extras, não quebrar
    model_config = {"extra": "allow"}