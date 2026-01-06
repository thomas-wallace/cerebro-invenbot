import requests
import json

url = "http://127.0.0.1:8000/api/chat"
payload = {
    "question": "quien es el responsable de i+d?",
    "user_email": "twallace@invenzis.com",
    "user_name": "Thomas",
    "conversation_id": "test_conv_123"
}

try:
    response = requests.post(url, json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
