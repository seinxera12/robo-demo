# Robo-BN ↔ Building-Nav Integration Guide
### For the Robo-BN Architecture Team

**Document version:** 1.0  
**Source codebase:** `building-nav-proto` (Day 8 integration complete)  
**Audience:** Architects and engineers on the Robo-BN side planning the NLU layer and REST API surface  
**Purpose:** Explain exactly what the building-nav frontend and backend expect from Robo-BN so Robo-BN can be architected to serve those contracts without surprises.

---

## Table of Contents

1. [System Boundary Map](#1-system-boundary-map)
2. [What Building-Nav Has Already Built](#2-what-building-nav-has-already-built)
3. [The Two Pipelines Robo-BN Must Serve](#3-the-two-pipelines-robo-bn-must-serve)
4. [The Three HTTP Endpoints Robo-BN Must Expose](#4-the-three-http-endpoints-robo-bn-must-expose)
5. [The `/api/navigate` Contract in Detail](#5-the-apinavigate-contract-in-detail)
6. [Session Management Expectations](#6-session-management-expectations)
7. [Language Support Requirements](#7-language-support-requirements)
8. [Degradation and Circuit Breaker Behaviour](#8-degradation-and-circuit-breaker-behaviour)
9. [Navigation State Machine — What Robo-BN Must Understand](#9-navigation-state-machine--what-robo-bn-must-understand)
10. [Privacy Constraints](#10-privacy-constraints)
11. [Network Topology and Auth](#11-network-topology-and-auth)
12. [End-to-End Scenario Walkthroughs](#12-end-to-end-scenario-walkthroughs)
13. [What NOT to Build on the Robo-BN Side](#13-what-not-to-build-on-the-robo-bn-side)
14. [Integration Verification Checklist](#14-integration-verification-checklist)

---

## 1. System Boundary Map

Understanding ownership is the most important thing before writing any code. The integration fails if either side reaches across its boundary.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ROBO-BN                                       │
│                                                                      │
│  Owns (must build or adapt):                                         │
│  ● POST /api/stt     — audio bytes → {text, language}               │
│  ● POST /api/navigate — text + context → structured intent          │
│  ● POST /api/tts     — text + language → WAV audio bytes            │
│                                                                      │
│  Internal responsibility:                                            │
│  ● STT (Groq Whisper or equivalent, multilingual)                   │
│  ● Language detection                                                │
│  ● Intent classification (navigate, accessibility_request, general) │
│  ● Entity extraction (destination query, accessibility flag)        │
│  ● Conversational LLM response generation                           │
│  ● TTS synthesis (Kokoro for EN/JA/ZH; 406 fallback for KO)        │
│  ● In-memory conversation session history (keyed by session_id)     │
│                                                                      │
│  Does NOT own:                                                       │
│  ● POI database or fuzzy matching against it                        │
│  ● Navigation graph or routing                                       │
│  ● User's physical location or current floor                        │
│  ● Navigation state machine                                          │
│  ● Any persistent storage                                            │
└──────────────────────┬──────────────────────────────────────────────┘
                       │  HTTP (server-to-server only)
                       │  called by building-nav backend
                       │  NEVER called by browser directly
┌──────────────────────▼──────────────────────────────────────────────┐
│                   BUILDING-NAV BACKEND (FastAPI, already built)      │
│                                                                      │
│  Owns:                                                               │
│  ● POST /chat        — orchestration hub (calls Robo-BN internally) │
│  ● POST /tts/instruction — TTS proxy (calls Robo-BN internally)     │
│  ● POI database and fuzzy resolution                                 │
│  ● Navigation graph and Dijkstra routing                             │
│  ● Analytics event logging                                           │
│  ● Circuit breaker for Robo-BN calls                                │
│  ● Session store: {accessibility_mode, chat_language}               │
│                                                                      │
│  Does NOT own:                                                       │
│  ● STT / TTS                                                         │
│  ● LLM or intent classification                                      │
└──────────────────────┬──────────────────────────────────────────────┘
                       │  HTTPS (browser ↔ building-nav backend)
┌──────────────────────▼──────────────────────────────────────────────┐
│                   BUILDING-NAV FRONTEND (PWA, already built)         │
│                                                                      │
│  Owns:                                                               │
│  ● Microphone capture and base64 encoding                           │
│  ● ChatbotPanel UI (voice/text input, conversation bubbles)         │
│  ● NavTTSPlayer (non-rendering, plays WAV audio)                    │
│  ● Navigation state machine (Zustand)                               │
│  ● Candidate confirmation UI                                         │
│  ● Degradation banners                                               │
│                                                                      │
│  Never communicates with Robo-BN directly.                          │
└─────────────────────────────────────────────────────────────────────┘
```

**Critical rule:** The browser never calls Robo-BN. All Robo-BN calls are server-to-server from the building-nav backend. This is intentional — it keeps POI resolution, analytics, and the circuit breaker in one place.

---

## 2. What Building-Nav Has Already Built

Before planning what Robo-BN needs to build, understand what is already live and waiting on the building-nav side. Robo-BN does not need to replicate any of this.

### 2.1 Backend — `backend/routes/chat.py`

This file is **complete and live**. It is the orchestration hub that calls Robo-BN. Here is its exact internal call sequence:

```
POST /chat (from frontend)
    │
    ├─ [if audio_b64] → POST {ROBO_BN_URL}/api/stt   (multipart file upload)
    │                        Returns: {text, language}
    │
    ├─ Always → POST {ROBO_BN_URL}/api/navigate       (JSON body)
    │                        Returns: {intent, destination_query,
    │                                  accessibility_flag, response_text,
    │                                  needs_clarification, language}
    │
    ├─ [if destination_query] → POI LIKE query against building DB
    │
    ├─ [if 0 POI matches] → POST {ROBO_BN_URL}/api/navigate again
    │                             with poi_not_found: true context
    │
    └─ Returns to frontend: {response_text, language, candidates[],
                             needs_confirmation, accessibility_mode,
                             session_id, chatbot_available}
```

The `ROBO_BN_URL` environment variable controls where these calls go. Default: `http://localhost:8001`.

### 2.2 Backend — Circuit Breaker (already built)

```python
# In backend/routes/chat.py
class CircuitBreaker:
    failure_threshold = 3
    recovery_timeout  = 60.0   # seconds
    # States: CLOSED → OPEN → HALF-OPEN → CLOSED
```

After 3 consecutive Robo-BN failures the circuit opens. All `/chat` calls return `chatbot_available: false` immediately without trying Robo-BN. The circuit tests recovery every 60 seconds. **Robo-BN does not need to implement any circuit breaker logic** — it just needs to respond correctly or let the timeout fire.

### 2.3 Backend — Session Store (already built)

```python
# In backend/routes/chat.py
sessions: dict[str, dict] = {}
# Per session_id: {accessibility_mode, chat_language, last_accessed}
# Expires: 30 minutes of inactivity
```

This is separate from Robo-BN's own session/conversation history. Building-nav tracks navigation session state. Robo-BN tracks conversation history. Both are keyed by the same `session_id` UUID.

### 2.4 Backend — Accessibility Routing (already built)

```python
# graph.py — shortest_path already supports this
def shortest_path(self, start, end, accessible_only: bool = False):
    # Filters out edges where accessible=False when flag is set

# routing.py
@router.get("/route")
async def get_route(from_: int, to: int, accessible_only: bool = False):
    path = nav_graph.shortest_path(from_, to, accessible_only=accessible_only)
```

When Robo-BN returns `accessibility_flag: true`, the building-nav backend sets `accessibility_mode = true` in the session. Every subsequent `/route` call from the frontend automatically appends `&accessible_only=true`. This is already wired end-to-end.

### 2.5 Backend — `/pois` Endpoint (already built)

```
GET /pois
Returns: [{id, name, category, search_terms, node_id, node_accessible,
           floor_name, floor_num}, ...]
```

Robo-BN should call this at startup and cache the result. It provides the `available_pois` list injected into every `/api/navigate` call. See §5 for how this list is used in the prompt.

### 2.6 Frontend — `ChatbotPanel.jsx` (already built)

The panel is a bottom-sheet component that:
- Opens when user taps the 🎙️ FAB
- Captures audio via `MediaRecorder` on press-and-hold of the mic button
- Encodes audio as a `Blob` → base64 string → sends to building-nav `/chat`
- Displays conversation bubbles (user + assistant alternating)
- Renders a candidate confirmation card when `needs_confirmation: true`
- Shows a language selector (Auto / English / 日本語 / 中文 / 한국어)
- Shows an amber degradation banner when `chatbot_available: false`
- Disables the mic button when unavailable

The panel calls `selectDestination(nodeId)` on candidate confirmation, which triggers the existing Dijkstra routing flow. The chatbot converges with manual navigation at destination selection — no special routing path needed.

### 2.7 Frontend — `NavTTSPlayer.jsx` (already built)

A non-rendering React component that monitors Zustand state and fires TTS requests. It already handles:

| State Transition | Text Spoken |
|---|---|
| `UNLOCATED → ANCHORED` | `"You are located at {node.label}"` |
| `NAVIGATING` + step advances | `instruction.text` (e.g. `"Turn left at the elevator bank"`) |
| `→ REROUTING` | `"Recalculating route"` |
| `→ ARRIVED` | `"You have arrived at {destination.label}"` |

For each event it calls `POST /tts/instruction { text, language }`, receives WAV bytes, and plays them. On `status 406` from the server it falls back to `window.speechSynthesis`. The audio is cancelled immediately if a higher-priority event fires (e.g. reroute interrupts a step instruction).

### 2.8 Frontend — `api/index.js` — `sendChatRequest` (already built)

```javascript
export async function sendChatRequest(body) {
  return fetchJson(`/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, 10000);  // ← 10-second timeout
}
```

The 10-second timeout is what Robo-BN's STT + LLM + response chain must complete within. This is the hard latency budget from the browser's perspective.

### 2.9 Vite Proxy — Endpoints Forwarded to Building-Nav Backend

```javascript
// vite.config.js — already configured
proxy: {
  '/chat': 'http://localhost:8000',
  '/tts':  'http://localhost:8000',
  // ... all other nav endpoints
}
```

The browser uses relative URLs. In development, Vite proxies everything to the building-nav backend at port 8000. In production, a reverse proxy handles this.

---

## 3. The Two Pipelines Robo-BN Must Serve

These are architecturally distinct. Robo-BN must handle both but they have different latency requirements, statefulness, and LLM involvement.

### Pipeline A — Conversational Destination Resolution

**Purpose:** User expresses a destination or navigation need in natural language. Robo-BN resolves it to a structured intent + entity that building-nav can act on.

**Trigger:** User speaks or types in `ChatbotPanel`  
**LLM involved:** Yes  
**Stateful (session history):** Yes  
**Latency budget:** < 8 seconds total for STT + LLM + response (10s hard browser timeout minus ~2s network overhead)

```
Frontend captures audio/text
    ↓ POST /chat {audio_b64|text, session_id, current_node_id, language}
Building-nav backend
    ↓ POST /api/stt (if audio)
Robo-BN STT → {text, language}
    ↓ POST /api/navigate {text, language, session_id, building_context}
Robo-BN NLU → {intent, destination_query, accessibility_flag, response_text, ...}
Building-nav POI resolution → candidates[]
    ↓ Response to frontend
Frontend shows response_text + candidate confirmation buttons
User confirms → routing begins
```

### Pipeline B — Navigation Instruction TTS

**Purpose:** Each navigation state transition is announced aloud to the user. No LLM, no session, stateless.

**Trigger:** Navigation state changes in Zustand (`NavTTSPlayer`)  
**LLM involved:** No  
**Stateful:** No — completely stateless  
**Latency budget:** < 3 seconds preferred (user has already moved and is waiting to hear the instruction)

```
Zustand state changes (ANCHORED / step advance / REROUTING / ARRIVED)
    ↓ POST /tts/instruction {text, language}
Building-nav backend
    ↓ POST /api/tts {text, language}
Robo-BN TTS → WAV bytes (or 406 for unsupported language)
Building-nav proxies response to frontend
Frontend plays WAV audio
```

These two pipelines share the same Robo-BN server but must be independently operable. If the LLM is slow or down, Pipeline B (TTS) must still work.

---

## 4. The Three HTTP Endpoints Robo-BN Must Expose

### 4.1 `POST /api/stt`

**Called by:** building-nav backend, inside `/chat` handler, when `audio_b64` is present  
**Transport:** multipart form upload  
**Timeout allowed:** ~5 seconds (part of the 10s total budget)

**Request:**
```
Content-Type: multipart/form-data
Field: file — audio bytes, filename="audio.wav", Content-Type="audio/wav"
```

The audio blob comes from the browser's `MediaRecorder` API encoding as `audio/webm`. The building-nav backend base64-decodes the client's payload and sends the raw bytes to Robo-BN as a file upload. Robo-BN's STT layer must handle `audio/webm` input (not just WAV) since `MediaRecorder` default encoding varies by browser.

**Response (200 OK):**
```json
{
  "text": "I want to go to the cafeteria",
  "language": "en"
}
```

| Field | Type | Notes |
|---|---|---|
| `text` | string | Transcribed text. Non-empty. |
| `language` | string | ISO 639-1 code: `en`, `ja`, `zh`, `ko`. Used for the subsequent `/api/navigate` call and stored in the building-nav session. |

**On failure:** Building-nav catches the exception, records a circuit breaker failure, and returns a Level 2 degradation response to the frontend (prompts user to type instead). Robo-BN should return a non-200 status on transcription failure rather than an empty string in a 200 body.

---

### 4.2 `POST /api/navigate`

This is the primary NLU endpoint and the most complex. Full contract in §5.

---

### 4.3 `POST /api/tts`

**Called by:** building-nav backend, inside `/tts/instruction` handler  
**Transport:** JSON request, binary WAV response  
**Stateless:** No session, no LLM, pure synthesis

**Request:**
```json
{
  "text": "Turn left at the elevator bank",
  "language": "en"
}
```

| Field | Type | Notes |
|---|---|---|
| `text` | string | Plain text to synthesise. Already a full English sentence from the routing engine. No SSML needed. |
| `language` | string | ISO 639-1 code. Supported: `en`, `ja`, `zh`. `ko` must return 406 (see below). |

**Response (200 OK):**
```
Content-Type: audio/wav
Body: WAV audio bytes (binary)
```

Building-nav proxies this binary response directly to the frontend using a `StreamingResponse`. The frontend creates an `Audio` object URL from the blob and plays it.

**Response (406 Not Acceptable) — Korean fallback:**
```json
{
  "fallback": "browser_tts",
  "language": "ko"
}
```

When `language: "ko"` is requested and no Korean Kokoro voice is available, return `406`. Building-nav's proxy forwards the 406 to the frontend. `NavTTSPlayer` detects the 406 and falls back to `window.speechSynthesis` with `lang: "ko"`. This is the agreed degradation contract — do not return a 500 or a silent 200 for Korean.

**On total failure (5xx):** `NavTTSPlayer` catches the error and falls back to browser `speechSynthesis` for all languages. TTS failure never blocks navigation — it is always best-effort.

---

## 5. The `/api/navigate` Contract in Detail

This is the most critical interface. Get this right and the rest falls into place.

### 5.1 Request Schema

```json
{
  "text": "I'm in a wheelchair, I need to find the elevator",
  "language": "en",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "building_context": {
    "current_node_label": "Main Lobby",
    "available_pois": [
      "Cafeteria",
      "Conference Room A",
      "Elevator Bank",
      "Stairwell A",
      "Main Lobby Entrance"
    ],
    "floor_name": "Ground Floor"
  }
}
```

| Field | Type | Required | Source in building-nav |
|---|---|---|---|
| `text` | string | Yes | STT transcript or direct user text input |
| `language` | string | Yes | From STT response or session default |
| `session_id` | string UUID | Yes | `crypto.randomUUID()` generated once per browser session, stored in Zustand |
| `building_context.current_node_label` | string | Yes | `nodes` table lookup by `current_node_id` |
| `building_context.available_pois` | string[] | Yes | All `name` values from `pois` table |
| `building_context.floor_name` | string | Yes | `floors.name` for the current floor |

**`available_pois` is critical.** Inject this list into the LLM prompt so the model can match user intent against real destination names rather than hallucinating POIs that do not exist. The building-nav `GET /pois` endpoint returns this data at startup. Robo-BN should refresh it periodically (recommended every 10 minutes) via a background task.

**Clarification context variant.** When building-nav gets 0 POI matches for `destination_query`, it calls `/api/navigate` a second time with additional fields in `building_context`:

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

When `poi_not_found: true` is present, the LLM should generate a clarification response that offers the closest matching POI names from `available_pois` and does NOT return a new `destination_query` (the frontend would loop again if it did).

### 5.2 Response Schema

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

| Field | Type | Required | Description |
|---|---|---|---|
| `intent` | string | Yes | See intent values below |
| `destination_query` | string \| null | Yes | Extracted destination string. Must be usable in a LIKE search against `pois.search_terms`. Null if intent is not navigation. |
| `accessibility_flag` | boolean | Yes | True if user expressed a mobility/accessibility constraint. This is sticky for the session — once true, stays true. |
| `response_text` | string | Yes | Full human-readable response to show in the chat bubble and pass to TTS. Must be in the same language as `language`. |
| `needs_clarification` | boolean | Yes | True if the LLM needs more info before suggesting a destination. Building-nav uses this as a hint but makes its own determination based on POI match count. |
| `language` | string | Yes | ISO 639-1 code of the detected/confirmed language. Building-nav stores this in the session. |
| `session_id` | string | Yes | Echo the session_id back. |

### 5.3 Intent Values

| Intent | Meaning | `destination_query` | `accessibility_flag` |
|---|---|---|---|
| `navigation` | User wants to go somewhere | Required — extract the destination | May be true |
| `accessibility_request` | User expressed a mobility constraint, no specific destination yet | null | Always true |
| `general` | Small talk, building info, anything not navigation | null | false |
| `out_of_scope` | Completely unrelated (weather, sports, etc.) | null | false |
| `clarify` | LLM needs more info to determine destination | null | false |

**Compound intents:** A single utterance can combine `navigation` + `accessibility_request` (e.g. "I'm in a wheelchair and I need the elevator"). In this case return `intent: "navigation"`, a non-null `destination_query`, and `accessibility_flag: true`. Do not require two separate turns to capture both signals.

### 5.4 How Building-Nav Uses the Response

Understanding this flow prevents mismatched expectations:

```python
# In backend/routes/chat.py (simplified)

intent = nav_data["intent"]
destination_query = nav_data["destination_query"]
accessibility_flag = nav_data["accessibility_flag"]

# 1. Apply accessibility flag immediately
if accessibility_flag:
    session["accessibility_mode"] = True
    # All future /route calls will use accessible_only=True

# 2. Resolve destination query against database
candidates = []
if intent == "navigation" and destination_query:
    results = await db.execute(
        "SELECT name, category, node_id, x, y FROM pois "
        "WHERE LOWER(search_terms) LIKE :q LIMIT 8",
        {"q": f"%{destination_query.lower()}%"}
    )
    candidates = [dict(r) for r in results]

    if not candidates:
        # Call /api/navigate again with poi_not_found context
        # (see §5.1 clarification variant)
        ...

# 3. Return to frontend
return {
    "response_text": nav_data["response_text"],
    "candidates": candidates,
    "needs_confirmation": len(candidates) > 0,
    "accessibility_mode": session["accessibility_mode"],
    ...
}
```

**Consequence for `destination_query` quality:** The query is used raw in a case-insensitive LIKE search against `pois.search_terms`. It does not go through another NLP step. If the user says "coffee" and the POI is named "Staff Cafeteria" with `search_terms = "cafeteria staff cafe coffee"`, the match will succeed. But if the query is too long or phrased as a sentence, the LIKE will fail. **`destination_query` must be a short noun phrase, not a sentence.** Good: `"cafeteria"`, `"elevator"`, `"conference room a"`. Bad: `"I want to go to the cafeteria on the ground floor"`.

---

## 6. Session Management Expectations

### What building-nav sends as `session_id`

A `crypto.randomUUID()` value generated once when the React app loads, stored in Zustand state as `chatbot.sessionId`. It persists for the browser session (cleared on app close; not stored in localStorage). It is the same UUID for all turns of a conversation.

### What Robo-BN should do with `session_id`

Maintain an in-memory conversation history dict keyed by `session_id`:

```python
app.state.navigate_sessions: dict[str, list[dict]] = {}
# Each entry: {role: "user"|"assistant", text: str}
```

Inject the last N turns of history into the LLM context on each `/api/navigate` call. This is what enables multi-turn conversations like:
1. User: "I need to find somewhere to eat"
2. Assistant: "I found the Cafeteria and Coffee Corner. Which would you prefer?"
3. User: "The first one" ← Robo-BN must resolve "the first one" to "Cafeteria" using history

**Expiry:** Expire sessions after 30 minutes of inactivity. No persistence to disk — this satisfies FR-017.

### What building-nav does NOT expect from sessions

Building-nav does not expect Robo-BN to remember the user's current navigation state, their floor, their current node, or their route. That information is injected fresh on every call via `building_context`. Building-nav owns navigation state; Robo-BN owns only the conversation transcript.

---

## 7. Language Support Requirements

### Languages the frontend exposes

The `ChatbotPanel` language selector offers:

| Code | Display | TTS Expected |
|---|---|---|
| `null` (Auto) | Auto | Detected from STT |
| `en` | English | Kokoro — full support |
| `ja` | 日本語 | Kokoro — full support |
| `zh` | 中文 | Kokoro ZH — build required |
| `ko` | 한국어 | Return 406 → browser fallback |

### Language detection flow

1. If user submits audio: Groq Whisper's `verbose_json` response includes a `language` field. Use this as the detected language.
2. If user submits text only: use the language stored in the session, or the language override from `building_context` passed by building-nav.
3. Both ISO 639-1 (`en`) and ISO 639-2 (`eng`) may appear in Whisper output. Normalise to ISO 639-1 before returning.

### `response_text` language

The LLM **must generate `response_text` in the detected language**. If the user spoke Japanese, `response_text` must be Japanese. Building-nav passes the text directly to TTS and displays it in the chat bubble — there is no translation step.

### Korean fallback protocol

```
POST /api/tts {"text": "...", "language": "ko"}
→ 406 {"fallback": "browser_tts", "language": "ko"}

Frontend NavTTSPlayer detects 406:
    fallbackSpeak(text, "ko")
    → window.speechSynthesis.speak(utterance with lang="ko-KR")
```

This is the only 406 case. All other languages must return 200 with WAV bytes or 5xx on genuine failure.

---

## 8. Degradation and Circuit Breaker Behaviour

### The four degradation levels (as seen by the user)

| Level | Condition | User Experience |
|---|---|---|
| 0 — Full | Robo-BN fully operational | Voice + text chat, TTS instructions |
| 1 — TTS down | `/api/tts` failing, LLM OK | Chat works, nav instructions text-only |
| 2 — LLM down, STT OK | `/api/navigate` failing | Speech-to-search: STT text auto-populates search bar |
| 3 — Robo-BN unreachable | All calls failing | Amber banner, mic disabled, manual text search |

### What the building-nav circuit breaker does

```
3 consecutive Robo-BN failures (any of /api/stt, /api/navigate)
    → circuit OPENS
    → building-nav returns {chatbot_available: false} immediately
    → frontend sets chatbot.isAvailable = false
    → ChatbotPanel shows amber "Voice assistant offline" banner
    → Mic button disabled
    → Text input still works but routes to existing /search endpoint

After 60 seconds:
    → circuit HALF-OPENS
    → next request is tried
    → on success: circuit CLOSES, chatbot.isAvailable resets to true
    → on failure: circuit re-OPENS, 60s restart
```

### What Robo-BN should do to cooperate with this

- Return proper HTTP error codes (4xx/5xx) on failure — do not return 200 with an error in the body, as this will fool the circuit breaker into treating it as a success.
- On genuine timeouts, respond with 503.
- The TTS endpoint (`/api/tts`) is not counted by the circuit breaker in the current implementation — it has its own error path in `NavTTSPlayer` that falls back to browser speech synthesis independently.

---

## 9. Navigation State Machine — What Robo-BN Must Understand

Robo-BN receives `current_node_id` and injects the node's label into the LLM via `building_context.current_node_label`. Robo-BN does not see the navigation status directly, but understanding the states helps design good LLM prompts.

```
UNLOCATED ──── scan/select ──→ ANCHORED
                                   │
                               select destination
                                   │
                                   ▼
                            ROUTE_PREVIEW
                                   │
                                begin
                                   │
                                   ▼
                            NAVIGATING ◄──── reroute ────┐
                                   │                      │
                              advance step           off-route QR
                                   │                      │
                              final step            REROUTING ────┘
                                   │
                                   ▼
                              ARRIVED ──── reset ──→ ANCHORED
```

### What triggers TTS (Pipeline B) vs. Chat (Pipeline A)

| Event | Pipeline | Robo-BN endpoint |
|---|---|---|
| User opens ChatbotPanel, types/speaks | A | `/api/stt` (if audio) + `/api/navigate` |
| User scans QR → ANCHORED | B | `/api/tts` — "You are located at X" |
| User taps Next → step advances | B | `/api/tts` — instruction text |
| User goes off-route → REROUTING | B | `/api/tts` — "Recalculating route" |
| User arrives → ARRIVED | B | `/api/tts` — "You have arrived at X" |

Pipeline A calls happen only when the user interacts with the chatbot. Pipeline B calls happen automatically from `NavTTSPlayer` on state transitions.

---

## 10. Privacy Constraints

These are hard requirements, not guidelines. They were established as FR-017 in the project spec.

**What must never be stored by Robo-BN:**
- Transcribed speech text
- Conversation message content
- Which destination a user navigated to
- Language of an individual user
- Any linkage between a session_id and a real person, device, or IP address

**What is acceptable:**
- In-memory session history keyed by `session_id` UUID (cleared on server restart, expiry after 30 min)
- Aggregate model performance metrics (latency, intent counts) with no user identifiers

**Audit path:** Before the integration demo, grep all Robo-BN log statements for any write of `text`, `transcript`, `speech`, `message`, or `session_id` to a log file. If any log line writes request body content, it must be removed.

**Building-nav's analytics side:** Building-nav logs only event type strings (`navigation_via_chat`, `accessibility_mode_enabled`) with no payload content. This is already implemented.

---

## 11. Network Topology and Auth

### During development

```
Browser (port 5173) → Vite proxy → building-nav backend (port 8000)
building-nav backend → Robo-BN (port 8001, configurable via ROBO_BN_URL)
```

All traffic is localhost. No CORS friction. No auth needed.

### During integration testing / LAN deployment

```
Browser (HTTPS, mkcert) → building-nav backend
building-nav backend → Robo-BN (LAN address)
```

Robo-BN must add CORS middleware for the building-nav backend's origin:

```python
# In Robo-BN's FastAPI app — this is a prerequisite
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://building-nav-backend-ip:8000"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
```

Because the building-nav backend calls Robo-BN server-to-server, this is technically not a browser CORS issue. But FastAPI's CORS middleware is still recommended to explicitly restrict which callers can access the NLU endpoints.

### Auth

No auth for the prototype. For production, implement API key authentication (header `X-API-Key`) shared as a secret between building-nav backend and Robo-BN. This is not required for initial integration but should be documented as a production requirement.

### `ROBO_BN_URL` configuration

Set this environment variable in building-nav's `.env` before starting the backend:

```
ROBO_BN_URL=http://localhost:8001   # dev default
ROBO_BN_URL=http://192.168.1.50:8001  # LAN example
```

The building-nav backend reads this in `routes/chat.py` and uses it for all outbound calls.

---

## 12. End-to-End Scenario Walkthroughs

### Scenario A: Single-match voice navigation (happy path)

```
1. User holds mic in ChatbotPanel, says "I want to go to the cafeteria" (EN)

2. Frontend
   MediaRecorder captures ~2s of audio/webm
   Converts to base64
   POST /chat {audio_b64: "...", session_id: "uuid-1", current_node_id: 1, language: null}

3. Building-nav /chat handler
   Decodes audio bytes
   POST Robo-BN /api/stt
       files={"file": (bytes, "audio/wav", "audio/wav")}

4. Robo-BN /api/stt
   Groq Whisper transcribes
   Returns: {text: "I want to go to the cafeteria", language: "en"}

5. Building-nav /chat handler
   POST Robo-BN /api/navigate {
       text: "I want to go to the cafeteria",
       language: "en",
       session_id: "uuid-1",
       building_context: {
           current_node_label: "Main Lobby",
           available_pois: ["Cafeteria", "Conference Room A", "Elevator Bank", ...],
           floor_name: "Ground Floor"
       }
   }

6. Robo-BN /api/navigate
   Classifier: intent="navigation", destination_query="cafeteria", accessibility_flag=false
   LLM generates response_text in English
   Returns: {
       intent: "navigation",
       destination_query: "cafeteria",
       accessibility_flag: false,
       response_text: "I found the Cafeteria on the ground floor. Shall I navigate you there?",
       needs_clarification: false,
       language: "en",
       session_id: "uuid-1"
   }

7. Building-nav /chat handler
   SELECT from pois WHERE LOWER(search_terms) LIKE '%cafeteria%'
   → 1 row: {name: "Cafeteria", category: "poi", node_id: 12, x: 1430, y: 920}
   Returns to frontend: {
       response_text: "I found the Cafeteria...",
       candidates: [{name: "Cafeteria", node_id: 12, category: "poi"}],
       needs_confirmation: true,
       accessibility_mode: false,
       chatbot_available: true
   }

8. Frontend ChatbotPanel
   Displays assistant bubble with response_text
   Shows confirmation card: "Did you mean one of these? [Cafeteria]"

9. NavTTSPlayer (concurrent with step 8)
   POST /tts/instruction {text: "I found the Cafeteria...", language: "en"}

10. Building-nav /tts/instruction → POST Robo-BN /api/tts
    Returns WAV bytes
    Frontend plays audio

11. User taps "Cafeteria" confirmation button
    ChatbotPanel calls selectDestination(12)
    ChatbotPanel closes
    Navigation flow begins: GET /route?from_=1&to=12

12. NavTTSPlayer fires for each step advance
    POST /tts/instruction {text: "Continue straight toward Conference Room A", language: "en"}
    → Robo-BN /api/tts → WAV → plays
```

---

### Scenario B: Accessibility + navigation in one utterance

```
User speaks: "I'm in a wheelchair and I need to find the elevator"

Robo-BN /api/navigate returns:
{
    intent: "navigation",
    destination_query: "elevator",
    accessibility_flag: true,      ← both flags set
    response_text: "I've enabled accessible routes for you. I found the Elevator Bank nearby. Shall I navigate you there?",
    ...
}

Building-nav:
    session["accessibility_mode"] = True    ← sticky for all future routes
    candidates = [{name: "Elevator Bank", node_id: 8}]
    Returns needs_confirmation: true

User confirms → selectDestination(8)
GET /route?from_=1&to=8&accessible_only=true   ← flag is applied automatically
Dijkstra filters out non-accessible edges
```

---

### Scenario C: Zero POI match — clarification loop

```
User: "Where is the blue section?"

Robo-BN /api/navigate:
{
    intent: "navigation",
    destination_query: "blue section",
    ...
}

Building-nav POI search: LIKE '%blue section%' → 0 results

Building-nav calls /api/navigate again:
{
    text: "Clarify: The user wants 'blue section', but no POIs match.",
    building_context: {
        poi_not_found: true,
        query: "blue section",
        available_pois: ["Cafeteria", "Conference Room A", ...]
    }
}

Robo-BN must:
    - NOT return a destination_query (would loop)
    - Generate response_text offering alternatives from available_pois
    - Return: {
        intent: "clarify",
        destination_query: null,
        response_text: "I couldn't find a blue section. Did you mean Conference Room A or the Cafeteria? Here are the locations I know about...",
        needs_clarification: true
      }

Frontend shows assistant bubble with clarification text.
User can respond with a new query (next turn continues the conversation via session history).
```

---

### Scenario D: Robo-BN unreachable (full degradation)

```
User holds mic, speaks destination

Building-nav /chat:
    Decodes audio
    POST Robo-BN /api/stt → connection refused / timeout
    circuit_breaker.record_failure()
    Returns: {chatbot_available: false, response_text: "Voice assistant temporarily unavailable..."}

Frontend:
    chatbot.isAvailable = false
    Amber banner appears: "Voice assistant offline — use text search"
    Mic button disabled
    User can still type in text box → routes to existing /search endpoint
    Navigation flow unaffected
```

---

## 13. What NOT to Build on the Robo-BN Side

This prevents duplication and scope creep.

| Capability | Owner | Why Robo-BN should NOT build it |
|---|---|---|
| POI database | Building-nav backend | Robo-BN has no DB. Gets POI list via `/pois` endpoint at startup. |
| Fuzzy POI matching | Building-nav backend | Happens after `/api/navigate` returns, using LIKE against PostgreSQL |
| Shortest-path routing | Building-nav backend | Already built as in-memory Dijkstra; accessible_only flag already wired |
| QR code resolution | Building-nav backend | QR → nodeId mapping is a database lookup, not an NLU task |
| Navigation state management | Frontend (Zustand) | Robo-BN never sees NAVIGATING/ARRIVED/REROUTING status |
| Accessibility routing logic | Building-nav backend | Robo-BN just sets `accessibility_flag: true`; routing flag application is backend's job |
| Conversation persistence | Neither side | In-memory only, by design, per FR-017 |
| User identity or auth | Neither side (prototype) | No user accounts exist anywhere in the system |
| Multiple floors / multi-building | Out of scope | The nav system currently has one floor. Robo-BN should not model floors. |

---

## 14. Integration Verification Checklist

Use this checklist during integration testing. Each item tests a specific contract from this document.

### Robo-BN endpoint smoke tests

```bash
# 4.1 — STT: does it accept audio/webm and return {text, language}?
curl -X POST http://robo-bn:8001/api/stt \
  -F "file=@test_audio.webm;type=audio/webm"
# Expect: {"text": "...", "language": "en"}

# 4.2 — Navigate: does it return all required fields?
curl -X POST http://robo-bn:8001/api/navigate \
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
# Expect: {intent, destination_query, accessibility_flag, response_text, needs_clarification, language, session_id}
# Check: destination_query should be "cafeteria" (short noun phrase, not a full sentence)

# 4.3 — TTS EN: does it return WAV bytes?
curl -X POST http://robo-bn:8001/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Turn left at the elevator bank", "language": "en"}' \
  --output test_audio.wav
# Expect: valid WAV file (check with: file test_audio.wav)

# 4.3 — TTS KO: does it return 406?
curl -o /dev/null -w "%{http_code}" -X POST http://robo-bn:8001/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "좌회전", "language": "ko"}'
# Expect: 406
```

### Integration flow tests (requires both services running)

```
[ ] Voice → destination resolution (Scenario A above works end-to-end)
[ ] Accessibility flag is set → subsequent /route calls include accessible_only=true
[ ] Zero POI match → clarification response (no destination_query in second /api/navigate response)
[ ] Session continuity: second turn in same session references first turn correctly
[ ] TTS plays on QR scan (ANCHORED state transition)
[ ] TTS plays on each navigation step advance
[ ] TTS fallback: Korean text → browser speechSynthesis fires (check browser console)
[ ] Circuit breaker: shut down Robo-BN → amber banner appears after 3 failures
[ ] Circuit breaker: restart Robo-BN → banner clears after next successful call
[ ] Manual search still works when chatbot.isAvailable = false
[ ] 10-second timeout: Robo-BN must complete STT+LLM within ~8s (test with artificial delay)
```

### Privacy audit

```
[ ] Grep Robo-BN log outputs for: transcript, text=, speech, message_content
    → None should appear in file-backed logs
[ ] Verify in-memory session dict is not written to disk on shutdown
[ ] Confirm building-nav /chat handler does not log req.audio_b64 or req.text
[ ] Confirm building-nav /chat handler does not log nav_data["response_text"]
```

---

## Appendix A — Complete API Reference Summary

| Endpoint | Method | Caller | Purpose |
|---|---|---|---|
| `/api/stt` | POST | building-nav backend | Audio → text transcript |
| `/api/navigate` | POST | building-nav backend | Text → intent + entity |
| `/api/tts` | POST | building-nav backend | Text → WAV audio |

All three are on Robo-BN. All three are called server-to-server. The browser never calls them.

---

## Appendix B — Environment Variable Reference

| Variable | Set in | Used by | Value |
|---|---|---|---|
| `ROBO_BN_URL` | `backend/.env` | `backend/routes/chat.py` | `http://localhost:8001` (dev default) |
| `DATABASE_URL` | `.env` | `backend/db.py` | async postgres connection string |
| `SYNC_DATABASE_URL` | `.env` | `backend/seed.py` | sync postgres connection string |

---

## Appendix C — Key Source Files for Reference

| File | What it contains |
|---|---|
| `backend/routes/chat.py` | Complete `/chat` and `/tts/instruction` implementation — the exact code that calls Robo-BN |
| `backend/routes/routing.py` | `/route` endpoint with `accessible_only` parameter |
| `backend/routes/map.py` | `/pois` endpoint (Robo-BN startup cache source) |
| `backend/graph.py` | `NavGraph.shortest_path(accessible_only=)` — accessibility routing logic |
| `frontend/src/components/ChatbotPanel.jsx` | UI, audio capture, candidate confirmation |
| `frontend/src/components/NavTTSPlayer.jsx` | TTS trigger logic, WAV playback, fallback |
| `frontend/src/store/useNavStore.js` | `sendChatQuery`, `chatbot` state shape, `accessibilityMode` wiring |
| `frontend/src/api/index.js` | `sendChatRequest`, `computeRoute(accessibleOnly)`, 10s timeout |
| `frontend/vite.config.js` | `/chat` and `/tts` proxy entries |
| `documentation/building-nav-architecture.md` | Full system architecture discovery report |
| `.agent/.antigravity/.specs/day8-integration/plan.md` | Original integration architecture plan |
