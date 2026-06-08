# Robo-BN Integration Plan
### Implementation guide for integrating Robo-BN with Building-Nav

**Source:** `guides/integration/robo-bn-integration.md`  
**Target project:** `robo-bn` (branched from `robo-demo`)  
**Purpose:** Define exactly what Robo-BN must implement to satisfy the building-nav integration contract — extracted from the architect's guide as an ordered, actionable plan.

---

## Overview

Robo-BN acts as the AI services backend for a building navigation system. It receives calls exclusively from the **building-nav backend** (never from the browser) and must expose three HTTP endpoints. The browser ↔ building-nav backend ↔ Robo-BN chain is fixed — do not alter the topology.

```
Browser → building-nav backend (port 8000) → Robo-BN (port 8001)
```

---

## System Boundaries — What Robo-BN Owns vs. Does Not Own

### Robo-BN MUST own and implement

| Responsibility | Notes |
|---|---|
| `POST /api/stt` | Audio bytes → `{text, language}` |
| `POST /api/navigate` | Text + context → structured intent response |
| `POST /api/tts` | Text + language → WAV audio bytes |
| STT via Groq Whisper (or equivalent) | Must be multilingual |
| Language detection + ISO 639-1 normalisation | Whisper may return ISO 639-2; normalise to 639-1 |
| Intent classification | 5 intent types (see §5) |
| Entity extraction | `destination_query` (short noun phrase), `accessibility_flag` |
| Conversational LLM response generation | `response_text` in the user's detected language |
| TTS synthesis | Kokoro for EN/JA/ZH; return HTTP 406 for KO |
| In-memory conversation session history | Keyed by `session_id`, 30-min expiry, no disk persistence |

### Robo-BN must NOT build

| Capability | Why |
|---|---|
| POI database or fuzzy matching | Belongs to building-nav backend; Robo-BN gets the POI list via `/pois` at startup |
| Navigation graph / Dijkstra routing | Already implemented in building-nav |
| User's physical location or current floor | Injected fresh on every request via `building_context` |
| Navigation state machine | Owned by the frontend (Zustand) |
| Any persistent storage of user data | Hard privacy requirement (FR-017) |
| QR code resolution | Database lookup, not an NLU task |
| Accessibility routing logic | Robo-BN sets the flag; building-nav applies it to routing |
| Circuit breaker logic | Already implemented in building-nav |
| Multi-floor or multi-building modelling | Out of scope; system has one floor |

---

## Two Pipelines to Serve

Both pipelines run on the same Robo-BN server. They must be **independently operable** — if the LLM is unavailable, TTS must still work.

### Pipeline A — Conversational Destination Resolution

- **Trigger:** User speaks or types in the ChatbotPanel
- **LLM involved:** Yes
- **Stateful:** Yes (uses session conversation history)
- **Hard latency budget:** < 8 seconds end-to-end (browser hard-timeout is 10s; subtract ~2s for network)
- **Endpoints used:** `/api/stt` (if audio input) → `/api/navigate`

Flow:
```
Frontend audio/text
  → building-nav POST /chat
    → Robo-BN POST /api/stt         (audio input only)
    → Robo-BN POST /api/navigate    (always)
  → building-nav resolves POIs, returns candidates to frontend
  → user confirms destination → routing begins
```

### Pipeline B — Navigation Instruction TTS

- **Trigger:** Navigation state transitions in Zustand (`NavTTSPlayer`)
- **LLM involved:** No
- **Stateful:** No — completely stateless
- **Preferred latency budget:** < 3 seconds
- **Endpoint used:** `/api/tts` only

Flow:
```
Zustand state change (ANCHORED / step advance / REROUTING / ARRIVED)
  → building-nav POST /tts/instruction
    → Robo-BN POST /api/tts → WAV bytes
  → frontend plays audio
```

---

## The Three HTTP Endpoints — Full Contracts

### 1. `POST /api/stt`

**Transport:** `multipart/form-data`  
**Called when:** `audio_b64` is present in the `/chat` request  
**Timeout budget:** ~5 seconds (part of the 10s total)

**Request:**
```
Content-Type: multipart/form-data
Field name: file
  - bytes: raw audio
  - filename: "audio.wav"
  - Content-Type: "audio/wav"
```

> **Important:** The audio originates from the browser's `MediaRecorder` API, which encodes as `audio/webm`. The building-nav backend decodes the base64 payload and sends raw bytes. Robo-BN's STT layer **must accept `audio/webm`** input, not only WAV.

**Response (200 OK):**
```json
{
  "text": "I want to go to the cafeteria",
  "language": "en"
}
```

| Field | Type | Constraint |
|---|---|---|
| `text` | string | Non-empty transcribed text |
| `language` | string | ISO 639-1 code: `en`, `ja`, `zh`, `ko` |

**On failure:** Return a non-200 HTTP status. Do not return an empty string inside a 200 body — the building-nav circuit breaker only registers failure on non-200 responses.

---

### 2. `POST /api/navigate`

**Transport:** JSON  
**Called:** On every `/chat` request, after STT if applicable  
**Stateful:** Yes — uses and updates in-memory session history

#### Request Schema

```json
{
  "text": "I'm in a wheelchair, I need to find the elevator",
  "language": "en",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "building_context": {
    "current_node_label": "Main Lobby",
    "available_pois": ["Cafeteria", "Conference Room A", "Elevator Bank"],
    "floor_name": "Ground Floor"
  }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `text` | string | Yes | STT transcript or direct user text |
| `language` | string | Yes | ISO 639-1 |
| `session_id` | UUID string | Yes | Same UUID for all turns in a browser session |
| `building_context.current_node_label` | string | Yes | Human-readable label of current map node |
| `building_context.available_pois` | string[] | Yes | All POI names from building-nav DB — inject into LLM prompt |
| `building_context.floor_name` | string | Yes | Current floor name |

**Clarification variant** (second call when 0 POIs matched):

```json
{
  "text": "Clarify: The user wants to go to 'blue section', but no POIs match this query.",
  "language": "en",
  "session_id": "...",
  "building_context": {
    "current_node_label": "Main Lobby",
    "available_pois": [...],
    "floor_name": "Ground Floor",
    "poi_not_found": true,
    "query": "blue section"
  }
}
```

When `poi_not_found: true` is present:
- Generate a clarification response offering closest POI names from `available_pois`
- **Do NOT return a `destination_query`** — this would cause an infinite loop
- Return `intent: "clarify"` and `needs_clarification: true`

#### Response Schema

```json
{
  "intent": "navigation",
  "destination_query": "elevator",
  "accessibility_flag": true,
  "response_text": "I've enabled accessible routes for you. I found the Elevator Bank nearby. Shall I navigate you there?",
  "needs_clarification": false,
  "language": "en",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `intent` | string | Yes | One of the 5 intent values (see below) |
| `destination_query` | string \| null | Yes | Short noun phrase for LIKE search. Null if intent ≠ navigation |
| `accessibility_flag` | boolean | Yes | True if user expressed a mobility/accessibility constraint |
| `response_text` | string | Yes | Full human-readable reply. Must be in the user's detected language |
| `needs_clarification` | boolean | Yes | True if LLM needs more info |
| `language` | string | Yes | ISO 639-1 of the detected language |
| `session_id` | string | Yes | Echo back the received session_id |

#### Intent Values

| Intent | When to use | `destination_query` | `accessibility_flag` |
|---|---|---|---|
| `navigation` | User wants to go somewhere | Required — short noun phrase | May be true |
| `accessibility_request` | User expressed mobility constraint, no destination yet | null | Always true |
| `general` | Small talk, building info, non-navigation | null | false |
| `out_of_scope` | Completely unrelated (weather, sports, etc.) | null | false |
| `clarify` | LLM needs more info to determine destination | null | false |

**Compound intent rule:** If a single utterance contains both a mobility constraint and a destination (e.g. "I'm in a wheelchair and I need the elevator"), return `intent: "navigation"`, a non-null `destination_query`, and `accessibility_flag: true`. Do not split this across two turns.

#### Critical constraint on `destination_query`

Building-nav runs the query directly as:
```sql
WHERE LOWER(search_terms) LIKE '%<destination_query>%'
```

The value must be a **short noun phrase** — never a full sentence.
- ✅ `"cafeteria"`, `"elevator"`, `"conference room a"`
- ❌ `"I want to go to the cafeteria on the ground floor"`

---

### 3. `POST /api/tts`

**Transport:** JSON request → binary WAV response  
**Stateless:** No session, no LLM

**Request:**
```json
{
  "text": "Turn left at the elevator bank",
  "language": "en"
}
```

| Field | Type | Notes |
|---|---|---|
| `text` | string | Plain text; no SSML needed |
| `language` | string | ISO 639-1 — `en`, `ja`, `zh` supported; `ko` returns 406 |

**Response (200 OK):**
```
Content-Type: audio/wav
Body: WAV audio bytes (binary)
```

Building-nav proxies this binary response directly to the frontend via `StreamingResponse`.

**Response (406) — Korean:**
```json
{
  "fallback": "browser_tts",
  "language": "ko"
}
```

Korean (`ko`) has no Kokoro voice. Return `406` and this JSON body. The frontend's `NavTTSPlayer` catches the 406 and falls back to `window.speechSynthesis` with `lang: "ko-KR"`. 

**Rules:**
- 406 is the **only** case for unsupported language. All other languages must return 200 + WAV, or 5xx on genuine failure.
- On 5xx, `NavTTSPlayer` falls back to browser speech synthesis for all languages. TTS failure never blocks navigation.
- `/api/tts` failures are **not counted** by the building-nav circuit breaker — it has its own independent fallback path.

---

## Session Management

### How sessions are keyed

Building-nav generates a `crypto.randomUUID()` once when the React app loads. It is stored in Zustand as `chatbot.sessionId`. The same UUID is used for all turns of a conversation. It is cleared on app close (not in localStorage).

### What Robo-BN must maintain

An in-memory conversation history dict keyed by `session_id`:

```python
app.state.navigate_sessions: dict[str, list[dict]] = {}
# Each entry: {"role": "user" | "assistant", "text": str}
```

- Inject the last N turns of history into the LLM context on each `/api/navigate` call
- This enables coreference resolution across turns (e.g. "the first one" → "Cafeteria")
- **Expiry:** 30 minutes of inactivity — no persistence to disk (required by FR-017)

### What Robo-BN does NOT store in sessions

- User's current navigation state (floor, node, route) — injected fresh via `building_context` on every call
- Any user-identifying information

---

## Language Support

### Supported languages

| Code | Language | TTS behaviour |
|---|---|---|
| `en` | English | Kokoro — full support |
| `ja` | 日本語 | Kokoro — full support |
| `zh` | 中文 | Kokoro ZH — must be built/configured |
| `ko` | 한국어 | Return HTTP 406 → browser speechSynthesis fallback |

### Language detection logic

1. **Audio input:** Use Groq Whisper's `verbose_json` `language` field as the detected language.
2. **Text input:** Use the language stored in the session, or the language override in `building_context`.
3. **Normalisation:** Whisper may return ISO 639-2 (e.g. `eng`). Always normalise to ISO 639-1 (`en`) before returning.

### `response_text` language rule

The LLM **must generate `response_text` in the same language the user spoke**. Building-nav passes this text directly to TTS and displays it in the chat bubble — there is no translation layer.

---

## Degradation Contract

Robo-BN must cooperate with building-nav's circuit breaker by using correct HTTP status codes.

| Rule | Detail |
|---|---|
| Use proper 4xx/5xx on failure | Do NOT return 200 with an error body — this fools the circuit breaker |
| Use 503 on genuine timeout | Not 200, not 500 |
| `/api/tts` is not circuit-breaker monitored | It has an independent fallback path in `NavTTSPlayer` |

### The four degradation levels (for context)

| Level | Condition | User experience |
|---|---|---|
| 0 — Full | Robo-BN fully operational | Voice + text chat, TTS instructions |
| 1 — TTS down | `/api/tts` failing, LLM OK | Chat works, nav instructions text-only |
| 2 — LLM down, STT OK | `/api/navigate` failing | STT text auto-populates search bar |
| 3 — Robo-BN unreachable | All calls failing | Amber banner, mic disabled, manual text search |

Circuit breaker behaviour (owned by building-nav, Robo-BN just needs to behave correctly):
- Opens after 3 consecutive failures on `/api/stt` or `/api/navigate`
- Stays open for 60 seconds
- Half-opens to probe recovery; closes on success

---

## POI Cache — Startup Requirement

Robo-BN must call building-nav's `GET /pois` at startup and cache the result:

```
GET http://<building-nav-backend>/pois
Returns: [{id, name, category, search_terms, node_id, node_accessible, floor_name, floor_num}, ...]
```

- Cache the `name` values as the `available_pois` list
- Refresh every 10 minutes via a background task
- Inject this list into the LLM prompt on every `/api/navigate` call so the model matches user intent against real POI names rather than hallucinating

---

## Navigation State Machine — Context for LLM Prompts

Robo-BN never sees the navigation status directly. However, understanding the state machine helps design accurate LLM system prompts.

```
UNLOCATED ──── QR scan / manual select ──→ ANCHORED
                                                │
                                          user selects destination
                                                │
                                                ▼
                                         ROUTE_PREVIEW
                                                │
                                            begin navigation
                                                │
                                                ▼
                                         NAVIGATING ◄──── reroute ───┐
                                                │                     │
                                          advance step          off-route QR
                                                │                     │
                                          final step           REROUTING ──┘
                                                │
                                                ▼
                                           ARRIVED ──── reset ──→ ANCHORED
```

**What triggers which pipeline:**

| Event | Pipeline | Robo-BN endpoint |
|---|---|---|
| User opens ChatbotPanel, types/speaks | A | `/api/stt` (if audio) + `/api/navigate` |
| QR scan → ANCHORED | B | `/api/tts` — "You are located at X" |
| Step advances (NAVIGATING) | B | `/api/tts` — instruction text |
| Off-route → REROUTING | B | `/api/tts` — "Recalculating route" |
| ARRIVED | B | `/api/tts` — "You have arrived at X" |

---

## Privacy Requirements (FR-017) — Hard Constraints

**What Robo-BN must NEVER write to any log file or persistent storage:**
- Transcribed speech text
- Conversation message content
- User's navigation destination
- User's language
- Any mapping between `session_id` and a real person, device, or IP address

**What is acceptable:**
- In-memory session history (no disk write; clears on server restart and on 30-min expiry)
- Aggregate performance metrics (latency, intent counts) with no user identifiers

**Pre-demo audit requirement:** Grep all Robo-BN log statements for `text`, `transcript`, `speech`, `message`, `session_id`. Remove any log line that writes request body content to a file.

---

## Network Configuration

### Development (localhost)

```
ROBO_BN_URL=http://localhost:8001
```

Building-nav reads this from `backend/.env`. All traffic is localhost, no auth needed.

### LAN / integration testing

```
ROBO_BN_URL=http://192.168.x.x:8001
```

Robo-BN must add CORS middleware to restrict callers:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://<building-nav-backend-ip>:8000"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
```

### Auth (prototype vs. production)

- **Prototype:** No auth required.
- **Production (future):** Implement API key authentication via `X-API-Key` header, shared as a secret between building-nav backend and Robo-BN.

---

## End-to-End Scenarios — Expected Behaviour

### Scenario A: Single-match voice navigation (happy path)

1. User says "I want to go to the cafeteria"
2. `/api/stt` returns `{text: "I want to go to the cafeteria", language: "en"}`
3. `/api/navigate` returns `{intent: "navigation", destination_query: "cafeteria", accessibility_flag: false, response_text: "I found the Cafeteria... Shall I navigate you there?", ...}`
4. Building-nav resolves `LIKE '%cafeteria%'` → 1 candidate
5. Frontend shows confirmation card; user confirms; routing begins
6. Each step advance triggers `/api/tts` → WAV plays

### Scenario B: Compound accessibility + navigation

1. User says "I'm in a wheelchair and I need the elevator"
2. `/api/navigate` must return:
   - `intent: "navigation"`
   - `destination_query: "elevator"`
   - `accessibility_flag: true`
   - Both signals captured in a single turn — do not require two turns
3. Building-nav sets `accessibility_mode: true` for the session (sticky)
4. All subsequent `/route` calls include `&accessible_only=true`

### Scenario C: Zero POI match → clarification loop

1. User asks for "blue section"
2. First `/api/navigate` → `destination_query: "blue section"`
3. Building-nav LIKE search → 0 results
4. Building-nav calls `/api/navigate` again with `poi_not_found: true`
5. Second Robo-BN response **must**:
   - Set `intent: "clarify"`
   - Set `destination_query: null` (do not provide a new query — would loop)
   - Set `needs_clarification: true`
   - Include `response_text` listing closest matches from `available_pois`

### Scenario D: Full degradation

1. Robo-BN unreachable → building-nav records 3 failures → circuit opens
2. Building-nav returns `{chatbot_available: false}`
3. Frontend shows amber banner; mic disabled
4. Text search and manual navigation still work without Robo-BN

---

## Verification Checklist

### Endpoint smoke tests

```bash
# STT — accepts audio/webm, returns {text, language}
curl -X POST http://localhost:8001/api/stt \
  -F "file=@test_audio.webm;type=audio/webm"
# Expected: {"text": "...", "language": "en"}

# Navigate — returns all required fields
curl -X POST http://localhost:8001/api/navigate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "find the cafeteria",
    "language": "en",
    "session_id": "test-session-001",
    "building_context": {
      "current_node_label": "Main Lobby",
      "available_pois": ["Cafeteria", "Elevator Bank"],
      "floor_name": "Ground Floor"
    }
  }'
# Expected: {intent, destination_query, accessibility_flag, response_text,
#            needs_clarification, language, session_id}
# Check: destination_query is a short noun phrase, not a sentence

# TTS English — returns WAV bytes
curl -X POST http://localhost:8001/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Turn left at the elevator bank", "language": "en"}' \
  --output test_audio.wav

# TTS Korean — must return 406
curl -o /dev/null -w "%{http_code}" -X POST http://localhost:8001/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "좌회전", "language": "ko"}'
# Expected: 406
```

### Integration flow tests

- [ ] Voice → destination resolution (Scenario A end-to-end)
- [ ] `accessibility_flag: true` → subsequent `/route` calls include `accessible_only=true`
- [ ] Zero POI match → clarification response with `destination_query: null`
- [ ] Second turn in same session correctly resolves references to first turn ("the first one")
- [ ] TTS plays on QR scan (ANCHORED transition)
- [ ] TTS plays on each navigation step advance
- [ ] Korean TTS → HTTP 406 → browser `speechSynthesis` fires (check browser console)
- [ ] Shut down Robo-BN → amber banner appears after 3 failures
- [ ] Restart Robo-BN → banner clears after next successful call
- [ ] Manual search still works when `chatbot.isAvailable = false`
- [ ] Robo-BN completes STT + LLM within 8 seconds (10s browser timeout − 2s network)

### Privacy audit

- [ ] Grep Robo-BN log output for: `transcript`, `text=`, `speech`, `message_content`, `session_id`
  - None of these should appear in file-backed logs
- [ ] In-memory session dict is not serialised to disk on shutdown
- [ ] No request body fields (`audio_b64`, `text`, `response_text`) are written to any log file

---

## Reference — Building-Nav Source Files (Do Not Duplicate)

| File | What it contains |
|---|---|
| `backend/routes/chat.py` | `/chat` and `/tts/instruction` — exact code that calls Robo-BN |
| `backend/routes/routing.py` | `/route` with `accessible_only` parameter |
| `backend/routes/map.py` | `/pois` endpoint — Robo-BN startup cache source |
| `backend/graph.py` | `NavGraph.shortest_path(accessible_only=)` |
| `frontend/src/components/ChatbotPanel.jsx` | Audio capture, chat UI, candidate confirmation |
| `frontend/src/components/NavTTSPlayer.jsx` | TTS trigger logic, WAV playback, fallback to browser speech |
| `frontend/src/store/useNavStore.js` | `chatbot` state shape, `accessibilityMode` wiring |
| `frontend/src/api/index.js` | `sendChatRequest` with 10s timeout |
