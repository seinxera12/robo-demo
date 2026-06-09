import requests

with open("audio.webm", "rb") as f:
    r = requests.post('http://localhost:8001/api/stt',
        files={'file': ('audio.webm', f, 'audio/webm')})
    print(r.status_code, r.json())