from fastapi import FastAPI
from app.api.routes import router
from app.core.logging_config import setup_logging

# 🔹 Configurar logging estruturado
setup_logging()

app = FastAPI(
    title="API Proposta Automática",
    description="Geração automática de proposta comercial via webhook do Pipedrive",
    version="1.0.0"
)

# 🔹 Registrar rotas
app.include_router(router)

# 🔹 Endpoint raiz
@app.get("/")
def root():
    return {"message": "API rodando"}

# 🔹 Healthcheck (importante para deploy e monitoramento)
@app.get("/health")
def health():
    return {"status": "ok"}