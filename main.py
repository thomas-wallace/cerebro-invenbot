import os
import logging
import json
from typing import List
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from llama_index.core import SQLDatabase, VectorStoreIndex, Settings
from llama_index.core.query_engine import NLSQLTableQueryEngine, RouterQueryEngine
from llama_index.core.tools import QueryEngineTool
from llama_index.core.selectors import PydanticSingleSelector
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.postgres import PGVectorStore
from llama_index.storage.chat_store.postgres import PostgresChatStore
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import CondenseQuestionChatEngine
from llama_index.core import PromptTemplate

from sqlalchemy import create_engine
from database import get_postgres_connection_string, get_supabase_client
from prompts import SYSTEM_PROMPT, TEXT_TO_SQL_PROMPT, CONDENSE_QUESTION_PROMPT

# ==========================================
# SETUP LOGGING
# ==========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ==========================================
# GLOBAL SETTINGS
# ==========================================
Settings.llm = OpenAI(model="gpt-4o", temperature=0)
Settings.embedding = OpenAIEmbedding(model="text-embedding-3-small", dimensions=1536)

app = FastAPI(title="Invenzis Intelligence Brain API")

# CORS para permitir requests desde n8n Cloud
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# GLOBAL VARIABLES
# ==========================================
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
        # SQL Engine
        db_url = get_postgres_connection_string()
        engine = create_engine(db_url)
        
        sql_database = SQLDatabase(engine, include_tables=[
            "proyectos", "clientes", "consultores", 
            "proyectoequipo", "stacktecnologico", "leccionesaprendidas"
        ])
        
        text_to_sql_template = PromptTemplate(TEXT_TO_SQL_PROMPT)

        sql_query_engine = NLSQLTableQueryEngine(
            sql_database=sql_database,
            tables=["proyectos", "clientes", "consultores", "proyectoequipo"],
            text_to_sql_prompt=text_to_sql_template,
            synthesize_response=True 
        )
        
        sql_tool = QueryEngineTool.from_defaults(
            query_engine=sql_query_engine,
            description=(
                "Consultas sobre datos estructurados de Invenzis: consultores, proyectos y clientes. "
                "Usa esto para buscar personas por nombre, rol, país o expertise."
            ),
        )

        # Vector Engine (RAG)
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
        vector_query_engine = vector_index.as_query_engine(similarity_top_k=5)
        
        vector_tool = QueryEngineTool.from_defaults(
            query_engine=vector_query_engine,
            description=(
                "Búsqueda semántica en descripciones de proyectos, problemas técnicos, "
                "soluciones y lecciones aprendidas de Invenzis."
            ),
        )

        # Router Engine
        router_engine = RouterQueryEngine(
            selector=PydanticSingleSelector.from_defaults(),
            query_engine_tools=[sql_tool, vector_tool],
            verbose=True
        )

        # Chat Store (Memoria)
        chat_store_db = PostgresChatStore.from_params(
            host=os.environ.get("DB_HOST"),
            port=os.environ.get("DB_PORT", "5432"),
            database=os.environ.get("DB_NAME", "postgres"),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASSWORD"),
            table_name="chat_store"
        )
        
        logger.info("✓ Engines Ready")

    except Exception as e:
        logger.error(f"Startup error: {e}")
        raise e

@app.post("/api/chat", response_model=ChatOutput)
async def chat_endpoint(payload: ChatInput):
    if not router_engine or not chat_store_db:
        raise HTTPException(status_code=503, detail="Engines not initialized")

    logger.info(f"Query from {payload.user_email}: {payload.question}")

    try:
        memory = ChatMemoryBuffer.from_defaults(
            token_limit=3000,
            chat_store=chat_store_db,
            chat_store_key=payload.conversation_id
        )

        chat_engine = CondenseQuestionChatEngine.from_defaults(
            query_engine=router_engine,
            memory=memory,
            condense_question_prompt=PromptTemplate(CONDENSE_QUESTION_PROMPT),
            verbose=True
        )

        full_query = f"{payload.question}\n\nSYSTEM INSTRUCTIONS:\n{SYSTEM_PROMPT}"
        response = chat_engine.chat(full_query)
        raw_text = str(response)

        # LIMPIEZA DE RESPUESTA (Parsear JSON si el LLM lo envió)
        final_answer = raw_text
        if "{" in raw_text:
            try:
                start = raw_text.find("{")
                end = raw_text.rfind("}") + 1
                data = json.loads(raw_text[start:end])
                final_answer = data.get("answer", raw_text)
            except:
                pass

        # Manejo de "no encontré nada"
        if not final_answer.strip() or "empty response" in raw_text.lower():
            final_answer = "No encontré información específica sobre eso en los registros de Invenzis. ¿Puedes darme más detalles?"

        # Extract source nodes (Citas)
        source_nodes = []
        if hasattr(response, 'source_nodes'):
            for node in response.source_nodes:
                m = node.metadata
                if m:
                    info = f"Fuente: {m.get('fuentetabla', 'Documentos')} | ID: {m.get('fuenteid', 'N/A')}"
                    source_nodes.append(info)

        return ChatOutput(answer=final_answer, source_nodes=source_nodes)

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return ChatOutput(answer="Hubo un error técnico. Intenta de nuevo.", source_nodes=[])

@app.get("/health")
async def health():
    return {"status": "ok", "ready": router_engine is not None}

@app.post("/api/trigger-ingest")
async def trigger_ingest(background_tasks: BackgroundTasks, token: str = Header(None, alias="X-Ingest-Token")):
    secret = os.environ.get("INGEST_SECRET")
    if not secret or token != secret:
        raise HTTPException(status_code=401)
    
    from ingest import process_generic, TABLES_CONFIG
    async def run_ingestion():
        sb = get_supabase_client()
        for cfg in TABLES_CONFIG:
            await process_generic(sb, cfg, Settings.embedding)
            
    background_tasks.add_task(run_ingestion)
    return {"status": "started"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
