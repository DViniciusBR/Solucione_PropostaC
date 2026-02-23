from fastapi import FastAPI
from app.api.routes import router

app = FastAPI()

app.include_router(router)

@app.get("/")
def root():
    return {"message": "API rodando"}