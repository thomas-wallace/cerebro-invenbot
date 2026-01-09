"""
Comprehensive test script for Invenzis Intelligence Brain API.

Tests multiple query types to verify the refactored system works correctly.
Uses unique conversation IDs to avoid history contamination.
"""
import requests
import json
import uuid

BASE_URL = "http://127.0.0.1:8000"

# Test cases
TEST_CASES = [
    {
        "name": "Consultant Search - By Name",
        "question": "¿Quién es Constanza, la de uruguay?",
        "expected_type": "consultant_search"
    },
    {
        "name": "Project Search - Consultant Projects",
        "question": "¿En qué proyectos trabaja thomas wallace?",
        "expected_type": "project_search"
    },
    {
        "name": "Client Search - By Industry",
        "question": "¿Qué clientes tenemos en el sector retail?",
        "expected_type": "client_search"
    },
    {
        "name": "Expert Search - By Technology",
        "question": "¿Quién sabe de SAP FI?",
        "expected_type": "consultant_search"
    },
]

def run_test(test_case):
    """Run a single test case."""
    print(f"\n{'='*60}")
    print(f"TEST: {test_case['name']}")
    print(f"Question: {test_case['question']}")
    print(f"{'='*60}")
    
    # Use unique conversation_id each time to avoid history contamination
    unique_id = uuid.uuid4().hex[:8]
    
    payload = {
        "question": test_case["question"],
        "user_email": "test@invenzis.com",
        "user_name": "Test User",
        "conversation_id": f"test_{unique_id}"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=60)
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            query_type = data.get('query_type', 'N/A')
            answer = data.get('answer', 'N/A')
            sources = data.get('source_nodes', [])
            
            print(f"Query Type: {query_type}")
            print(f"Answer: {answer[:500]}..." if len(answer) > 500 else f"Answer: {answer}")
            print(f"Sources: {sources}")
            
            # Check for error indicators in response
            error_indicators = ["error", "sql", "select", "consulta"]
            has_errors = any(ind in answer.lower()[:100] for ind in error_indicators)
            
            if has_errors:
                print("⚠️  WARNING: Response may contain technical content")
                return False
            
            return True
        else:
            print(f"Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"Error: {e}")
        return False

def main():
    print("\n" + "="*60)
    print("INVENZIS INTELLIGENCE BRAIN - TEST SUITE")
    print("="*60)
    
    # Health check
    try:
        health = requests.get(f"{BASE_URL}/health")
        print(f"\nHealth Check: {health.json()}")
    except Exception as e:
        print(f"\nHealth Check Failed: {e}")
        return
    
    # Run tests
    results = []
    for test in TEST_CASES:
        success = run_test(test)
        results.append((test['name'], success))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {name}")
    
    passed = sum(1 for _, s in results if s)
    print(f"\nTotal: {passed}/{len(results)} tests passed")

if __name__ == "__main__":
    main()
