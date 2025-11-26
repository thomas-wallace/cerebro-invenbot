import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

def get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables")
    return create_client(url, key)

def get_postgres_connection_string() -> str:
    user = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASSWORD")
    host = os.environ.get("DB_HOST")
    port = os.environ.get("DB_PORT", "5432")
    dbname = os.environ.get("DB_NAME", "postgres")
    
    if not all([user, password, host, dbname]):
         raise ValueError("DB_USER, DB_PASSWORD, DB_HOST, and DB_NAME must be set in environment variables")

    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
