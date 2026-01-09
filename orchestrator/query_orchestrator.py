"""
Query Orchestrator for Invenzis Intelligence Brain.

This is the main entry point for processing user queries.
It coordinates classification, routing, execution, and synthesis.
"""
import logging
from typing import Optional, Dict, Any

from llama_index.llms.openai import OpenAI
from llama_index.storage.chat_store.postgres import PostgresChatStore
from llama_index.core.memory import ChatMemoryBuffer

from models.schemas import QueryType, QueryResult, ChatOutput
from orchestrator.classifier import QueryClassifier
from engines.safe_sql_engine import SafeSQLEngine, SQLResult
from engines.vector_engine import VectorEngine, VectorResult
from prompts.templates import SYNTHESIS_PROMPT, NO_RESULTS_GENERIC
from config.settings import get_settings

logger = logging.getLogger(__name__)


class QueryOrchestrator:
    """
    Main orchestrator for query processing.
    
    Flow:
    1. Receive user question
    2. Get context from chat memory (if available)
    3. Classify query intent
    4. Route to appropriate engine(s)
    5. Synthesize response
    6. Save to memory
    
    Example:
        orchestrator = QueryOrchestrator(llm, db_url)
        result = await orchestrator.process(
            "¿Quién es Constanza?",
            conversation_id="conv_123"
        )
    """
    
    def __init__(
        self,
        llm: OpenAI,
        db_connection_string: str,
        chat_store: Optional[PostgresChatStore] = None
    ):
        """
        Initialize the orchestrator.
        
        Args:
            llm: OpenAI LLM instance
            db_connection_string: PostgreSQL connection string
            chat_store: Optional chat store for memory
        """
        self.llm = llm
        self.settings = get_settings()
        
        # Initialize components
        self.classifier = QueryClassifier(llm)
        self.sql_engine = SafeSQLEngine(
            llm=llm,
            db_connection_string=db_connection_string,
            max_retries=self.settings.sql_max_retries
        )
        self.vector_engine = VectorEngine.from_env(top_k=self.settings.vector_top_k)
        self.chat_store = chat_store
    
    async def process(
        self,
        question: str,
        conversation_id: Optional[str] = None,
        user_context: Optional[Dict] = None
    ) -> ChatOutput:
        """
        Process a user query end-to-end.
        
        Args:
            question: User's natural language question
            conversation_id: Optional conversation ID for memory
            user_context: Optional user context (email, name, etc.)
            
        Returns:
            ChatOutput with answer and source information
        """
        logger.info(f"Processing query: {question[:100]}...")
        
        try:
            # Step 1: Classify query
            classification = self.classifier.classify(question)
            logger.info(f"Query classified as: {classification.query_type.value}")
            
            # Step 2: Execute based on classification
            result = await self._execute_query(question, classification)
            
            # Step 3: Synthesize response
            answer = await self._synthesize_response(question, result)
            
            # Step 4: Build source nodes
            source_nodes = self._build_source_nodes(result)
            
            return ChatOutput(
                answer=answer,
                source_nodes=source_nodes,
                query_type=classification.query_type.value
            )
            
        except Exception as e:
            logger.error(f"Query processing failed: {e}", exc_info=True)
            return ChatOutput(
                answer="Hubo un problema procesando tu consulta. Por favor, intenta reformular tu pregunta.",
                source_nodes=[],
                query_type="error"
            )
    
    async def _execute_query(
        self, 
        question: str, 
        classification
    ) -> QueryResult:
        """Execute query based on classification type."""
        
        query_type = classification.query_type
        
        if query_type in [QueryType.CONSULTANT_SEARCH, QueryType.PROJECT_SEARCH, 
                          QueryType.CLIENT_SEARCH]:
            # SQL-based queries
            sql_result = await self.sql_engine.query(question)
            
            return QueryResult(
                success=sql_result.success,
                data=sql_result.data,
                query_type=query_type,
                sql_executed=sql_result.sql_executed,
                error_message=sql_result.error_message
            )
            
        elif query_type == QueryType.KNOWLEDGE_SEARCH:
            # Vector-based queries
            vector_result = await self.vector_engine.search(question)
            
            return QueryResult(
                success=vector_result.success,
                data=[],
                query_type=query_type,
                vector_chunks=vector_result.chunks,
                error_message=vector_result.error_message
            )
            
        else:  # HYBRID or UNKNOWN
            # Try both engines
            sql_result = await self.sql_engine.query(question)
            vector_result = await self.vector_engine.search(question)
            
            # Combine results
            return QueryResult(
                success=sql_result.success or vector_result.success,
                data=sql_result.data,
                query_type=query_type,
                sql_executed=sql_result.sql_executed,
                vector_chunks=vector_result.chunks if vector_result.success else None,
                error_message=sql_result.error_message or vector_result.error_message
            )
    
    async def _synthesize_response(
        self, 
        question: str, 
        result: QueryResult
    ) -> str:
        """Generate user-friendly response from query results."""
        
        # Format results for synthesis
        formatted_results = self._format_results_for_synthesis(result)
        
        # If no results, return appropriate message
        if not formatted_results or formatted_results == "No se encontraron resultados.":
            return NO_RESULTS_GENERIC
        
        # Use LLM to synthesize
        prompt = SYNTHESIS_PROMPT.format(
            query_results=formatted_results,
            question=question
        )
        
        try:
            response = await self.llm.acomplete(prompt)
            return str(response).strip()
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            # Fallback to raw results
            return f"Encontré la siguiente información:\n\n{formatted_results}"
    
    def _format_results_for_synthesis(self, result: QueryResult) -> str:
        """Format query results for the synthesis prompt."""
        lines = []
        
        # Format SQL data
        if result.data:
            lines.append("## Datos de Base de Datos:")
            for i, row in enumerate(result.data, 1):
                row_str = ", ".join(f"{k}: {v}" for k, v in row.items() if v is not None)
                lines.append(f"{i}. {row_str}")
            lines.append("")
        
        # Format vector chunks
        if result.vector_chunks:
            lines.append("## Información Contextual:")
            for chunk in result.vector_chunks[:3]:  # Limit to top 3
                lines.append(f"- {chunk.get('text', '')[:500]}")
            lines.append("")
        
        if not lines:
            return "No se encontraron resultados."
        
        return "\n".join(lines)
    
    def _build_source_nodes(self, result: QueryResult) -> list:
        """Build source citations from query results."""
        sources = []
        
        if result.sql_executed:
            sources.append(f"Fuente: SQL Query | Tipo: {result.query_type.value}")
        
        if result.vector_chunks:
            for chunk in result.vector_chunks[:3]:
                metadata = chunk.get("metadata", {})
                source_table = metadata.get("fuentetabla", "documento")
                source_id = metadata.get("fuenteid", "N/A")
                sources.append(f"Fuente: {source_table} | ID: {source_id}")
        
        return sources
    
    def get_memory_buffer(self, conversation_id: str) -> Optional[ChatMemoryBuffer]:
        """Get memory buffer for a conversation."""
        if not self.chat_store:
            return None
        
        return ChatMemoryBuffer.from_defaults(
            token_limit=self.settings.chat_token_limit,
            chat_store=self.chat_store,
            chat_store_key=conversation_id
        )
