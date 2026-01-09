"""
Safe SQL Engine for Invenzis Intelligence Brain.

This engine generates, validates, and executes SQL queries safely.
It handles the common problem where LLMs include explanatory text with SQL,
and provides retry logic with error context for failed queries.
"""
import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from llama_index.llms.openai import OpenAI

from utils.sql_extractor import (
    extract_sql, 
    validate_sql_safety, 
    filter_forbidden_fields,
    SQLExtractionError
)
from prompts.templates import (
    SQL_GENERATION_PROMPT, 
    SQL_RETRY_PROMPT, 
    DATABASE_SCHEMA
)
from config.settings import get_settings

logger = logging.getLogger(__name__)

# Timeout for LLM calls (in seconds)
LLM_TIMEOUT = 15.0


@dataclass
class SQLResult:
    """Result of a SQL query execution."""
    success: bool
    data: List[Dict[str, Any]]
    sql_executed: str
    error_message: Optional[str] = None
    retries_used: int = 0


class SafeSQLEngine:
    """
    SQL Engine that generates and executes SQL queries safely.
    
    Features:
    - Generates SQL using strict, focused prompts
    - Extracts ONLY valid SQL from LLM responses (handles explanatory text)
    - Validates SQL for safety (no DROP, DELETE, etc.)
    - Filters forbidden fields from results
    - Retries with error context on failure
    - Timeout protection for LLM calls
    
    Example:
        engine = SafeSQLEngine(llm, db_url)
        result = await engine.query("¿Quién es Constanza?")
        if result.success:
            print(result.data)
    """
    
    def __init__(
        self, 
        llm: OpenAI,
        db_connection_string: str,
        max_retries: int = 2  # Reduced from 3 to avoid long waits
    ):
        """
        Initialize the SafeSQLEngine.
        
        Args:
            llm: LlamaIndex OpenAI LLM instance
            db_connection_string: PostgreSQL connection string
            max_retries: Maximum number of retry attempts for failed queries
        """
        self.llm = llm
        self.engine = create_engine(db_connection_string)
        self.max_retries = max_retries
        self.settings = get_settings()
        
    async def query(
        self, 
        question: str, 
        context: Optional[Dict] = None
    ) -> SQLResult:
        """
        Process a natural language question and return SQL results.
        
        Args:
            question: Natural language question from user
            context: Optional context (e.g., from chat history)
            
        Returns:
            SQLResult with query results or error information
        """
        logger.info(f"Processing SQL query: {question[:100]}...")
        
        last_error = None
        last_sql = None
        raw_response = None
        sql = None
        
        for attempt in range(self.max_retries):
            try:
                # Step 1: Generate SQL with timeout
                if attempt == 0:
                    raw_response = await asyncio.wait_for(
                        self._generate_sql(question),
                        timeout=LLM_TIMEOUT
                    )
                else:
                    # Retry with error context
                    raw_response = await asyncio.wait_for(
                        self._generate_sql_retry(question, last_sql, last_error),
                        timeout=LLM_TIMEOUT
                    )
                
                logger.debug(f"Raw LLM response: {raw_response[:200]}...")
                
                # Step 2: Extract SQL from response
                sql = extract_sql(raw_response)
                logger.info(f"Extracted SQL: {sql[:200]}...")
                
                # Step 3: Validate SQL safety
                is_safe, safety_error = validate_sql_safety(sql)
                if not is_safe:
                    raise ValueError(f"SQL safety check failed: {safety_error}")
                
                # Step 4: Filter forbidden fields from SQL
                sql = filter_forbidden_fields(sql, self.settings.forbidden_fields)
                
                # Step 5: Execute SQL
                data = self._execute_sql(sql)
                
                # Step 6: Filter forbidden fields from results
                data = self._filter_result_fields(data)
                
                logger.info(f"Query successful, returned {len(data)} rows")
                
                return SQLResult(
                    success=True,
                    data=data,
                    sql_executed=sql,
                    retries_used=attempt
                )
                
            except asyncio.TimeoutError:
                logger.warning(f"LLM timeout (attempt {attempt + 1}/{self.max_retries})")
                last_error = "LLM request timed out"
                last_sql = None  # No valid SQL from timeout
                continue
                
            except SQLExtractionError as e:
                logger.warning(f"SQL extraction failed (attempt {attempt + 1}): {e}")
                last_error = "Could not generate valid SQL query"
                last_sql = None  # Don't pass raw response as SQL
                continue
                
            except SQLAlchemyError as e:
                logger.warning(f"SQL execution failed (attempt {attempt + 1}): {e}")
                last_error = str(e)
                last_sql = sql if sql else None
                continue
                
            except Exception as e:
                logger.warning(f"Query failed (attempt {attempt + 1}): {e}")
                last_error = str(e)
                last_sql = sql if sql else None
                continue
        
        # All retries exhausted
        logger.error(f"Query failed after {self.max_retries} attempts: {last_error}")
        return SQLResult(
            success=False,
            data=[],
            sql_executed=last_sql or "",
            error_message=last_error,
            retries_used=self.max_retries
        )
    
    async def _generate_sql(self, question: str) -> str:
        """Generate SQL from a question using the LLM."""
        prompt = SQL_GENERATION_PROMPT.format(
            schema=DATABASE_SCHEMA,
            question=question
        )
        
        # Use temperature=0 for deterministic output
        response = await self.llm.acomplete(prompt, temperature=0)
        return str(response).strip()
    
    async def _generate_sql_retry(
        self, 
        question: str, 
        failed_sql: Optional[str], 
        error: str
    ) -> str:
        """Generate SQL with retry context after a failure."""
        prompt = SQL_RETRY_PROMPT.format(
            schema=DATABASE_SCHEMA,
            question=question,
            failed_sql=failed_sql or "No se generó SQL válido",
            error=error
        )
        
        # Use temperature=0 for deterministic output
        response = await self.llm.acomplete(prompt, temperature=0)
        return str(response).strip()
    
    def _execute_sql(self, sql: str) -> List[Dict[str, Any]]:
        """Execute SQL and return results as list of dicts."""
        with self.engine.connect() as connection:
            result = connection.execute(text(sql))
            columns = result.keys()
            rows = result.fetchall()
            
            return [dict(zip(columns, row)) for row in rows]
    
    def _filter_result_fields(
        self, 
        data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Remove forbidden fields from query results."""
        forbidden = set(f.lower() for f in self.settings.forbidden_fields)
        
        filtered_data = []
        for row in data:
            filtered_row = {
                k: v for k, v in row.items() 
                if k.lower() not in forbidden
            }
            filtered_data.append(filtered_row)
        
        return filtered_data
    
    def search_consultant(self, name: str) -> str:
        """
        Generate SQL for consultant search.
        
        This is a helper method that generates a pre-built SQL query
        for the common case of searching for a consultant by name.
        """
        # Normalize name for search
        name_clean = name.strip().lower()
        
        # Generate variations for fuzzy matching
        sql = f"""
        SELECT consultorid, nombrecompleto, email, rolprincipal, 
               nivelsenioridad, ubicacion, disponibilidad
        FROM consultores 
        WHERE LOWER(nombrecompleto) ILIKE '%{name_clean}%'
        AND activo = true;
        """
        return sql.strip()
    
    def search_consultant_projects(self, name: str) -> str:
        """
        Generate SQL for finding a consultant's projects.
        
        Uses JOIN with proyectoequipo to find project assignments.
        """
        name_clean = name.strip().lower()
        
        sql = f"""
        SELECT p.nombreproyecto, pe.rolenproyecto, c.nombrecliente, 
               p.estado, p.tiposervicio
        FROM consultores co
        JOIN proyectoequipo pe ON co.consultorid = pe.consultorid
        JOIN proyectos p ON pe.proyectoid = p.proyectoid
        LEFT JOIN clientes c ON p.clienteid = c.clienteid
        WHERE LOWER(co.nombrecompleto) ILIKE '%{name_clean}%' 
        AND pe.activo = true;
        """
        return sql.strip()
    
    def search_clients_by_industry(self, industry: str) -> str:
        """
        Generate SQL for finding clients by industry.
        """
        industry_clean = industry.strip().lower()
        
        sql = f"""
        SELECT clienteid, nombrecliente, industria, pais, ubicacion
        FROM clientes 
        WHERE LOWER(industria) ILIKE '%{industry_clean}%'
        AND activo = true;
        """
        return sql.strip()
    
    def search_experts(self, technology: str) -> str:
        """
        Generate SQL for finding consultants with expertise in a technology.
        
        Searches the JSONB expertise field.
        """
        tech_clean = technology.strip().lower()
        
        sql = f"""
        SELECT consultorid, nombrecompleto, email, rolprincipal, 
               expertise, ubicacion
        FROM consultores 
        WHERE expertise::text ILIKE '%{tech_clean}%'
        AND activo = true;
        """
        return sql.strip()
