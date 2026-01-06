import os
import logging
import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Depends, Header
from pydantic import BaseModel
from dotenv import load_dotenv

from llama_index.core import SQLDatabase, VectorStoreIndex, Settings
from llama_index.core.query_engine import NLSQLTableQueryEngine, RouterQueryEngine, RetrieverQueryEngine
from llama_index.core.tools import QueryEngineTool, ToolMetadata
from llama_index.core.selectors import LLMSingleSelector, PydanticSingleSelector
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.postgres import PGVectorStore
from llama_index.storage.chat_store.postgres import PostgresChatStore
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import CondenseQuestionChatEngine, ContextChatEngine

from sqlalchemy import create_engine
from database import get_postgres_connection_string, get_supabase_client
from prompts import SYSTEM_PROMPT, TEXT_TO_SQL_PROMPT
from llama_index.core import PromptTemplate  

# Ingestion imports
from ingest import process_generic, TABLES_CONFIG

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load Env
load_dotenv()

# Global LlamaIndex Settings
Settings.llm = OpenAI(model="gpt-4o", temperature=0)
Settings.embedding = OpenAIEmbedding(model="text-embedding-3-small", dimensions=1536)

app = FastAPI(title="MSP Intelligence Brain API")

# Global Variables
router_engine = None
chat_store_db = None

class ChatInput(BaseModel):
    question: str
    user_email: str
    user_name: str
    conversation_id: str

class ChatOutput(BaseModel):
    answer: str
    source_nodes: List[str]

@app.on_event("startup")
async def startup_event():
    global router_engine, chat_store_db
    logger.info("Initializing LlamaIndex Engines...")

    try:
        # 1. Setup SQL Engine
        db_url = get_postgres_connection_string()
        engine = create_engine(db_url)
        # Use lowercase table names as Postgres stores them lowercase by default
        sql_database = SQLDatabase(engine, include_tables=[
            "proyectos", "clientes", "consultores", "proyectoequipo", 
            "stacktecnologico", "leccionesaprendidas"
        ])
        
        text_to_sql_template = PromptTemplate(TEXT_TO_SQL_PROMPT)

        sql_query_engine = NLSQLTableQueryEngine(
            sql_database=sql_database,
            tables=["proyectos", "clientes", "consultores", "proyectoequipo"],
            text_to_sql_prompt=text_to_sql_template
        )
        
        sql_tool = QueryEngineTool.from_defaults(
            query_engine=sql_query_engine,
            description=(
                "Utilizar para consultas sobre datos exactos y estructurados. "
                "Tablas: 'proyectos' (nombres, estados), 'clientes', 'consultores' (nombres, roles, expertise, skills, seniority), "
                "'proyectoequipo' (asignaciones). "
                "Usa esto para buscar personas específicas, sus roles, o listas de proyectos."
            ),
        )

        # 2. Setup Vector Engine (PGVector)
        vector_store = PGVectorStore.from_params(
            database=os.environ.get("DB_NAME", "postgres"),
            host=os.environ.get("DB_HOST"),
            password=os.environ.get("DB_PASSWORD"),
            port=os.environ.get("DB_PORT", "5432"),
            user=os.environ.get("DB_USER"),
            table_name="rag_chunks",
            embed_dim=1536,
        )
        
        vector_index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
        vector_query_engine = vector_index.as_query_engine()
        
        vector_tool = QueryEngineTool.from_defaults(
            query_engine=vector_query_engine,
            description=(
                "Useful for semantic search over unstructured text data like "
                "Project Descriptions, Problems, Solutions, and Lessons Learned."
            ),
        )

        # 3. Setup Router Engine
        router_engine = RouterQueryEngine(
            selector=PydanticSingleSelector.from_defaults(),
            query_engine_tools=[
                sql_tool,
                vector_tool,
            ],
            verbose=True
        )

        # 4. Setup Persistent Chat Store
        chat_store_db = PostgresChatStore.from_params(
            host=os.environ.get("DB_HOST"),
            port=os.environ.get("DB_PORT", "5432"),
            database=os.environ.get("DB_NAME", "postgres"),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASSWORD"),
            table_name="chat_store"
        )
        
        logger.info("LlamaIndex Engines Initialized Successfully.")

    except Exception as e:
        logger.error(f"Failed to initialize engines: {e}")
        raise e

@app.post("/api/chat", response_model=ChatOutput)
async def chat_endpoint(payload: ChatInput):
    if not router_engine or not chat_store_db:
        raise HTTPException(status_code=503, detail="Engines not initialized")

    logger.info(f"Incoming request from {payload.user_email} ({payload.user_name}): {payload.question}")

    # Persistent Memory Logic using Supabase
    memory = ChatMemoryBuffer.from_defaults(
        token_limit=3000,
        chat_store=chat_store_db,
        chat_store_key=payload.conversation_id
    )

    # Use CondenseQuestionChatEngine to handle chat history and context
    # NOTE: system_prompt is NOT supported in from_defaults for CondenseQuestionChatEngine
    chat_engine = CondenseQuestionChatEngine.from_defaults(
        query_engine=router_engine,
        memory=memory,
        verbose=True
    )

    # Inject system prompt by appending it to the user query
    full_query = f"{payload.question}\n\nINSTRUCCIONES DEL SISTEMA:\n{SYSTEM_PROMPT}"

    try:
        response = chat_engine.chat(full_query)
        response_text = str(response)
        logger.info(f"Raw AI Response: {response_text}")
        
        # 1. Manejo de "Empty Response"
        if not response_text.strip() or "empty response" in response_text.lower():
            logger.warning("IA returned empty response, formatting default message")
            final_answer = "Lo siento, no encontré información específica en mis registros sobre eso. ¿Podrías ser más específico con el nombre del proyecto o consultor?"
        else:
            # 2. Intentar limpiar el JSON si la IA lo mandó con markdown o texto extra
            if "{" in response_text:
                try:
                    # Extraer solo el contenido entre las primeras { y últimas }
                    start = response_text.find("{")
                    end = response_text.rfind("}") + 1
                    json_str = response_text[start:end]
                    data = json.loads(json_str)
                    final_answer = data.get("answer", response_text)
                except:
                    final_answer = response_text
            else:
                final_answer = response_text

        # Extract source nodes for citation
        source_nodes = []
        if response.source_nodes:
            for node in response.source_nodes:
                node_meta = node.metadata
                if node_meta:
                    source_nodes.append(str(node_meta))
                else:
                    source_nodes.append(node.get_content()[:100] + "...")
        
        return ChatOutput(
            answer=final_answer,
            source_nodes=source_nodes
        )

    except Exception as e:
        logger.error(f"Error processing query: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def run_ingestion():
    """
    Background task to run the ingestion process.
    """
    logger.info("Starting background ingestion process...")
    try:
        supabase = get_supabase_client()
        # Use the global Settings.embedding which is already initialized
        embed_model = Settings.embedding
        
        for config in TABLES_CONFIG:
            await process_generic(supabase, config, embed_model)
            
        logger.info("Background ingestion process completed successfully.")
    except Exception as e:
        logger.error(f"Background ingestion process failed: {e}")

@app.post("/api/trigger-ingest")
async def trigger_ingest(
    background_tasks: BackgroundTasks,
    token: str = Header(None, alias="X-Ingest-Token")
):
    """
    Trigger the ingestion process manually or via external tools (e.g. n8n).
    Requires a secret token in the X-Ingest-Token header or 'token' query param.
    """
    secret = os.environ.get("INGEST_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="INGEST_SECRET not configured on server")
    
    if token != secret:
        # Also check query param if header is missing/invalid, for easier testing
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    background_tasks.add_task(run_ingestion)
    return {"status": "Ingestion triggered", "message": "The ingestion process has been started in the background."}
