"""
Invenzis Intelligence Brain API

A knowledge management system for Invenzis consultants that provides:
- Structured data queries (consultants, projects, clients)
- Semantic search (lessons learned, project descriptions)
- Hybrid queries combining both approaches

Architecture:
- QueryOrchestrator: Main entry point for query processing
- SafeSQLEngine: Robust SQL generation and execution
- VectorEngine: Semantic search over vectorized content
- QueryClassifier: Intent classification for routing
"""
import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from llama_index.core import Settings as LlamaSettings
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.storage.chat_store.postgres import PostgresChatStore

from models.schemas import ChatInput, ChatOutput
from orchestrator.query_orchestrator import QueryOrchestrator
from database import get_postgres_connection_string, get_supabase_client
from config.settings import get_settings

# =============================================================================
# SETUP
# =============================================================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()

# Configure LlamaIndex global settings
LlamaSettings.llm = OpenAI(model=settings.openai_model, temperature=0)
LlamaSettings.embed_model = OpenAIEmbedding(
    model=settings.openai_embedding_model, 
    dimensions=settings.embedding_dimensions
)

# =============================================================================
# GLOBAL VARIABLES
# =============================================================================

orchestrator: QueryOrchestrator = None
chat_store: PostgresChatStore = None


# =============================================================================
# LIFESPAN (Startup/Shutdown)
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup resources."""
    global orchestrator, chat_store
    
    logger.info("🚀 Initializing Invenzis Intelligence Brain...")
    
    try:
        # Initialize LLM
        llm = OpenAI(model=settings.openai_model, temperature=0)
        
        # Get database connection string
        db_url = get_postgres_connection_string()
        
        # Initialize Chat Store (memory)
        chat_store = PostgresChatStore.from_params(
            host=os.environ.get("DB_HOST"),
            port=os.environ.get("DB_PORT", "5432"),
            database=os.environ.get("DB_NAME", "postgres"),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASSWORD"),
            table_name="chat_store"
        )
        
        # Initialize Query Orchestrator
        orchestrator = QueryOrchestrator(
            llm=llm,
            db_connection_string=db_url,
            chat_store=chat_store
        )
        
        logger.info("✅ Engines initialized successfully")
        
        yield
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}", exc_info=True)
        raise
    
    finally:
        logger.info("🛑 Shutting down...")


# =============================================================================
# APP
# =============================================================================

app = FastAPI(
    title="Invenzis Intelligence Brain API",
    description="Knowledge management system for Invenzis consultants",
    version="2.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# ENDPOINTS
# =============================================================================

@app.post("/api/chat", response_model=ChatOutput)
async def chat_endpoint(payload: ChatInput):
    """
    Main chat endpoint for processing user queries.
    
    Accepts natural language questions and returns structured responses
    based on data from SQL tables and vector store.
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    logger.info(f"📥 Query from {payload.user_email}: {payload.question}")
    
    try:
        # Process query through orchestrator
        result = await orchestrator.process(
            question=payload.question,
            conversation_id=payload.conversation_id,
            user_context={
                "email": payload.user_email,
                "name": payload.user_name
            }
        )
        
        logger.info(f"📤 Response type: {result.query_type}, sources: {len(result.source_nodes)}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Error processing query: {e}", exc_info=True)
        return ChatOutput(
            answer="Hubo un error procesando tu consulta. Por favor, intenta de nuevo.",
            source_nodes=[],
            query_type="error"
        )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "ready": orchestrator is not None,
        "version": "2.0.0"
    }


@app.post("/api/trigger-ingest")
async def trigger_ingest(
    background_tasks: BackgroundTasks,
    token: str = Header(None, alias="X-Ingest-Token")
):
    """
    Trigger background ingestion of new data.
    
    Protected by X-Ingest-Token header.
    """
    secret = settings.ingest_secret
    if not secret or token != secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    from ingest import process_generic, TABLES_CONFIG
    
    async def run_ingestion():
        sb = get_supabase_client()
        for cfg in TABLES_CONFIG:
            await process_generic(sb, cfg, LlamaSettings.embed_model)
        logger.info("✅ Ingestion complete")
    
    background_tasks.add_task(run_ingestion)
    return {"status": "started", "message": "Ingestion started in background"}


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
