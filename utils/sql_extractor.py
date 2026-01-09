"""
Robust SQL extraction from LLM responses.

This module handles the common problem where LLMs include explanatory text
along with SQL queries. It uses multiple fallback strategies to extract
only the valid SQL.
"""
import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class SQLExtractionError(Exception):
    """Raised when no valid SQL can be extracted from a response."""
    pass


def extract_sql(raw_response: str) -> str:
    """
    Extract SQL query from LLM response using multiple strategies.
    
    Strategies in order of priority:
    1. Find ```sql ... ``` markdown blocks
    2. Find ```...``` blocks containing SELECT
    3. Find SELECT ... ; pattern (semicolon terminated)
    4. Find SELECT to end of text
    5. If starts with SELECT, take all until problematic text
    
    Args:
        raw_response: The raw text from the LLM
        
    Returns:
        Cleaned SQL query string
        
    Raises:
        SQLExtractionError: If no valid SQL can be extracted
    """
    if not raw_response or not raw_response.strip():
        raise SQLExtractionError("Empty response from LLM")
    
    text = raw_response.strip()
    
    # Strategy 1: Extract from ```sql ... ``` blocks
    sql = _extract_from_sql_block(text)
    if sql:
        logger.debug("Extracted SQL using strategy 1 (```sql block)")
        return sql
    
    # Strategy 2: Extract from generic ``` ... ``` blocks
    sql = _extract_from_code_block(text)
    if sql:
        logger.debug("Extracted SQL using strategy 2 (``` block)")
        return sql
    
    # Strategy 3: Find SELECT ... ; pattern
    sql = _extract_select_with_semicolon(text)
    if sql:
        logger.debug("Extracted SQL using strategy 3 (SELECT...;)")
        return sql
    
    # Strategy 4: Find SELECT to end (for responses without semicolon)
    sql = _extract_select_to_end(text)
    if sql:
        logger.debug("Extracted SQL using strategy 4 (SELECT to end)")
        return sql
    
    # Strategy 5: If text starts with SELECT
    sql = _extract_if_starts_with_select(text)
    if sql:
        logger.debug("Extracted SQL using strategy 5 (starts with SELECT)")
        return sql
    
    raise SQLExtractionError(f"Could not extract SQL from response: {text[:200]}...")


def _extract_from_sql_block(text: str) -> Optional[str]:
    """Extract SQL from ```sql ... ``` markdown blocks."""
    pattern = r"```sql\s*(.*?)\s*```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        sql = match.group(1).strip()
        if _looks_like_sql(sql):
            return _clean_sql(sql)
    return None


def _extract_from_code_block(text: str) -> Optional[str]:
    """Extract SQL from generic ``` ... ``` blocks."""
    pattern = r"```\s*(.*?)\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        sql = match.strip()
        if _looks_like_sql(sql):
            return _clean_sql(sql)
    return None


def _extract_select_with_semicolon(text: str) -> Optional[str]:
    """Extract SELECT statement terminated by semicolon."""
    # Match SELECT ... ; allowing for nested parentheses
    pattern = r"(SELECT\s+.+?;)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        sql = match.group(1).strip()
        return _clean_sql(sql)
    return None


def _extract_select_to_end(text: str) -> Optional[str]:
    """Extract SELECT statement to end of text or until clear delimiter."""
    # Find SELECT and take until a clear stopping point
    pattern = r"(SELECT\s+.+?)(?:\n\n|\nEsta|\nAquí|\nLa consulta|\nEsto|$)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        sql = match.group(1).strip()
        # Remove trailing explanation text if present
        sql = _remove_trailing_text(sql)
        if _looks_like_sql(sql):
            return _clean_sql(sql)
    return None


def _extract_if_starts_with_select(text: str) -> Optional[str]:
    """Handle case where response starts directly with SELECT."""
    if text.upper().startswith("SELECT"):
        # Take until we hit explanatory text
        sql = _remove_trailing_text(text)
        if _looks_like_sql(sql):
            return _clean_sql(sql)
    return None


def _remove_trailing_text(sql: str) -> str:
    """Remove trailing explanatory text from SQL."""
    # Common patterns that indicate end of SQL
    stop_patterns = [
        r"\n\nEsta",
        r"\n\nAquí",
        r"\n\nLa consulta",
        r"\n\nEsto busca",
        r"\n\nExplicación",
        r"\n\n--",
        r"\nEsta consulta",
        r"\.\s*Esta",
    ]
    
    for pattern in stop_patterns:
        match = re.search(pattern, sql, re.IGNORECASE)
        if match:
            sql = sql[:match.start()]
    
    return sql.strip()


def _looks_like_sql(text: str) -> bool:
    """Check if text looks like valid SQL."""
    if not text:
        return False
    
    upper = text.upper().strip()
    
    # Must start with a valid SQL keyword
    valid_starts = ["SELECT", "WITH"]
    if not any(upper.startswith(start) for start in valid_starts):
        return False
    
    # Must contain FROM for SELECT queries
    if upper.startswith("SELECT") and "FROM" not in upper:
        return False
    
    return True


def _clean_sql(sql: str) -> str:
    """Clean up extracted SQL."""
    # Remove leading/trailing whitespace
    sql = sql.strip()
    
    # Remove trailing semicolons (we'll add if needed)
    sql = sql.rstrip(";").strip()
    
    # Ensure it ends with semicolon
    sql = sql + ";"
    
    # Normalize whitespace
    sql = re.sub(r"\s+", " ", sql)
    
    # But preserve newlines for readability if they were there
    # Actually, let's keep it compact for execution
    
    return sql


def validate_sql_safety(sql: str) -> Tuple[bool, str]:
    """
    Validate that SQL is safe to execute (read-only).
    
    Args:
        sql: The SQL query to validate
        
    Returns:
        Tuple of (is_safe, error_message)
    """
    upper = sql.upper()
    
    # Dangerous keywords that should never appear
    dangerous_keywords = [
        "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE",
        "TRUNCATE", "GRANT", "REVOKE", "EXEC", "EXECUTE",
        "INTO OUTFILE", "LOAD_FILE", "--", "/*"
    ]
    
    for keyword in dangerous_keywords:
        if keyword in upper:
            return False, f"SQL contains forbidden keyword: {keyword}"
    
    # Must be a SELECT or WITH query
    stripped = upper.strip()
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        return False, "Query must start with SELECT or WITH"
    
    return True, ""


def filter_forbidden_fields(sql: str, forbidden_fields: list) -> str:
    """
    Remove forbidden fields from SELECT clause if present.
    
    This is a safety measure to prevent accidental exposure of sensitive data.
    """
    for field in forbidden_fields:
        # Remove field from SELECT clause
        patterns = [
            rf",\s*{field}\s*(?=,|FROM)",  # field in middle
            rf"{field}\s*,",  # field at start
            rf",\s*{field}\s*(?=FROM)",  # field at end
            rf"\.\s*{field}\s*(?=,|FROM)",  # table.field
        ]
        for pattern in patterns:
            sql = re.sub(pattern, "", sql, flags=re.IGNORECASE)
    
    return sql
