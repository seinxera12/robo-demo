import requests
import json

payload = {
    "text": "find the cafeteria",
    "language": "en",
    "session_id": "smoke-test-001",
    "building_context": {
        "current_node_label": "Main Lobby",
        "available_pois": ["Cafeteria", "Elevator Bank", "Conference Room A"],
        "floor_name": "Ground Floor"
    }
}

r = requests.post(
    "http://localhost:8001/api/navigate",
    headers={"Content-Type": "application/json"},
    json=payload
)

print(r.status_code)
print(json.dumps(r.json(), indent=2))