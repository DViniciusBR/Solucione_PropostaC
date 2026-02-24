import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def setup_logging():
    Path("logs").mkdir(exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    # Arquivo com rotação
    fh = RotatingFileHandler("logs/app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)

    logger.handlers = []
    logger.addHandler(ch)
    logger.addHandler(fh)