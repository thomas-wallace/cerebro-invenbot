"""
Query Orchestrator for Invenzis Intelligence Brain.

This is the main entry point for processing user queries.
It coordinates classification, routing, execution, and synthesis.

Uses CleanChatMemory to ensure:
- System prompts are never saved with user messages
- Error responses are never saved
- History is limited to prevent context overflow
"""
import logging
from typing import Optional, Dict, Any, List

from llama_index.llms.openai import OpenAI
from llama_index.storage.chat_store.postgres import PostgresChatStore

from models.schemas import QueryType, QueryResult, ChatOutput
from orchestrator.classifier import QueryClassifier
from engines.safe_sql_engine import SafeSQLEngine, SQLResult
from engines.vector_engine import VectorEngine, VectorResult
from prompts.templates import SYNTHESIS_PROMPT, NO_RESULTS_GENERIC
from config.settings import get_settings
from memory.chat_memory import CleanChatMemory

logger = logging.getLogger(__name__)

# Threshold for disambiguation - if more than this many results, ask for clarification
DISAMBIGUATION_THRESHOLD = 5


class QueryOrchestrator:
    """
    Main orchestrator for query processing.
    
    Flow:
    1. Receive user question
    2. Classify query intent
    3. Route to appropriate engine(s)
    4. Check for disambiguation needs
    5. Synthesize response
    6. Save successful exchanges to clean memory
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
        
        # Initialize clean memory if chat_store provided
        self.memory = CleanChatMemory(chat_store) if chat_store else None
    
    async def process(
        self,
        question: str,
        conversation_id: Optional[str] = None,
        user_context: Optional[Dict] = None
    ) -> ChatOutput:
        """
        Process a user query end-to-end.
        
        Args:
            question: User's natural language question (clean, no system prompt)
            conversation_id: Optional conversation ID for memory
            user_context: Optional user context (email, name, etc.)
            
        Returns:
            ChatOutput with answer and source information
        """
        logger.info(f"Processing query: {question[:100]}...")
        
        # Clean the question - remove any system prompt if accidentally included
        clean_question = self._clean_question(question)
        
        try:
            # Step 1: Classify query
            classification = self.classifier.classify(clean_question)
            logger.info(f"Query classified as: {classification.query_type.value}")
            
            # Step 2: Execute based on classification
            result = await self._execute_query(clean_question, classification)
            
            # Step 3: Build response
            if result.needs_disambiguation:
                answer = self._build_disambiguation_response(result)
            else:
                answer = await self._synthesize_response(clean_question, result)
            
            # Step 4: Build source nodes
            source_nodes = self._build_source_nodes(result)
            
            # Step 5: Save to memory (only if successful)
            if conversation_id and self.memory and result.success:
                self.memory.save_exchange(
                    conversation_id=conversation_id,
                    user_question=clean_question,
                    assistant_response=answer,
                    was_successful=result.success
                )
            
            return ChatOutput(
                answer=answer,
                source_nodes=source_nodes,
                query_type=classification.query_type.value
            )
            
        except Exception as e:
            logger.error(f"Query processing failed: {e}", exc_info=True)
            # NEVER expose technical errors to users
            # NEVER save errors to memory
            return ChatOutput(
                answer="No encontré información sobre eso en los registros. ¿Podrías reformular tu pregunta o dar más detalles?",
                source_nodes=[],
                query_type="error"
            )
    
    def _clean_question(self, question: str) -> str:
        """Remove any system prompt remnants from the question."""
        # Check for system prompt injection
        if "SYSTEM INSTRUCTIONS" in question:
            # Take only the part before system instructions
            question = question.split("SYSTEM INSTRUCTIONS")[0].strip()
        
        if "SYSTEM_PROMPT" in question:
            question = question.split("SYSTEM_PROMPT")[0].strip()
        
        return question.strip()
    
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
            
            # Check if disambiguation is needed (too many person results)
            needs_disambiguation = False
            disambiguation_message = None
            
            if sql_result.success and query_type == QueryType.CONSULTANT_SEARCH:
                if len(sql_result.data) > DISAMBIGUATION_THRESHOLD:
                    needs_disambiguation = True
                    disambiguation_message = f"Encontré {len(sql_result.data)} personas con ese nombre."
            
            return QueryResult(
                success=sql_result.success,
                data=sql_result.data[:10] if needs_disambiguation else sql_result.data,
                query_type=query_type,
                sql_executed=sql_result.sql_executed,
                error_message=sql_result.error_message,
                needs_disambiguation=needs_disambiguation,
                disambiguation_message=disambiguation_message
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
            
            return QueryResult(
                success=sql_result.success or vector_result.success,
                data=sql_result.data,
                query_type=query_type,
                sql_executed=sql_result.sql_executed,
                vector_chunks=vector_result.chunks if vector_result.success else None,
                error_message=sql_result.error_message or vector_result.error_message
            )
    
    def _build_disambiguation_response(self, result: QueryResult) -> str:
        """Build a response asking user to clarify which person they mean."""
        lines = [result.disambiguation_message or "Encontré varias personas con ese nombre:"]
        lines.append("")
        
        for row in result.data[:10]:
            name = row.get('nombrecompleto', 'N/A')
            email = row.get('email', 'N/A')
            role = row.get('rolprincipal', '')
            location = row.get('ubicacion', '')
            
            person_info = f"• **{name}** ({email})"
            if role:
                person_info += f" - {role}"
            if location:
                person_info += f" - {location}"
            
            lines.append(person_info)
        
        lines.append("")
        lines.append("¿Podrías indicar a cuál te refieres? Puedes usar el email o el apellido completo.")
        
        return "\n".join(lines)
    
    async def _synthesize_response(
        self, 
        question: str, 
        result: QueryResult
    ) -> str:
        """Generate user-friendly response from query results."""
        
        # CRÍTICO: Si la query falló, retornar mensaje genérico SIN detalles técnicos
        if not result.success:
            logger.warning(f"Query failed: {result.error_message}")
            return "No encontré información sobre eso en los registros. ¿Podrías darme más detalles o reformular tu pregunta?"
        
        # Si no hay datos, mensaje amigable
        if not result.data and not result.vector_chunks:
            return "No encontré resultados para tu consulta. ¿Podrías ser más específico?"
        
        # Format results for synthesis
        formatted_results = self._format_results_for_synthesis(result)
        
        # If empty after formatting
        if not formatted_results or formatted_results == "No se encontraron resultados.":
            return "No encontré información relevante. ¿Podrías reformular tu pregunta?"
        
        # Use LLM to synthesize
        prompt = SYNTHESIS_PROMPT.format(
            query_results=formatted_results,
            question=question
        )
        
        try:
            # Use temperature=0 for deterministic output
            response = await self.llm.acomplete(prompt, temperature=0)
            synthesized = str(response).strip()
            
            # SAFETY CHECK: Make sure response doesn't contain SQL or error messages
            error_indicators = ["error", "sql", "select", "from consultores", "where", "```"]
            if any(indicator in synthesized.lower()[:150] for indicator in error_indicators):
                logger.warning("Synthesis contained technical content, using fallback")
                return self._format_human_readable(result)
            
            return synthesized
            
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return self._format_human_readable(result)
    
    def _format_human_readable(self, result: QueryResult) -> str:
        """Format results in a human-readable way without LLM."""
        lines = ["Encontré la siguiente información:"]
        lines.append("")
        
        for row in result.data[:5]:
            name = row.get('nombrecompleto', '')
            email = row.get('email', '')
            role = row.get('rolprincipal', '')
            location = row.get('ubicacion', '')
            
            if name:
                info = f"• **{name}**"
                if email:
                    info += f" ({email})"
                if role:
                    info += f" - {role}"
                if location:
                    info += f" - {location}"
                lines.append(info)
        
        return "\n".join(lines) if len(lines) > 2 else "No encontré información relevante."
    
    def _format_results_for_synthesis(self, result: QueryResult) -> str:
        """Format query results for the synthesis prompt."""
        lines = []
        
        # Format SQL data
        if result.data:
            lines.append("## Datos encontrados:")
            for i, row in enumerate(result.data, 1):
                # Filter out None values and format nicely
                row_parts = []
                for k, v in row.items():
                    if v is not None and str(v).strip():
                        row_parts.append(f"{k}: {v}")
                if row_parts:
                    lines.append(f"{i}. " + ", ".join(row_parts))
            lines.append("")
        
        # Format vector chunks
        if result.vector_chunks:
            lines.append("## Información contextual:")
            for chunk in result.vector_chunks[:3]:  # Limit to top 3
                text = chunk.get('text', '')
                if text:
                    lines.append(f"- {text[:500]}")
            lines.append("")
        
        if not lines:
            return "No se encontraron resultados."
        
        return "\n".join(lines)
    
    def _build_source_nodes(self, result: QueryResult) -> List[str]:
        """Build source citations from query results."""
        sources = []
        
        if result.sql_executed and result.success:
            sources.append(f"Fuente: Base de datos | Tipo: {result.query_type.value}")
        
        if result.vector_chunks:
            for chunk in result.vector_chunks[:3]:
                metadata = chunk.get("metadata", {})
                source_table = metadata.get("fuentetabla", "documento")
                source_id = metadata.get("fuenteid", "N/A")
                sources.append(f"Fuente: {source_table} | ID: {source_id}")
        
        return sources
