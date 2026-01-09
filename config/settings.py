"""
Centralized configuration for Invenzis Intelligence Brain.
"""
import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Database Configuration
    db_user: str
    db_password: str
    db_host: str
    db_port: str = "5432"
    db_name: str = "postgres"
    
    # Supabase Configuration
    supabase_url: str
    supabase_key: str
    
    # OpenAI Configuration
    openai_api_key: str
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    
    # Engine Configuration
    sql_max_retries: int = 3
    vector_top_k: int = 5
    chat_token_limit: int = 3000
    
    # Security
    ingest_secret: Optional[str] = None
    
    # Tables included in SQL queries
    sql_tables: List[str] = [
        "consultores", "proyectos", "clientes", 
        "proyectoequipo", "tareas", "oficinas"
    ]
    
    # Fields to NEVER return (financial/sensitive data)
    forbidden_fields: List[str] = [
        "costohora", "tarifahora", "salario", "costo", 
        "tarifa", "precio", "monto", "honorarios"
    ]
    
    # Required fields for consultant responses
    consultant_required_fields: List[str] = [
        "nombrecompleto", "email", "rolprincipal", "ubicacion"
    ]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
