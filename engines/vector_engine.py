"""
Vector Engine for semantic search in Invenzis Intelligence Brain.

This is a simplified wrapper around LlamaIndex's vector store
for searching vectorized content like project descriptions,
lessons learned, and consultant profiles.
"""
import os
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.postgres import PGVectorStore
from llama_index.core.schema import NodeWithScore

logger = logging.getLogger(__name__)


@dataclass
class VectorResult:
    """Result of a vector search."""
    success: bool
    chunks: List[Dict[str, Any]]
    error_message: Optional[str] = None


class VectorEngine:
    """
    Vector search engine for semantic queries.
    
    Used for:
    - Searching project descriptions and solutions
    - Finding lessons learned
    - Consultant expertise descriptions
    
    Example:
        engine = VectorEngine.from_env()
        result = await engine.search("problemas con migración S/4HANA")
    """
    
    def __init__(self, vector_store: PGVectorStore, top_k: int = 5):
        """
        Initialize the VectorEngine.
        
        Args:
            vector_store: PGVectorStore instance
            top_k: Number of results to return
        """
        self.vector_store = vector_store
        self.top_k = top_k
        self.index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
        self.query_engine = self.index.as_query_engine(similarity_top_k=top_k)
    
    @classmethod
    def from_env(cls, top_k: int = 5) -> "VectorEngine":
        """
        Create VectorEngine from environment variables.
        
        Expects: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
        """
        vector_store = PGVectorStore.from_params(
            database=os.environ.get("DB_NAME", "postgres"),
            host=os.environ.get("DB_HOST"),
            password=os.environ.get("DB_PASSWORD"),
            port=os.environ.get("DB_PORT", "5432"),
            user=os.environ.get("DB_USER"),
            table_name="rag_chunks",
            embed_dim=1536,
        )
        return cls(vector_store, top_k)
    
    async def search(self, query: str) -> VectorResult:
        """
        Perform semantic search.
        
        Args:
            query: Natural language search query
            
        Returns:
            VectorResult with matching chunks
        """
        try:
            logger.info(f"Vector search: {query[:100]}...")
            
            # Use the retriever directly for more control
            retriever = self.index.as_retriever(similarity_top_k=self.top_k)
            nodes: List[NodeWithScore] = await retriever.aretrieve(query)
            
            chunks = []
            for node in nodes:
                chunk = {
                    "text": node.node.get_content(),
                    "score": node.score,
                    "metadata": node.node.metadata or {}
                }
                chunks.append(chunk)
            
            logger.info(f"Vector search returned {len(chunks)} results")
            
            return VectorResult(
                success=True,
                chunks=chunks
            )
            
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return VectorResult(
                success=False,
                chunks=[],
                error_message=str(e)
            )
    
    async def search_with_filter(
        self, 
        query: str, 
        source_table: Optional[str] = None
    ) -> VectorResult:
        """
        Perform semantic search with metadata filter.
        
        Args:
            query: Natural language search query
            source_table: Filter by source table (e.g., "proyectos", "leccionesaprendidas")
            
        Returns:
            VectorResult with matching chunks
        """
        try:
            # Get all results first
            result = await self.search(query)
            
            if not result.success:
                return result
            
            # Filter by source table if specified
            if source_table:
                filtered_chunks = [
                    chunk for chunk in result.chunks
                    if chunk.get("metadata", {}).get("fuentetabla") == source_table
                ]
                result.chunks = filtered_chunks
            
            return result
            
        except Exception as e:
            logger.error(f"Filtered vector search failed: {e}")
            return VectorResult(
                success=False,
                chunks=[],
                error_message=str(e)
            )
    
    def format_results_for_synthesis(self, result: VectorResult) -> str:
        """
        Format vector results for synthesis prompt.
        
        Returns a readable string representation of the search results.
        """
        if not result.success or not result.chunks:
            return "No se encontraron resultados relevantes."
        
        lines = []
        for i, chunk in enumerate(result.chunks, 1):
            metadata = chunk.get("metadata", {})
            source = metadata.get("fuentetabla", "documento")
            source_id = metadata.get("fuenteid", "N/A")
            
            lines.append(f"--- Resultado {i} (Fuente: {source}, ID: {source_id}) ---")
            lines.append(chunk["text"])
            lines.append("")
        
        return "\n".join(lines)
