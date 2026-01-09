"""
Query classifier for determining query intent.

Uses regex patterns for common cases and LLM fallback for complex queries.
"""
import re
import logging
from typing import Dict, Any, Optional

from llama_index.llms.openai import OpenAI

from models.schemas import QueryType, ClassificationResult
from prompts.templates import CLASSIFICATION_PROMPT

logger = logging.getLogger(__name__)


class QueryClassifier:
    """
    Classifies user queries into types for routing.
    
    Query types:
    - CONSULTANT_SEARCH: Questions about people/consultants
    - PROJECT_SEARCH: Questions about projects or assignments
    - CLIENT_SEARCH: Questions about clients/companies
    - KNOWLEDGE_SEARCH: Questions about lessons learned
    - HYBRID: Needs multiple sources
    """
    
    # Regex patterns for common query types
    PATTERNS = {
        QueryType.CONSULTANT_SEARCH: [
            r"(?:qu[ié](?:e)?n(?:es)?)\s+(?:es|son|trabaja)\s+(\w+)",  # quién es X
            r"(?:qu[ié](?:e)?n)\s+sabe\s+(?:de|sobre)\s+(.+)",  # quién sabe de X
            r"experto(?:s)?\s+en\s+(.+)",  # expertos en X
            r"consultor(?:es|a)?\s+(?:de|en)\s+(.+)",  # consultores de X
            r"(?:qu[ié](?:e)?n)\s+conoce\s+(.+)",  # quién conoce X
            r"(?:personas?|gente)\s+(?:que\s+)?(?:sabe(?:n)?|conoce(?:n)?)\s+(.+)",
            r"(?:busco|necesito)\s+(?:a\s+)?alguien\s+(?:que|con)\s+(.+)",
        ],
        QueryType.PROJECT_SEARCH: [
            r"proyectos?\s+(?:de|en\s+que\s+trabaja)\s+(\w+)",  # proyectos de X
            r"(?:en\s+)?qu[ée]\s+(?:proyectos?|trabaja)\s+(\w+)",  # en qué trabaja X
            r"(?:equipo|asignaciones?)\s+(?:de(?:l)?)\s+(?:proyecto\s+)?(.+)",  # equipo de proyecto X
            r"(?:qu[ié](?:e)?n(?:es)?)\s+trabaja(?:n)?\s+en\s+(?:el\s+)?(?:proyecto\s+)?(.+)",
        ],
        QueryType.CLIENT_SEARCH: [
            r"clientes?\s+(?:de|del?\s+rubro|en)\s+(.+)",  # clientes de industria
            r"empresas?\s+(?:del?\s+(?:rubro|sector)|en)\s+(.+)",  # empresas del rubro X
            r"(?:trabajamos|hicimos\s+algo)\s+con\s+(.+)",  # trabajamos con X
            r"(?:qu[ée])\s+(?:librer[ií]as?|bancos?|empresas?)\s+(.+)",  # qué librerías tenemos
            r"(?:industria|sector)\s+(.+)",
        ],
        QueryType.KNOWLEDGE_SEARCH: [
            r"lecciones?\s+(?:aprendidas?|de)\s+(.+)",  # lecciones aprendidas
            r"problemas?\s+(?:con|en|de)\s+(.+)",  # problemas con X
            r"(?:c[oó]mo)\s+(?:se\s+)?(?:solucion[oó]|resolvi[oó]|manej[oó])\s+(.+)",
            r"(?:qu[ée])\s+(?:hicimos|aprendimos)\s+(?:con|en|de)\s+(.+)",
            r"experiencias?\s+(?:con|en)\s+(.+)",
        ],
    }
    
    def __init__(self, llm: Optional[OpenAI] = None):
        """
        Initialize the classifier.
        
        Args:
            llm: Optional LLM for complex classification (fallback)
        """
        self.llm = llm
    
    def classify(
        self, 
        question: str, 
        context: Optional[Dict] = None
    ) -> ClassificationResult:
        """
        Classify a question into a query type.
        
        Uses regex patterns first, then LLM fallback if no match.
        
        Args:
            question: User's question
            context: Optional context from chat history
            
        Returns:
            ClassificationResult with type and extracted entities
        """
        question_lower = question.lower().strip()
        
        # Try regex patterns first
        for query_type, patterns in self.PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, question_lower, re.IGNORECASE)
                if match:
                    entities = self._extract_entities(match)
                    logger.info(f"Classified as {query_type.value} via pattern: {pattern}")
                    return ClassificationResult(
                        query_type=query_type,
                        entities=entities,
                        confidence=0.9
                    )
        
        # Default to SQL consultant search for common name patterns
        if self._looks_like_name_query(question):
            return ClassificationResult(
                query_type=QueryType.CONSULTANT_SEARCH,
                entities={"name": question},
                confidence=0.7
            )
        
        # Fallback to HYBRID for complex queries
        logger.info("No pattern match, defaulting to HYBRID")
        return ClassificationResult(
            query_type=QueryType.HYBRID,
            entities={"raw_question": question},
            confidence=0.5
        )
    
    async def classify_with_llm(self, question: str) -> ClassificationResult:
        """
        Use LLM to classify complex queries.
        
        More accurate but slower. Use when regex fails.
        """
        if not self.llm:
            return ClassificationResult(
                query_type=QueryType.UNKNOWN,
                confidence=0.0
            )
        
        try:
            prompt = CLASSIFICATION_PROMPT.format(question=question)
            response = await self.llm.acomplete(prompt)
            classification = str(response).strip().upper()
            
            # Map response to QueryType
            type_map = {
                "CONSULTANT_SEARCH": QueryType.CONSULTANT_SEARCH,
                "PROJECT_SEARCH": QueryType.PROJECT_SEARCH,
                "CLIENT_SEARCH": QueryType.CLIENT_SEARCH,
                "KNOWLEDGE_SEARCH": QueryType.KNOWLEDGE_SEARCH,
                "HYBRID": QueryType.HYBRID,
            }
            
            query_type = type_map.get(classification, QueryType.UNKNOWN)
            
            return ClassificationResult(
                query_type=query_type,
                entities={"raw_question": question},
                confidence=0.8
            )
            
        except Exception as e:
            logger.error(f"LLM classification failed: {e}")
            return ClassificationResult(
                query_type=QueryType.UNKNOWN,
                confidence=0.0
            )
    
    def _extract_entities(self, match: re.Match) -> Dict[str, Any]:
        """Extract entities from regex match."""
        entities = {}
        
        # Get named groups or positional groups
        if match.lastgroup:
            entities = match.groupdict()
        elif match.groups():
            # Use first group as main entity
            entities["value"] = match.group(1).strip() if match.group(1) else None
        
        return entities
    
    def _looks_like_name_query(self, question: str) -> bool:
        """
        Check if question looks like a simple name lookup.
        
        Examples:
        - "Constanza Boix"
        - "Juan?"
        - "Martin"
        """
        # Short questions with just a name
        words = question.strip().split()
        
        # 1-3 words, mostly capitalized, no question words
        if len(words) <= 3:
            question_words = {"que", "qué", "como", "cómo", "cuando", "cuándo", 
                           "donde", "dónde", "por", "para", "el", "la", "los", "las"}
            
            if not any(w.lower() in question_words for w in words):
                # Check if looks like a name (capitalized or contains @)
                if any(w[0].isupper() for w in words if w) or "@" in question:
                    return True
        
        return False
