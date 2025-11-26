import os
import logging
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from dotenv import load_dotenv

from llama_index.core import SQLDatabase, VectorStoreIndex, Settings
from llama_index.core.query_engine import NLSQLTableQueryEngine, RouterQueryEngine, RetrieverQueryEngine
from llama_index.core.tools import QueryEngineTool, ToolMetadata
from llama_index.core.selectors import LLMSingleSelector
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.postgres import PGVectorStore

from sqlalchemy import create_engine
from database import get_postgres_connection_string, get_supabase_client
from prompts import SYSTEM_PROMPT

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load Env
load_dotenv()

# Global LlamaIndex Settings
Settings.llm = OpenAI(model="gpt-4o", temperature=0)
Settings.embedding = OpenAIEmbedding(model="text-embedding-3-small", dimensions=1536)

app = FastAPI(title="MSP Intelligence Brain API")

# Global Engine Variable
query_engine = None

class ChatInput(BaseModel):
    question: str
    user_email: str
    user_name: str

class ChatOutput(BaseModel):
    answer: str
    source_nodes: List[str]

@app.on_event("startup")
async def startup_event():
    global query_engine
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
        
        sql_query_engine = NLSQLTableQueryEngine(
            sql_database=sql_database,
            tables=["proyectos", "clientes", "consultores", "proyectoequipo"],
        )
        
        sql_tool = QueryEngineTool.from_defaults(
            query_engine=sql_query_engine,
            description=(
                "Useful for translating a natural language query into a SQL query over tables containing: "
                "proyectos, clientes, consultores, proyectoequipo. "
                "Use this for questions about specific data, counts, aggregations, or structured attributes."
            ),
        )

        # 2. Setup Vector Engine (PGVector)
        # We use PGVectorStore to directly access the 'public.rag_chunks' table created by ingest.py
        # instead of SupabaseVectorStore which expects a 'vecs' managed schema.
        
        vector_store = PGVectorStore.from_params(
            database=os.environ.get("DB_NAME", "postgres"),
            host=os.environ.get("DB_HOST"),
            password=os.environ.get("DB_PASSWORD"),
            port=os.environ.get("DB_PORT", "5432"),
            user=os.environ.get("DB_USER"),
            table_name="rag_chunks",
            embed_dim=1536,
            # perform_setup=False ensures we don't try to create tables/extensions if they exist
            # but usually it's safe to leave default (True) which checks existence.
        )
        
        # We need an index to create a retriever
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
        query_engine = RouterQueryEngine(
            selector=LLMSingleSelector.from_defaults(),
            query_engine_tools=[
                sql_tool,
                vector_tool,
            ],
        )
        
        logger.info("LlamaIndex Engines Initialized Successfully.")

    except Exception as e:
        logger.error(f"Failed to initialize engines: {e}")
        raise e

@app.post("/api/chat", response_model=ChatOutput)
async def chat_endpoint(payload: ChatInput):
    if not query_engine:
        raise HTTPException(status_code=503, detail="Query Engine not initialized")

    logger.info(f"Incoming request from {payload.user_email} ({payload.user_name}): {payload.question}")

    # Personalize System Prompt
    personalized_prompt = f"Address the user as {payload.user_name}.\n" + SYSTEM_PROMPT
    
    # Update the LLM system prompt for this request (Context management in LlamaIndex can be tricky per-request)
    # A simple way is to prepend the instruction to the query or use a custom prompt template.
    # Here we will prepend to the question for simplicity in the Router context.
    full_query = f"{personalized_prompt}\n\nUser Question: {payload.question}"

    try:
        response = query_engine.query(full_query)
        
        # Extract source nodes
        source_nodes = []
        if response.source_nodes:
            for node in response.source_nodes:
                # Try to get meaningful metadata
                node_meta = node.metadata
                if node_meta:
                    source_nodes.append(str(node_meta))
                else:
                    source_nodes.append(node.get_content()[:100] + "...")
        
        return ChatOutput(
            answer=str(response),
            source_nodes=source_nodes
        )

    except Exception as e:
        logger.error(f"Error processing query: {e}")
        raise HTTPException(status_code=500, detail=str(e))
