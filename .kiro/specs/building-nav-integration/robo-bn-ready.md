# Robo-BN Integration — What Is Ready and How to Connect

**Audience:** building-nav backend/frontend developers and agentic coding agents  
**Robo-BN base URL (dev):** `http://localhost:8001` (set `ROBO_BN_URL=http://localhost:8001` in `backend/.env`)  
**Status:** All three REST endpoints implemented, tested, and running. 82/82 tests pass.

---

## 1. What Robo-BN Exposes

Five HTTP endpoints, all under `/api`. Three are called by building-nav; two are utility.

| Method | Path | Called by | Purpose |
|---|---|---|---|
| `POST` | `/api/stt` | building-nav backend | Audio bytes → `{text, language}` |
| `POST` | `/api/navigate` | building-nav backend | Text + context → structured intent |
| `POST` | `/api/tts` | building-nav backend | Text + language → WAV audio |
| `GET` | `/api/health` | building-nav backend | Readiness probe for circuit breaker |
| `POST` | `/api/detect-language` | building-nav backend | Text → language code (keyboard input path) |

The browser never calls any of these directly. All calls are server-to-server.

---

## 2. CORS Configuration

Robo-BN has CORS middleware configured. The allowed origin is read from the `BUILDING_NAV_ORIGIN` env var in Robo-BN's `.env`. Default value: `http://localhost:8001`.

**What building-nav must set in Robo-BN's `.env`:**

```ini
BUILDING_NAV_ORIGIN=http://<building-nav-backend-host>:<port>
# Example for dev: BUILDING_NAV_ORIGIN=http://localhost:8000
```

The middleware allows `GET`, `POST`, `OPTIONS` with all headers. No special request headers required from building-nav.

**What building-nav does NOT need to do:**
- No auth tokens for the prototype
- No special `Content-Type` or `Accept` headers beyond the standard ones

---

## 3. POST /api/stt — Audio Transcription

### Request

```
POST http://localhost:8001/api/stt
Content-Type: multipart/form-data

Field: file
  - value: raw audio bytes (decoded from the browser's base64 blob)
  - filename: "audio.wav"       ← any filename is fine, Robo-BN ignores it
  - Content-Type: "audio/wav"   ← any content-type is fine, Robo-BN ignores it
```

**Audio format:** Robo-BN detects the actual format from the first 4 bytes (magic-byte detection). Send the raw bytes as-is — do not re-encode. Supported formats: `webm`, `ogg`, `wav`, `mp3`, `flac`, and raw PCM16. The browser's `MediaRecorder` typically produces `audio/webm`; that works correctly even if you label it `audio/wav` in the multipart header.

**Python example (building-nav backend):**

```python
import httpx

async def call_robo_stt(audio_bytes: bytes) -> dict:
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.post(
            f"{ROBO_BN_URL}/api/stt",
            files={"file": ("audio.wav", audio_bytes, "audio/wav")},
        )
        resp.raise_for_status()
        return resp.json()  # {"text": "...", "language": "en"}
```

### Response (200 OK)

```json
{
  "text": "I want to go to the cafeteria",
  "language": "en"
}
```

| Field | Type | Notes |
|---|---|---|
| `text` | string | Transcribed text. Non-empty. Never null. |
| `language` | string | ISO 639-1. Always one of: `en`, `ja`, `ko`, `zh`. ISO 639-2 codes from Whisper (e.g. `eng`, `jpn`) are normalised automatically. |

### Error responses

| Status | When |
|---|---|
| `422` | File field missing or audio is 0 bytes |
| `502` | Groq API failed — `{"error": "stt_failed", "detail": "..."}` |

On 502, record a circuit-breaker failure and return degraded response to frontend.

---

## 4. POST /api/navigate — Navigation Intent

### Request

```
POST http://localhost:8001/api/navigate
Content-Type: application/json
```

**Normal turn (standard call):**

```json
{
  "text": "I want to find the cafeteria",
  "language": "en",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "building_context": {
    "current_node_label": "Main Lobby",
    "available_pois": ["Cafeteria", "Conference Room A", "Elevator Bank", "Stairwell A"],
    "floor_name": "Ground Floor"
  }
}
```

**Clarification turn (when your POI LIKE search returned 0 results):**

```json
{
  "text": "Clarify: The user wants to go to 'blue section', but no POIs match this query.",
  "language": "en",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "building_context": {
    "current_node_label": "Main Lobby",
    "available_pois": ["Cafeteria", "Conference Room A", "Elevator Bank"],
    "floor_name": "Ground Floor",
    "poi_not_found": true,
    "query": "blue section"
  }
}
```

When `poi_not_found: true` is set, Robo-BN bypasses the intent classifier entirely and generates a clarification response. `destination_query` will always be `null` in the response — this prevents the infinite loop.

**Field reference:**

| Field | Type | Required | Source |
|---|---|---|---|
| `text` | string | Yes | STT transcript or keyboard input |
| `language` | string | Yes | From STT response or session default |
| `session_id` | UUID string | Yes | `crypto.randomUUID()` once per browser session |
| `building_context.current_node_label` | string | Yes | Node label from your `nodes` table |
| `building_context.available_pois` | string[] | Yes | All POI names from your `pois` table |
| `building_context.floor_name` | string | Yes | Current floor name |
| `building_context.poi_not_found` | boolean | No (default `false`) | Set `true` on the second call when 0 POI matches |
| `building_context.query` | string | No (default `null`) | The failed query string — used in the clarification prompt |

**Python example:**

```python
async def call_robo_navigate(
    text: str,
    language: str,
    session_id: str,
    current_node_label: str,
    available_pois: list[str],
    floor_name: str,
    poi_not_found: bool = False,
    failed_query: str | None = None,
) -> dict:
    building_context = {
        "current_node_label": current_node_label,
        "available_pois": available_pois,
        "floor_name": floor_name,
    }
    if poi_not_found:
        building_context["poi_not_found"] = True
        building_context["query"] = failed_query or text

    async with httpx.AsyncClient(timeout=9.0) as client:
        resp = await client.post(
            f"{ROBO_BN_URL}/api/navigate",
            json={
                "text": text,
                "language": language,
                "session_id": session_id,
                "building_context": building_context,
            },
        )
        resp.raise_for_status()
        return resp.json()
```

### Response (200 OK)

```json
{
  "intent": "navigation",
  "destination_query": "cafeteria",
  "accessibility_flag": false,
  "response_text": "I found the Cafeteria on the ground floor. Shall I navigate you there?",
  "needs_clarification": false,
  "language": "en",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| Field | Type | Notes |
|---|---|---|
| `intent` | string | One of: `navigation`, `accessibility_request`, `general`, `out_of_scope`, `clarify` |
| `destination_query` | string \| null | Short noun phrase (1–4 words). Use directly in `WHERE LOWER(search_terms) LIKE '%<query>%'`. Null when intent is not `navigation`. |
| `accessibility_flag` | boolean | `true` if user expressed a mobility constraint. Set `session.accessibility_mode = true` and use `accessible_only=true` on all subsequent route calls. |
| `response_text` | string | Full text to display in chat bubble and pass to `/api/tts`. Always in the user's language. |
| `needs_clarification` | boolean | Hint from the LLM. Also check your own POI match count — 0 matches means you need to call again with `poi_not_found: true` regardless of this flag. |
| `language` | string | ISO 639-1 detected language. Store in session. |
| `session_id` | string | Echoed from the request. |

**Intent → action mapping for your `/chat` handler:**

```python
intent = nav_data["intent"]
destination_query = nav_data["destination_query"]
accessibility_flag = nav_data["accessibility_flag"]

# 1. Apply accessibility flag (sticky for session)
if accessibility_flag:
    session["accessibility_mode"] = True

# 2. POI resolution
candidates = []
if intent == "navigation" and destination_query:
    rows = await db.execute(
        "SELECT name, category, node_id FROM pois "
        "WHERE LOWER(search_terms) LIKE :q LIMIT 8",
        {"q": f"%{destination_query.lower()}%"},
    )
    candidates = [dict(r) for r in rows]

    if not candidates:
        # Second call with poi_not_found=True
        nav_data = await call_robo_navigate(
            text=text,
            ...,
            poi_not_found=True,
            failed_query=destination_query,
        )
        # nav_data["destination_query"] will be null — no third loop

# 3. Return to frontend
return {
    "response_text": nav_data["response_text"],
    "language": nav_data["language"],
    "candidates": candidates,
    "needs_confirmation": len(candidates) > 0,
    "accessibility_mode": session.get("accessibility_mode", False),
    "session_id": session_id,
    "chatbot_available": True,
}
```

### Error responses

| Status | When |
|---|---|
| `422` | Missing required field, empty `text`, or whitespace-only `text` |
| `502` | Intent classifier failed — `{"error": "classify_failed", "detail": "..."}` |
| `502` | LLM stream failed — `{"error": "llm_failed", "detail": "..."}` |

Both 502 variants should count as circuit-breaker failures.

---

## 5. POST /api/tts — Text-to-Speech

### Request

```
POST http://localhost:8001/api/tts
Content-Type: application/json

{
  "text": "Turn left at the elevator bank",
  "language": "en"
}
```

| Field | Type | Accepted values |
|---|---|---|
| `text` | string | Non-empty, non-whitespace |
| `language` | string | `en`, `ja`, `zh`, `ko` |

**Python example (your `/tts/instruction` proxy handler):**

```python
async def call_robo_tts(text: str, language: str) -> bytes | None:
    """Returns WAV bytes on success, None on 406 (Korean fallback)."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{ROBO_BN_URL}/api/tts",
            json={"text": text, "language": language},
        )
        if resp.status_code == 406:
            return None  # frontend will use browser speechSynthesis
        resp.raise_for_status()
        return resp.content  # raw WAV bytes
```

### Response (200 OK)

```
Content-Type: audio/wav
Body: binary WAV bytes
```

Proxy this response directly to the frontend with a `StreamingResponse`. Do not re-encode.

### Response (406) — Korean

```json
{
  "fallback": "browser_tts",
  "language": "ko"
}
```

Korean has no Kokoro voice. Proxy this 406 to the frontend as-is. `NavTTSPlayer` on the frontend will call `window.speechSynthesis` with `lang: "ko-KR"`.

### Error responses

| Status | When |
|---|---|
| `422` | Missing/empty `text` or unsupported `language` |
| `502` | Kokoro synthesis failed — `{"error": "tts_failed", "detail": "..."}` |

TTS failures are NOT counted by your circuit breaker. `NavTTSPlayer` handles TTS 5xx by falling back to browser speech synthesis independently.

---

## 6. GET /api/health — Readiness Probe

```
GET http://localhost:8001/api/health
```

**Response (200 OK):**

```json
{
  "status": "ok",
  "tts_ready": true,
  "stt_ready": true
}
```

| Field | Meaning |
|---|---|
| `tts_ready` | `true` after Kokoro TTS engines have warmed up (takes ~3–5s at startup) |
| `stt_ready` | `true` when Groq API key is present and STT backend is initialised |

Call this at building-nav startup to decide whether to show the chatbot as available. Also useful for your circuit breaker's recovery probe.

---

## 7. POST /api/detect-language — Text Language Detection

Used when the user submits text via keyboard (no STT involved) and you need to determine the language.

```
POST http://localhost:8001/api/detect-language
Content-Type: application/json

{"text": "エレベーターはどこですか"}
```

**Response (200 OK):**

```json
{"language": "ja"}
```

Returns one of: `en`, `ja`, `zh`, `ko`. Detection is based on Unicode character ranges. Stateless.

---

## 8. Network and Routing Setup

### Building-nav backend `.env`

```ini
ROBO_BN_URL=http://localhost:8001   # dev
# ROBO_BN_URL=http://192.168.1.50:8001  # LAN testing
```

### Robo-BN `.env`

```ini
SERVER_PORT=8001                          # port Robo-BN listens on
BUILDING_NAV_ORIGIN=http://localhost:8000 # your backend's origin for CORS
GROQ_API_KEY=<your-key>                   # required
```

### Starting Robo-BN for integration testing

```bash
# From the Robo-BN project root
venv/Scripts/python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 8001

# Or with auto-reload during development
venv/Scripts/python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 8001 --reload
```

Wait for this log line before sending traffic:
```
INFO  server.pipeline  — SERVER ready  port=8001
```

TTS warm-up runs concurrently and takes 3–5 seconds. The `/api/health` endpoint returns `tts_ready: false` during warm-up and `true` once complete.

### Ports summary

| Service | Port | What listens there |
|---|---|---|
| building-nav backend | 8000 | FastAPI — your `/chat`, `/tts/instruction`, `/route`, `/pois` |
| Robo-BN | 8001 | FastAPI — `/api/stt`, `/api/navigate`, `/api/tts`, `/api/health` |
| building-nav frontend (Vite dev) | 5173 | React PWA — proxies `/chat` and `/tts` to port 8000 |

---

## 9. Session Management — What You Must Track

Robo-BN maintains conversation history keyed by `session_id`. You need to maintain a separate navigation session on your side.

**Your session state (in-memory dict, 30-min TTL):**

```python
sessions: dict[str, dict] = {}
# Per session_id:
# {
#   "accessibility_mode": False,  # set True when accessibility_flag received
#   "chat_language": "en",        # updated from each navigate response
#   "last_accessed": datetime,
# }
```

**What Robo-BN tracks (you don't need to replicate this):**
- Full conversation message history (for LLM multi-turn context)
- Last active timestamp per session

Both sides use the same `session_id` UUID. It is generated once by the frontend: `crypto.randomUUID()` on app load, stored in Zustand, sent with every `/chat` request.

---

## 10. Circuit Breaker — What Triggers It

Your circuit breaker should count failures on `/api/stt` and `/api/navigate`. Open after 3 consecutive failures. Close after 60s recovery probe succeeds.

```python
class CircuitBreaker:
    failure_threshold = 3
    recovery_timeout = 60.0   # seconds

    def record_failure(self): ...
    def is_open(self) -> bool: ...
    def attempt_reset(self): ...
```

When open:
- Skip Robo-BN calls entirely
- Return `{"chatbot_available": false}` to the frontend immediately
- Frontend shows amber degradation banner, disables mic

**What does NOT count as a circuit-breaker failure:**
- `/api/tts` 406 (Korean — expected)
- `/api/tts` 5xx (has its own independent fallback in `NavTTSPlayer`)
- `/api/health` timeouts (health is a probe, not a user-facing call)

---

## 11. End-to-End Orchestration — Your `/chat` Handler

This is the complete logic for your `/chat` endpoint:

```python
@router.post("/chat")
async def chat(body: ChatRequest, session_id: str) -> ChatResponse:
    # Circuit breaker check
    if circuit_breaker.is_open():
        return ChatResponse(chatbot_available=False, ...)

    # Resolve building context
    current_node = await get_node(body.current_node_id)
    all_pois = await get_all_poi_names()   # cache this, refresh every 10 min
    floor_name = current_node.floor_name

    # Step 1: STT (if audio provided)
    text = body.text
    language = body.language or session.get("chat_language", "en")
    if body.audio_b64:
        try:
            audio_bytes = base64.b64decode(body.audio_b64)
            stt_result = await call_robo_stt(audio_bytes)
            text = stt_result["text"]
            language = stt_result["language"]
        except Exception:
            circuit_breaker.record_failure()
            return ChatResponse(chatbot_available=False, ...)

    # Step 2: Navigate
    try:
        nav_data = await call_robo_navigate(
            text=text,
            language=language,
            session_id=session_id,
            current_node_label=current_node.label,
            available_pois=all_pois,
            floor_name=floor_name,
        )
        circuit_breaker.record_success()
    except Exception:
        circuit_breaker.record_failure()
        return ChatResponse(chatbot_available=False, text_extracted=text, ...)

    # Step 3: Update session
    session["chat_language"] = nav_data["language"]
    if nav_data["accessibility_flag"]:
        session["accessibility_mode"] = True

    # Step 4: POI resolution
    candidates = []
    if nav_data["intent"] == "navigation" and nav_data["destination_query"]:
        candidates = await resolve_pois(nav_data["destination_query"])
        if not candidates:
            # Clarification call — will not loop because destination_query will be null
            nav_data = await call_robo_navigate(
                text=text,
                language=language,
                session_id=session_id,
                current_node_label=current_node.label,
                available_pois=all_pois,
                floor_name=floor_name,
                poi_not_found=True,
                failed_query=nav_data["destination_query"],
            )

    return ChatResponse(
        response_text=nav_data["response_text"],
        language=nav_data["language"],
        candidates=candidates,
        needs_confirmation=len(candidates) > 0,
        accessibility_mode=session.get("accessibility_mode", False),
        session_id=session_id,
        chatbot_available=True,
    )
```

---

## 12. TTS Proxy — Your `/tts/instruction` Handler

```python
@router.post("/tts/instruction")
async def tts_instruction(body: TTSRequest):
    resp = await httpx.AsyncClient().post(
        f"{ROBO_BN_URL}/api/tts",
        json={"text": body.text, "language": body.language},
        timeout=5.0,
    )
    if resp.status_code == 406:
        # Korean fallback — pass 406 through to frontend
        return JSONResponse(resp.json(), status_code=406)
    if resp.status_code >= 500:
        # TTS failure — pass error through; NavTTSPlayer falls back to browser TTS
        raise HTTPException(502, detail=resp.text)
    # Proxy WAV bytes directly
    return Response(content=resp.content, media_type="audio/wav")
```

---

## 13. What Does NOT Need to Be Built on the Building-Nav Side

| Capability | Why you skip it |
|---|---|
| Intent classification | Robo-BN handles it — you receive `intent` in the response |
| Language detection | Robo-BN normalises Whisper output to ISO 639-1 |
| STT | Robo-BN handles it — you just forward the audio bytes |
| TTS synthesis | Robo-BN handles it — you just proxy the WAV |
| Session conversation history | Robo-BN maintains it per `session_id` |
| Clarification LLM prompt | Robo-BN has a dedicated `run_clarification_turn()` path |
| POI hallucination prevention | Robo-BN injects `available_pois` into the LLM prompt |

What you DO build:
- `/chat` orchestration handler (as shown above)
- `/tts/instruction` proxy
- POI LIKE resolution against your DB
- Accessibility mode flag application to `/route` calls
- Circuit breaker around Robo-BN calls
- Navigation session state (`accessibility_mode`, `chat_language`)

---

## 14. Verification Smoke Tests

Run these against a live Robo-BN instance before running integration tests:

```bash
ROBO=http://localhost:8001

# Health
curl $ROBO/api/health
# Expect: {"status":"ok","tts_ready":true,"stt_ready":true}

# Navigate — normal turn
curl -s -X POST $ROBO/api/navigate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "find the cafeteria",
    "language": "en",
    "session_id": "smoke-test-001",
    "building_context": {
      "current_node_label": "Main Lobby",
      "available_pois": ["Cafeteria", "Elevator Bank", "Conference Room A"],
      "floor_name": "Ground Floor"
    }
  }'
# Expect: {"intent":"navigation","destination_query":"cafeteria",...}

# Navigate — clarification turn
curl -s -X POST $ROBO/api/navigate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Clarify: no POIs match blue section",
    "language": "en",
    "session_id": "smoke-test-001",
    "building_context": {
      "current_node_label": "Main Lobby",
      "available_pois": ["Cafeteria", "Elevator Bank"],
      "floor_name": "Ground Floor",
      "poi_not_found": true,
      "query": "blue section"
    }
  }'
# Expect: {"intent":"clarify","destination_query":null,"needs_clarification":true,...}

# TTS English
curl -s -X POST $ROBO/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Turn left at the elevator bank", "language": "en"}' \
  --output /tmp/test.wav
file /tmp/test.wav
# Expect: RIFF ... WAVE audio

# TTS Korean — must be 406
curl -o /dev/null -w "%{http_code}\n" -X POST $ROBO/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "좌회전", "language": "ko"}'
# Expect: 406
```

---

## 15. Known Constraints and Gotchas

**Audio format:** building-nav sends `audio/webm` bytes labelled as `audio/wav` in the multipart header. This is fine — Robo-BN ignores the content-type header and detects the format from magic bytes. Do not re-encode.

**destination_query is raw SQL input:** The value is used directly in `LIKE '%...%'`. It is a short noun phrase (1–4 words). The classifier prompt enforces this. Do not run NLP on it again.

**No destination_query on clarification response:** When `poi_not_found: true` is set, the response will always have `destination_query: null`. This is by design — do not run a POI search on a clarification response.

**Session TTL:** Robo-BN expires sessions after 30 minutes of inactivity. Your navigation session should use the same TTL. Using a stale `session_id` creates a fresh session on Robo-BN with empty history — this is graceful, not an error.

**Startup warm-up:** Kokoro TTS takes 3–5 seconds to warm up. `tts_ready` in `/api/health` will be `false` until then. TTS calls during warm-up may fail with 502. Check health before routing the first TTS instruction.

**Language normalisation:** Robo-BN guarantees the `language` field in both `/api/stt` and `/api/navigate` responses is one of `en`, `ja`, `ko`, `zh`. You do not need to handle ISO 639-2 codes.
