from app.core.config import settings

if settings.APP_ENV == "PROD":
    from app.services.proposal_state_service_pg import has_generated, mark_generated
else:
    from app.services.proposal_state_service import has_generated, mark_generated