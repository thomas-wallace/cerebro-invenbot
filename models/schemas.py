"""
Pydantic models for Invenzis Intelligence Brain API.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


# =============================================================================
# API Models
# =============================================================================

class ChatInput(BaseModel):
    """Input model for chat endpoint."""
    question: str = Field(..., description="User's question in natural language")
    user_email: str = Field(..., description="User's email for identification")
    user_name: str = Field(..., description="User's display name")
    conversation_id: str = Field(..., description="Conversation ID for memory")


class ChatOutput(BaseModel):
    """Output model for chat endpoint."""
    answer: str = Field(..., description="Generated response")
    source_nodes: List[str] = Field(default_factory=list, description="Source citations")
    query_type: Optional[str] = Field(None, description="Type of query executed (SQL/Vector/Hybrid)")


# =============================================================================
# Internal Models
# =============================================================================

class QueryType(str, Enum):
    """Types of queries the system can handle."""
    CONSULTANT_SEARCH = "consultant_search"
    PROJECT_SEARCH = "project_search"
    CLIENT_SEARCH = "client_search"
    KNOWLEDGE_SEARCH = "knowledge_search"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class ClassificationResult(BaseModel):
    """Result of query classification."""
    query_type: QueryType
    entities: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ConsultantInfo(BaseModel):
    """Structured consultant information."""
    consultorid: int
    nombrecompleto: str
    email: str
    rolprincipal: Optional[str] = None
    ubicacion: Optional[str] = None
    nivelsenioridad: Optional[str] = None
    disponibilidad: Optional[str] = None
    expertise: Optional[List[str]] = None


class ProjectInfo(BaseModel):
    """Structured project information."""
    proyectoid: int
    nombreproyecto: str
    estado: Optional[str] = None
    tiposervicio: Optional[str] = None
    cliente: Optional[str] = None


class QueryResult(BaseModel):
    """Unified query result for orchestrator."""
    success: bool
    data: List[Dict[str, Any]] = Field(default_factory=list)
    query_type: QueryType
    sql_executed: Optional[str] = None
    vector_chunks: Optional[List[Dict]] = None
    error_message: Optional[str] = None
    needs_disambiguation: bool = Field(default=False, description="True if multiple matches found and clarification needed")
    disambiguation_message: Optional[str] = Field(None, description="Message to show when disambiguation is needed")

