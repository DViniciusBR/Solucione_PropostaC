import os
from urllib.parse import urlparse
from dotenv import load_dotenv
import psycopg2

load_dotenv()

database_url = os.getenv("DATABASE_URL")

if not database_url:
    raise RuntimeError("DATABASE_URL não definida no ambiente.")

parsed = urlparse(database_url)
print("Host detectado:", parsed.hostname)
print("Banco detectado:", parsed.path.lstrip("/"))

try:
    conn = psycopg2.connect(database_url)
    cur = conn.cursor()
    cur.execute("SELECT 1;")
    print("Resultado do SELECT 1:", cur.fetchone())  # esperado: (1,)
    cur.close()
    conn.close()
    print("Conexão com PostgreSQL OK")
except Exception as e:
    print("Erro ao conectar no PostgreSQL:")
    print(type(e).__name__, "-", e)
    raise