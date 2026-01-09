"""
Clean chat memory manager that filters out error messages
and never stores system prompts with user messages.

This solves the problem where:
1. SYSTEM_PROMPT was being saved with every user message
2. Error responses (containing SQL, technical details) were being saved
3. The LLM would see this corrupted history and produce bad responses
"""
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class CleanChatMemory:
    """
    Memory manager that:
    1. Never stores system prompts with user messages
    2. Only stores successful responses
    3. Filters technical content from history
    4. Limits history to prevent context overflow
    """
    
    # Error indicators that should never be saved
    ERROR_INDICATORS = [
        "error al intentar",
        "consulta sql",
        "```sql",
        "select ",
        "from consultores",
        "where ",
        "parece que hubo",
        "no encontré información",
        "hubo un problema",
        "sql debido",
        "intentar ejecutar"
    ]
    
    MAX_MESSAGES = 10  # Keep last N messages (5 exchanges)
    
    def __init__(self, chat_store):
        """
        Initialize with a PostgresChatStore instance.
        
        Args:
            chat_store: LlamaIndex PostgresChatStore or compatible store
        """
        self.chat_store = chat_store
    
    def save_exchange(
        self, 
        conversation_id: str,
        user_question: str,
        assistant_response: str,
        was_successful: bool
    ) -> bool:
        """
        Save a Q&A exchange to memory.
        
        Only saves if:
        - The query was successful
        - The response doesn't contain error indicators
        - The question doesn't contain system prompts
        
        Args:
            conversation_id: Unique conversation identifier
            user_question: The user's question (clean, without system prompt)
            assistant_response: The assistant's response
            was_successful: Whether the query execution was successful
            
        Returns:
            True if saved, False if skipped
        """
        # Don't save failed responses
        if not was_successful:
            logger.debug(f"Skipping save - query was not successful")
            return False
        
        # Don't save if question contains system prompt remnants
        if "SYSTEM INSTRUCTIONS" in user_question or "SYSTEM_PROMPT" in user_question:
            logger.warning(f"Skipping save - question contains system prompt")
            return False
        
        # Don't save responses that look like errors
        response_lower = assistant_response.lower()
        if any(indicator in response_lower for indicator in self.ERROR_INDICATORS):
            logger.debug(f"Skipping save - response contains error indicators")
            return False
        
        # Save clean exchange
        try:
            # Get existing messages
            messages = self._get_messages(conversation_id)
            
            # Add user message (clean, without system prompt)
            messages.append({
                "role": "user",
                "content": user_question.strip()
            })
            
            # Add assistant message
            messages.append({
                "role": "assistant", 
                "content": assistant_response.strip()
            })
            
            # Keep only last N messages to avoid context overflow
            if len(messages) > self.MAX_MESSAGES:
                messages = messages[-self.MAX_MESSAGES:]
            
            # Save back
            self._set_messages(conversation_id, messages)
            logger.info(f"Saved clean exchange to memory: {conversation_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save to memory: {e}")
            return False
    
    def get_context(self, conversation_id: str, max_exchanges: int = 3) -> str:
        """
        Get clean conversation context for the LLM.
        
        Returns formatted string of previous exchanges, 
        filtering out any that contain error indicators.
        
        Args:
            conversation_id: Unique conversation identifier
            max_exchanges: Maximum number of Q&A pairs to include
            
        Returns:
            Formatted string of conversation history
        """
        try:
            messages = self._get_messages(conversation_id)
            if not messages:
                return ""
            
            # Filter out messages with errors
            clean_messages = []
            for msg in messages:
                content = msg.get("content", "")
                content_lower = content.lower()
                
                # Skip messages with error indicators
                if any(indicator in content_lower for indicator in self.ERROR_INDICATORS):
                    continue
                
                clean_messages.append(msg)
            
            # Take last N exchanges (2*max_exchanges messages)
            recent = clean_messages[-(max_exchanges * 2):]
            
            context_lines = []
            for msg in recent:
                role = msg.get("role", "")
                content = msg.get("content", "")
                
                if role == "user":
                    context_lines.append(f"Usuario: {content}")
                elif role == "assistant":
                    # Truncate long responses
                    content_short = content[:300] + "..." if len(content) > 300 else content
                    context_lines.append(f"Asistente: {content_short}")
            
            return "\n".join(context_lines)
            
        except Exception as e:
            logger.error(f"Failed to get context: {e}")
            return ""
    
    def get_entities_from_history(self, conversation_id: str) -> Dict[str, Any]:
        """
        Extract mentioned entities from conversation history.
        
        Useful for resolving references like "él", "ese proyecto", etc.
        
        Returns:
            Dict with extracted entities (names, projects, etc.)
        """
        entities = {
            "mentioned_names": [],
            "mentioned_projects": [],
            "mentioned_clients": []
        }
        
        try:
            context = self.get_context(conversation_id)
            
            # Simple extraction - could be enhanced with NER
            # For now, just extract capitalized words that look like names
            import re
            
            # Find patterns like "Constanza Boix" or "Juan García"
            name_pattern = r'\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)\b'
            names = re.findall(name_pattern, context)
            entities["mentioned_names"] = list(set(names))[:5]
            
            return entities
            
        except Exception as e:
            logger.error(f"Failed to extract entities: {e}")
            return entities
    
    def clear(self, conversation_id: str) -> bool:
        """
        Clear conversation history.
        
        Args:
            conversation_id: Unique conversation identifier
            
        Returns:
            True if cleared successfully
        """
        try:
            self._set_messages(conversation_id, [])
            logger.info(f"Cleared memory for: {conversation_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear memory: {e}")
            return False
    
    def _get_messages(self, conversation_id: str) -> List[Dict]:
        """Get messages from chat store."""
        try:
            # LlamaIndex PostgresChatStore interface
            messages = self.chat_store.get_messages(conversation_id)
            if messages is None:
                return []
            
            # Convert ChatMessage objects to dicts if needed
            result = []
            for msg in messages:
                if hasattr(msg, 'role') and hasattr(msg, 'content'):
                    result.append({
                        "role": str(msg.role.value) if hasattr(msg.role, 'value') else str(msg.role),
                        "content": str(msg.content)
                    })
                elif isinstance(msg, dict):
                    result.append(msg)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get messages: {e}")
            return []
    
    def _set_messages(self, conversation_id: str, messages: List[Dict]):
        """Set messages in chat store."""
        try:
            # LlamaIndex PostgresChatStore interface
            from llama_index.core.llms import ChatMessage, MessageRole
            
            chat_messages = []
            for msg in messages:
                role_str = msg.get("role", "user")
                content = msg.get("content", "")
                
                if role_str == "user":
                    role = MessageRole.USER
                elif role_str == "assistant":
                    role = MessageRole.ASSISTANT
                else:
                    role = MessageRole.SYSTEM
                
                chat_messages.append(ChatMessage(role=role, content=content))
            
            self.chat_store.set_messages(conversation_id, chat_messages)
            
        except Exception as e:
            logger.error(f"Failed to set messages: {e}")
            raise
