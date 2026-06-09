import requests
import json

BASE_URL = "http://localhost:8001"

def print_result(label, r):
    print(f"\n{'='*50}")
    print(f"TEST: {label}")
    print(f"Status: {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2))
    except Exception:
        print(f"(non-JSON response, {len(r.content)} bytes)")
    print('='*50)


# 1. Navigate — clarification turn (poi_not_found path)
r = requests.post(f"{BASE_URL}/api/navigate", json={
    "text": "Clarify: The user wants the blue section but no POIs match.",
    "language": "en",
    "session_id": "smoke-test-001",
    "building_context": {
        "current_node_label": "Main Lobby",
        "available_pois": ["Cafeteria", "Elevator Bank", "Conference Room A"],
        "floor_name": "Ground Floor",
        "poi_not_found": True,
        "query": "blue section"
    }
})
print_result("Navigate — poi_not_found / clarify", r)
data = r.json()
assert data.get("intent") == "clarify", f"Expected intent=clarify, got {data.get('intent')}"
assert data.get("destination_query") is None, f"Expected destination_query=null, got {data.get('destination_query')}"
print("PASS: intent=clarify, destination_query=null")


# 2. TTS — English
r = requests.post(f"{BASE_URL}/api/tts", json={
    "text": "Turn left at the elevator bank",
    "language": "en"
})
print_result("TTS — English", r)
assert r.status_code == 200, f"Expected 200, got {r.status_code}"
assert r.content[:4] == b"RIFF", f"Expected RIFF/WAV, got {r.content[:16]}"
with open("test_output.wav", "wb") as f:
    f.write(r.content)
print("PASS: got valid WAV, saved to test_output.wav")


# 3. TTS — Korean (must return 406)
r = requests.post(f"{BASE_URL}/api/tts", json={
    "text": "좌회전",
    "language": "ko"
})
print_result("TTS — Korean (expect 406)", r)
assert r.status_code == 406, f"Expected 406, got {r.status_code}"
print("PASS: 406 returned for unsupported language")


print("\n✓ All tests passed.")