import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PIPEDRIVE_TOKEN = os.getenv("PIPEDRIVE_TOKEN")
    BASE_URL = os.getenv("BASE_URL")
    APP_ENV = os.getenv("APP_ENV", "DEV")

settings = Settings()