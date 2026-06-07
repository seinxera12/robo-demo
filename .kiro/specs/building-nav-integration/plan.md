## Day 8 — Voice Chatbot Integration: Unified Architecture Plan

### Reading Summary Before Planning

Robo is a five-worker async pipeline running in a single uvicorn process. It is WebSocket-first, has no REST endpoints, no CORS, no auth. Its strengths are the local VAD, the local Kokoro TTS, the streaming LLM pipeline, and the clean class-level separation that the modularisation assessment confirms is extractable with minimal refactoring. Its current language scope is EN/JA only. It was built for trusted local network use, not as a public-facing microservice.

The building-nav project is a React PWA with a FastAPI backend, a PostgreSQL POI/graph database, a Zustand state machine, and an existing manual search flow. It is deployed publicly over HTTPS.

What this integration is actually asking for is not "embed a chatbot into a nav app." It is asking for a **voice-driven NLU layer that resolves natural language to structured navigation intent, plus a TTS pipeline that vocalises navigation state changes**. Those are two distinct pipelines that happen to share infrastructure.

---

### Part 1 — System Boundary Map

The fundamental question before writing a single line of code is: what does each system own and where does the boundary sit.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ROBO-BN (Robo branch)                        │
│                                                                      │
│  Owns:                                                               │
│  - STT (Groq Whisper, multilingual)                                 │
│  - Language detection                                                │
│  - Intent classification (including navigation intent)              │
│  - Entity extraction (destination query, accessibility flag)        │
│  - Conversational LLM response generation                           │
│  - TTS synthesis (Kokoro local, all supported languages)            │
│  - Session conversation history (in-memory, no persistence)         │
│  - Barge-in / interrupt control                                      │
│                                                                      │
│  Does NOT own:                                                       │
│  - POI database                                                      │
│  - Route graph                                                       │
│  - Navigation state                                                  │
│  - User location                                                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  HTTP (REST + SSE)
                           │  called by building-nav backend
┌──────────────────────────▼──────────────────────────────────────────┐
│                   BUILDING-NAV BACKEND (FastAPI)                     │
│                                                                      │
│  Owns:                                                               │
│  - POI database and resolution logic                                 │
│  - Navigation graph and routing                                      │
│  - QR checkpoint registry                                            │
│  - Accessibility routing flag application                            │
│  - Analytics events (no user identity)                               │
│  - Session orchestration (chatbot context ↔ navigation state)       │
│                                                                      │
│  Does NOT own:                                                       │
│  - STT/TTS                                                           │
│  - LLM                                                               │
│  - Conversation generation                                           │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  HTTPS (REST + WebSocket for TTS stream)
┌──────────────────────────▼──────────────────────────────────────────┐
│                   BUILDING-NAV FRONTEND (PWA)                        │
│                                                                      │
│  Owns:                                                               │
│  - Microphone capture and audio chunking                             │
│  - Chatbot UI (voice/text input, conversation display)              │
│  - Navigation state machine                                          │
│  - TTS audio playback (navigation instructions)                      │
│  - Language preference UI                                            │
│  - LLM degradation UI                                                │
└─────────────────────────────────────────────────────────────────────┘
```

**Why not connect the frontend directly to Robo-BN?**

Robo-BN has no knowledge of the POI database. When the LLM extracts "coffee shop" from user speech, something must resolve that against actual nodes in the building. That resolution is business logic that belongs in building-nav's backend — it knows the graph, the floors, the distances, the accessible routes. Routing all chat through building-nav's backend also keeps the single analytics ingestion point, enforces FR-017 (no identity logging) at one layer, and means the frontend never needs a second WebSocket connection to a second server.

---

### Part 2 — The Two Pipelines

This integration is two distinct pipelines that share Robo-BN infrastructure but serve different purposes. They must be designed and reasoned about separately.

#### Pipeline A — Conversational Destination Resolution

The user wants to find somewhere to go. They express this in natural language, possibly with ambiguity, possibly with accessibility requirements.

```
User speaks/types
        │
        ▼
[Frontend] captures audio or text
        │
        ▼  POST /api/chat (audio bytes or text)
[Building-nav backend] receives input
        │
        ▼  POST /api/stt (if audio)
[Robo-BN] transcribes audio → {text, language}
        │
        ▼  POST /api/navigate (text + language + session_id + building_context)
[Robo-BN] runs intent classification + entity extraction
        │  returns: {intent, destination_query, accessibility_flag,
        │            response_text (streaming), needs_clarification, language}
        │
        ▼
[Building-nav backend] receives structured result
        │
        ├─ destination_query exists?
        │       │
        │       ▼  SELECT from pois WHERE search_terms LIKE %query%
        │  POI resolution against database
        │       │
        │       ├─ 1 match   → confirm with user
        │       ├─ 2-4 match → present options
        │       └─ 0 match   → return to Robo-BN with "not found" context
        │                       for clarification response
        │
        ├─ accessibility_flag = true?
        │       └─ set session accessibility_mode = true
        │           (all subsequent routes use accessible edges only)
        │
        └─ Returns to frontend:
              {response_text, candidates[], needs_confirmation,
               accessibility_mode, language}
        │
        ▼
[Frontend] displays response text, speaks it via TTS
        │
        ▼  POST /api/tts
[Robo-BN] synthesises response_text → WAV audio
        │
        ▼
[Frontend] plays audio, shows confirmation UI if candidates exist
```

#### Pipeline B — Navigation Instruction TTS

The user is navigating. Each state change (location anchored, instruction advance, reroute, arrival) should be announced aloud.

```
Navigation state changes in Zustand
(LOCATED, instruction advance, REROUTING, ARRIVED)
        │
        ▼
[Frontend] determines instruction text + language
        │
        ▼  POST /api/tts  {text, language}
[Robo-BN] Kokoro TTS synthesis → WAV bytes
        │
        ▼
[Frontend] Web Audio API plays WAV
        │
        (next instruction queued after current audio ends)
```

This pipeline is stateless and simple. It uses only Robo-BN's TTS endpoint. It has no LLM involvement. It runs independently of Pipeline A.

---

### Part 3 — Robo-BN Branch Plan

#### 3.1 What to Branch From

Create `robo-bn` branch from current Robo main. This branch diverges permanently — it is a deployment-specific variant, not a contribution back to Robo mainline. The reason: the changes needed (CORS, REST endpoints, navigation-specific intent, multilingual TTS expansion, building POI context injection) are specific to the building-nav deployment configuration and would pollute the general-purpose Robo codebase.

#### 3.2 CORS and Auth Middleware

Robo currently has no CORS middleware and is designed for local trusted network. Robo-BN will be called by building-nav's backend server-to-server, not from a browser directly. This means:

- Add CORS middleware allowing building-nav's backend origin only
- No browser-facing CORS needed (frontend never calls Robo-BN directly)
- No auth for the prototype — note this as a production requirement (API key or mTLS between building-nav backend and Robo-BN)

The architecture guide confirms adding CORS is a prerequisite for any modularisation. It is a one-line FastAPI middleware addition.

#### 3.3 New HTTP Endpoints

Based on the modularisation assessment in the architecture guide, the following endpoints can be extracted with minimal refactoring:

**`POST /api/stt`**

Accepts multipart audio (PCM16 or WAV bytes). Calls `GroqSTTBackend.transcribe()` which is already a pure async function. Returns `{text, language}`. The guide confirms this is ~20 lines of new FastAPI code with zero changes to the backend class.

**`POST /api/tts`**

Accepts `{text, language}`. Calls `TTSRouter.synthesize()` which is already pure. Returns `audio/wav` bytes. Used by Pipeline B for navigation instruction audio. The sentence-boundary accumulation stays in the pipeline; this endpoint calls single-shot synthesis directly.

**`POST /api/detect-language`**

Stateless. Accepts `{text}`. Calls `LanguageDetector.detect()`. Returns `{language}`. Used when text is submitted and language cannot be inferred from STT.

**`POST /api/navigate`** ← new, navigation-specific

This is the key new endpoint. It orchestrates the LLM pipeline for navigation intent specifically. It is not a thin wrapper — it is a new orchestration path that runs the two-call LLM flow (intent classification → response generation) with navigation-specific context injected.

Input:

json

```json
{
  "text": "I need to find a coffee shop",
  "language": "en",
  "session_id": "uuid",
  "building_context": {
    "current_node_label": "Main Lobby",
    "available_pois": ["Cafeteria", "Meeting Room A", ...],
    "floor_name": "Ground Floor"
  }
}
```

Output (streaming SSE or batched JSON):

json

```json
{
  "intent": "navigation",
  "destination_query": "coffee shop",
  "accessibility_flag": false,
  "response_text": "I found a cafeteria on the ground floor. Is that where you'd like to go?",
  "needs_clarification": false,
  "language": "en",
  "session_id": "uuid"
}
```

The `building_context` is injected into the prompt as a context block in `PromptAssembler`. The `available_pois` list tells the LLM what exists in the building, preventing hallucinated destinations. The `current_node_label` allows proximity reasoning in the LLM response ("The cafeteria is nearby from your current location").

The guide says extracting the full LLM response endpoint requires ~80–100 lines of refactoring to pull per-turn logic out of `_llm_worker` into a standalone function. This is the primary engineering effort in the Robo-BN work.

#### 3.4 Navigation Intent in the Classifier

The existing intent classifier recognises: `general`, `environment`, `web_search`, `out_of_scope`, `clarify`. Add two new intents:

**`navigation`** — user wants to go somewhere. Entity extraction required: destination name/category, floor preference if mentioned.

**`accessibility_request`** — user expresses a mobility or accessibility constraint. Examples: "I use a wheelchair", "I cannot use stairs", "I need elevator access". Maps to `accessibility_flag: true` in the response. This can co-occur with `navigation` intent in one utterance ("I'm in a wheelchair, where is the nearest accessible restroom") — the classifier must handle compound intents and return both `destination_query` and `accessibility_flag`.

The `classifier.txt` system prompt gets a navigation-specific section. This is a prompt engineering task, not a code change, but it must be done carefully — the classifier runs at temperature 0.0 and its JSON schema must be updated to include the new fields.

Updated `IntentResult` schema:

python

```python
@dataclass
class IntentResult:
    intent: str          # existing
    language: str        # existing
    confidence: float    # existing
    needs_clarification: bool  # existing
    query_clean: str     # existing
    # New fields:
    destination_query: str | None   # extracted destination for navigation intent
    accessibility_flag: bool        # true if accessibility constraint expressed
```

#### 3.5 Language Expansion: KO and ZH

**Current state:** EN (Kokoro `af_heart`) and JA (Kokoro `jf_alpha`).

**Mandarin Chinese (ZH):** Kokoro supports Mandarin Chinese. Add a `KokoroChineseTTS` instance using the appropriate Kokoro ZH voice. The `TTSRouter._detect_language_from_text()` already checks CJK Unicode ranges — extend it to distinguish Japanese (Hiragana/Katakana present: U+3040–U+30FF) from Chinese (CJK Unified Ideographs without Kana: U+4E00–U+9FFF dominant). Route Chinese text to the ZH Kokoro engine.

**Korean (KO):** Kokoro does not currently have a Korean voice model. Options in order of quality:

1. Integrate a Korean TTS model (e.g., a Coqui/VITS Korean voice) as a fourth local engine — highest quality but requires model sourcing and integration work
2. Use Web Speech API synthesis on the client side for Korean only — acceptable for prototype, browser-dependent quality
3. Use a cloud TTS API (Google Cloud TTS, Azure Cognitive Services) for Korean only — reliable quality, adds external dependency and cost

For this integration: implement options 2 as the immediate path, flag option 1 as the production requirement. The degradation is graceful — Korean users get browser-native TTS rather than Kokoro. The `POST /api/tts` endpoint returns a `406 Not Acceptable` with `{"fallback": "browser_tts", "language": "ko"}` when Korean is requested, and the frontend falls back to Web Speech API synthesis.

**Language detection expansion:** `LanguageDetector.detect()` currently maps anything non-EN to JA or defaults to EN. Extend the mapping to recognise `ko` (Korean) and `zh` (Chinese) ISO codes from Groq Whisper's `verbose_json` response. `LanguageDetector` is described as stateless — this is a simple mapping extension.

**Prompt language blocks:** Add `lang_ko.txt` and `lang_zh.txt` to the deployment configuration. These instruct the LLM to respond in Korean and Chinese respectively when those languages are detected.

#### 3.6 Building-Nav Deployment Configuration

Robo uses a `deployment.yaml` and `environment_docs` for deployment-specific context. The building-nav deployment gets its own configuration:

**`deployment.yaml` for building-nav:**

- `deployment_name`: building-nav
- `tone_override`: neutral (not formal)
- `out_of_scope_response`: multilingual message explaining the assistant only helps with navigation in this building
- `poi_endpoint`: URL of building-nav's `/pois` endpoint — Robo-BN fetches this at startup and caches the POI list

**`environment_docs/building_context.txt`:** Building name, floor descriptions, general layout description. This is the static context block injected when intent is `navigation`.

**POI list injection at startup:** In the Robo-BN lifespan startup, after warm_up calls, add a call to building-nav's `/pois` endpoint. Store the POI list in `app.state.poi_list`. This list is injected into `building_context.available_pois` in every `/api/navigate` call. Refresh every 10 minutes via a background task to pick up any POI changes.

#### 3.7 Session Management for `/api/navigate`

The existing pipeline maintains session history in `PipelineState`. For the REST path, sessions are identified by `session_id` UUID. Store session history in a dict keyed by session_id:

python

```python
app.state.navigate_sessions: dict[str, list[dict]] = {}
```

History is in-memory only. No persistence. Sessions expire after 30 minutes of inactivity via a background cleanup task. This satisfies FR-017 — no conversation history is written to disk or database.

#### 3.8 Summary of Robo-BN Changes

|Change|Effort|Basis|
|---|---|---|
|Add CORS middleware|Trivial|Prerequisite per architecture guide|
|POST /api/stt|~20 lines|Architecture guide: minimal refactoring|
|POST /api/tts|~20 lines|Architecture guide: minimal refactoring|
|POST /api/detect-language|~5 lines|Architecture guide: one-liner|
|POST /api/navigate (new orchestration)|~100 lines|Architecture guide: moderate refactoring|
|Navigation + accessibility intents in classifier|Prompt engineering + schema update|Medium|
|ZH language support (Kokoro)|Medium|New TTS engine instance + routing|
|KO language fallback|Small|406 response + client fallback|
|Language detection: KO + ZH mapping|Trivial|Extend existing mapping dict|
|lang_ko.txt + lang_zh.txt prompt blocks|Content work|Follow existing lang_en/ja pattern|
|building-nav deployment.yaml|Config work|Follow existing deployment pattern|
|POI list injection at startup|Small|New lifespan step + background refresh|
|In-memory session store for REST path|Small|Dict + expiry background task|

---

### Part 4 — Building-Nav Changes Plan

#### 4.1 Backend Changes

**New `/chat` endpoint — the orchestration hub**

This is the single endpoint the frontend calls for all chatbot interactions. It accepts audio bytes or text, orchestrates calls to Robo-BN, resolves POIs against the database, applies accessibility flags, and returns a unified response. The frontend never calls Robo-BN directly.

```
POST /chat
Input:  {audio_b64, text, language, session_id, current_node_id}
        (audio_b64 OR text, not both)

Internal flow:
    1. If audio_b64: call Robo-BN POST /api/stt → {text, language}
    2. Call Robo-BN POST /api/navigate with text + language + building_context
          building_context = {
              current_node_label: nodes[current_node_id].label,
              available_pois: [all poi names from DB],
              floor_name: current floor name
          }
    3. If destination_query returned:
          SELECT from pois WHERE search_terms matches destination_query
          → candidates[]
    4. If accessibility_flag = true:
          store in session: accessibility_mode = true
    5. Return:
          {response_text, language, candidates[], needs_confirmation,
           accessibility_mode, session_id}

Output is JSON. response_text is the full generated text (not streaming at this layer).
```

**Why not stream?** The frontend needs both the response text (to display and speak) and the candidates array (to render as buttons). These cannot be streamed independently without a more complex SSE protocol. For the prototype, batch the response. If latency becomes a problem, add SSE with typed events as a later optimisation.

**New `/tts/instruction` endpoint**

Proxies to Robo-BN `/api/tts`. The frontend calls this with navigation instruction text and language. Returns WAV bytes. Building-nav backend sits in the middle to control rate limiting and log events without logging content.

**Modify `/route` to accept accessibility flag**

The route endpoint gains an optional `accessible_only=true` parameter. When set, the Dijkstra query filters edges to `WHERE accessible = true`. This connects the chatbot's accessibility detection directly to the routing engine.

**New `/pois` endpoint (already partially exists)**

Extend to return full POI data including search terms, floor, and accessibility notes. This is used by Robo-BN at startup to build its POI list, and by the frontend for manual category browsing.

**Session store on building-nav backend**

A lightweight in-memory dict keyed by session_id tracking:

- `accessibility_mode`: bool
- `active_destination_id`: current confirmed destination node
- `chat_language`: detected language for this session

This is separate from Robo-BN's conversation history. It is navigation session state, not conversation state. No persistence, same 30-minute expiry pattern.

#### 4.2 Frontend Changes

**Chatbot Panel Component**

A bottom-sheet panel that slides up over the map, similar to the instruction panel but taller. Contains:

- Conversation display area (scrollable message bubbles, alternating user/assistant)
- Voice input button (hold to record, release to send)
- Text input field with send button
- Language selector (manual override, shows detected language)
- "Chatbot unavailable" degraded state banner when Robo-BN is unreachable

The panel coexists with the navigation flow. When navigation is active, the chatbot can be opened to ask a follow-up ("how far is it?" or "can I find a restroom on the way?"). These queries go through the same `/chat` endpoint.

**Voice Input Component**

Hold-to-record interaction pattern. On press: start capturing via `navigator.mediaDevices.getUserMedia` with audio constraints matching Robo-BN's expected format (PCM16, 16kHz). On release: stop capture, encode as base64, send to `/chat`. Show waveform animation while recording to confirm mic is active.

Web Speech API is used as a client-side fallback only — if the backend `/chat` endpoint is unreachable or times out, fall back to `window.SpeechRecognition` for STT and handle the text normally through manual search. This satisfies NFR-021 graceful degradation.

**POI Confirmation Flow**

When `/chat` returns `candidates[]` with `needs_confirmation: true`, render a confirmation bottom sheet:

```
┌────────────────────────────────────────┐
│  Did you mean one of these?            │
│                                        │
│  ● Staff Cafeteria  (Ground Floor)     │
│  ● Coffee Corner    (Floor 2)          │
│                                        │
│  [None of these — describe again]      │
└────────────────────────────────────────┘
```

Tapping a candidate calls `handleSelectDestination(candidate.node_id)` — the same action triggered by manual search. The chatbot flow converges with the existing navigation flow at destination selection.

**Navigation TTS Player**

A non-visible audio component that listens to Zustand navigation state changes and fires TTS requests:

- `status → LOCATED`: "You are located at [label]"
- `currentStepIndex` advances: speaks the new instruction text
- `status → REROUTING`: "Recalculating route"
- `status → ARRIVED`: "You have arrived at [destination label]"

Each TTS request calls `/tts/instruction` with the text and current session language. Plays via Web Audio API. A simple queue ensures instructions don't overlap — next instruction waits for current audio to finish. Barge-in on a new QR scan cancels current audio immediately (matching Robo's interrupt pattern).

**Zustand Store Extensions**

js

```js
// New chatbot state in useNavStore
chatbot: {
  isOpen: false,
  messages: [],              // {role, text, timestamp}
  isListening: false,        // recording in progress
  isProcessing: false,       // waiting for /chat response
  isAvailable: true,         // Robo-BN reachable
  detectedLanguage: 'en',    // from last STT response
  selectedLanguage: null,    // manual override, null = auto
  accessibilityMode: false,  // set by chatbot, persists for session
  sessionId: uuid(),         // generated once per app session
  candidates: [],            // POI candidates awaiting confirmation
  needsConfirmation: false,
}
```

The `accessibilityMode` flag in the store is read by the routing call — when true, `accessible_only=true` is appended to every `/route` request automatically.

**Language Selector**

A small flag/language button in the LocationBar or ChatbotPanel. Shows auto-detected language. Tap to open a four-option selector: English, 日本語, 한국어, 中文. Selection stores in `chatbot.selectedLanguage` and overrides detection on subsequent requests. This satisfies FR-011's manual selection fallback.

**Degradation State**

On app load, ping Robo-BN's health endpoint (via building-nav backend proxy). If unavailable:

- Set `chatbot.isAvailable = false`
- Show banner in ChatbotPanel: language-appropriate message that voice assistant is temporarily unavailable
- Voice button disabled
- Text input remains but routes to `/search` (existing endpoint) instead of `/chat`
- Category browsing as additional fallback

---

### Part 5 — Data Flow for Key Scenarios

#### Scenario 1: Voice destination with single POI match

```
User holds mic button → speaks "I want to go to the cafeteria" (EN)
Frontend captures audio → POST /chat {audio_b64, session_id, current_node_id: 1}
Backend → POST Robo-BN /api/stt → {text: "I want to go to the cafeteria", language: "en"}
Backend → POST Robo-BN /api/navigate {text, language, building_context}
Robo-BN → classifier: intent=navigation, destination_query="cafeteria", accessibility_flag=false
Robo-BN → LLM generates: "I found the Staff Cafeteria on the ground floor. Shall I navigate you there?"
Backend → POI resolve "cafeteria" → [{node_id: 12, name: "Cafeteria", floor: "Ground"}]
Backend → returns {response_text, candidates: [cafeteria], needs_confirmation: true, language: "en"}
Frontend → displays message bubble + plays TTS of response_text
Frontend → shows confirmation sheet with Cafeteria option
User taps Cafeteria
Frontend → handleSelectDestination(12) → existing routing flow
```

#### Scenario 2: Accessibility request co-expressed with navigation

```
User speaks: "I'm in a wheelchair, I need to find the elevator"
Backend → Robo-BN navigate: intent=navigation, destination_query="elevator",
          accessibility_flag=true
Backend → sets session accessibility_mode=true
Backend → POI resolve "elevator" → [{node_id: 8, name: "Elevator Bank"}]
Backend → returns {response_text: "I've enabled accessible routes for you.
          The elevator bank is nearby. Shall I navigate you there?",
          accessibility_mode: true, candidates: [elevator bank]}
Frontend → chatbot.accessibilityMode = true
Frontend → all subsequent /route calls include accessible_only=true
User confirms → route uses only accessible edges
```

#### Scenario 3: No POI match — clarification loop

```
User speaks: "Where is the blue section?"
Robo-BN: destination_query="blue section"
Backend: POI resolve "blue section" → [] (no match)
Backend: calls Robo-BN /api/navigate again with context {poi_not_found: true, query: "blue section"}
Robo-BN: LLM generates clarification with suggestions
         "I couldn't find a blue section. Did you mean the Blue Conference Room,
          or perhaps the East Wing? Here are some nearby options:"
Backend: returns {response_text, candidates: [Conference Room A, East Wing entrance],
         needs_clarification: true}
Frontend: displays message + shows alternative options
```

#### Scenario 4: Navigation TTS during active route

```
User scans checkpoint QR → currentStepIndex advances to 2
Zustand: instruction text = "Turn left at the elevator bank"
NavTTSPlayer detects stepIndex change
POST /tts/instruction {text: "Turn left at the elevator bank", language: "en"}
Robo-BN /api/tts → Kokoro synthesis → WAV bytes
Backend proxies WAV back to frontend
Web Audio API plays audio
User scans off-route QR → reroute begins
NavTTSPlayer: cancel current audio immediately
POST /tts/instruction {text: "Recalculating route", language: "en"}
Plays immediately
```

#### Scenario 5: LLM unavailable

```
User holds mic → speaks destination
POST /chat → building-nav backend → POST Robo-BN /api/stt → success {text, language}
→ POST Robo-BN /api/navigate → timeout / 503
Backend: catches exception, returns {chatbot_available: false, text_extracted: "cafeteria"}
Frontend: sets chatbot.isAvailable = false for this session
Frontend: shows "Voice assistant temporarily unavailable"
Frontend: auto-populates search bar with extracted text "cafeteria" (STT worked, LLM didn't)
User sees search results normally
Manual search continues as per existing Day 2 implementation
```

---

### Part 6 — Privacy Implementation (FR-017)

**What is not stored anywhere:**

- Transcribed speech text
- Conversation messages
- Language of individual users
- Which destination a user navigated to, linked to any identifier

**What is stored (analytics events):**

- Event type: `navigation_via_chat` (no payload content, just the event type)
- Event type: `accessibility_mode_enabled` (no user link)
- Aggregate counts per session_id (session_id is a random UUID, not linked to device, account, or IP)

**Where conversation lives:**

- In Robo-BN: in-memory session dict, keyed by UUID, expires after 30 minutes of inactivity, never written to disk
- In building-nav backend: accessibility_mode flag and active destination only, also in-memory, same expiry
- In frontend: Zustand store messages array, cleared on app close (sessionStorage, not localStorage)

**Audit point:** Verify FastAPI request logging middleware does not log request bodies. If Uvicorn is configured with `access_log=True`, request paths are logged but not bodies. Confirm Robo-BN also does not write transcripts to any log file in the `robo-bn` branch.

---

### Part 7 — Degradation Strategy (NFR-021)

Three degradation levels, each with a clear user-facing state:

**Level 0 — Full capability:** Robo-BN reachable, LLM responsive, TTS working. Full voice chatbot + navigation TTS.

**Level 1 — TTS unavailable (Robo-BN down or slow):** Voice input disabled. Navigation instructions shown as text only (instruction panel still works). Manual text search fully functional. Banner: "Voice assistant unavailable — use text search."

**Level 2 — LLM unavailable but STT available:** Voice input captures speech, STT transcribes, but LLM pipeline fails. Auto-populate search bar with transcribed text. Manual search handles it. Banner: "Voice assistant limited — your speech has been converted to text."

**Level 3 — Robo-BN completely unreachable:** Voice input disabled. No TTS. Full manual search + category browsing. This is the Day 2 state — it is always functional. Banner: "Voice assistant offline — use text search or browse by category."

Detection: building-nav backend maintains a circuit breaker for Robo-BN calls. After 3 consecutive failures, mark as unavailable, stop calling, check again after 60 seconds. Frontend receives `chatbot_available: false` flag in any `/chat` response and sets degradation state accordingly.

---

### Part 8 — Implementation Sequence

The work splits across two parallel tracks that converge at integration testing.

**Track A — Robo-BN (can start immediately)**

1. Create `robo-bn` branch, add CORS middleware, confirm existing tests pass
2. Extract `POST /api/stt` and `POST /api/tts` as thin HTTP wrappers — these are the quickest wins and unblock building-nav frontend work early
3. Add `POST /api/detect-language`
4. Add KO and ZH language detection mapping
5. Add ZH Kokoro TTS engine, update TTSRouter routing logic
6. Add KO fallback response (406 + browser_tts directive)
7. Add lang_ko.txt and lang_zh.txt prompt blocks
8. Add navigation and accessibility_request intents to classifier system prompt, update IntentResult schema
9. Refactor per-turn LLM logic out of `_llm_worker` into standalone function
10. Build `POST /api/navigate` endpoint using refactored function
11. Add building-nav deployment.yaml and environment_docs
12. Add POI list fetch at startup + background refresh
13. Add in-memory session store for REST path with expiry

**Track B — Building-nav (starts after /api/stt and /api/tts are available)**

1. Add `/chat` orchestration endpoint to building-nav backend (stubs Robo-BN calls initially)
2. Add `/tts/instruction` proxy endpoint
3. Modify `/route` to accept and apply `accessible_only` flag
4. Add session store (accessibility_mode, language, session_id) to building-nav backend
5. Build ChatbotPanel UI component (text-only first, no voice yet)
6. Build POI confirmation bottom sheet
7. Extend Zustand store with chatbot state
8. Build NavTTSPlayer component, wire to state changes
9. Add voice capture to ChatbotPanel, wire to `/chat`
10. Add language selector UI
11. Add degradation state UI (banner, auto-populate search)
12. Add circuit breaker to building-nav backend for Robo-BN calls
13. Extend analytics events for chat interactions (no content, just event types)

**Integration testing begins when Track A step 10 and Track B step 9 are both complete.**

---

### Part 9 — Risks and Dependencies

|Risk|Impact|Mitigation|
|---|---|---|
|Kokoro ZH voice quality insufficient|Medium|Evaluate early in Track A step 6; fallback to cloud TTS for ZH if unacceptable|
|`/api/navigate` refactoring breaks existing pipeline|High|Keep existing `_llm_worker` intact for WebSocket path; new function is a parallel extraction, not a replacement|
|POI natural language matching quality|High|Test with real building POI names in all 4 languages; add aliases/synonyms column to pois table if needed|
|Latency of STT + LLM + TTS chain (3 external calls)|Medium|Cache TTS for common instruction phrases; show "thinking" indicator immediately; target <4s end-to-end|
|Web Speech API STT quality for JA/KO/ZH|Medium|Robo-BN Groq Whisper is primary; Web Speech API only activates on Robo-BN failure|
|Accessibility intent false positives|Medium|Temperature 0.0 on classifier reduces this; add explicit negative examples to classifier prompt|
|FR-017 compliance — log audit|High|Code review all log statements in both projects before demo; grep for transcript/text/speech in log calls|
|CORS between building-nav backend and Robo-BN|Low|Server-to-server call, no browser CORS needed; verify firewall/network rules in deployment|





## You said: look at day 1 plan, see if you can give an idea of what the .

look at day 1 plan, see if you can give an idea of what the .env file should look like , It has SYNC_DATABASE_URL , and one other variable that i cant recall

Jun 6





## Claude responded: Looking at the Day 1 plan, the .

Looking at the Day 1 plan, the `.env` file had exactly two variables:

env

```env
DATABASE_URL=postgresql+asyncpg://nav:nav123@db/indoornav
SYNC_DATABASE_URL=postgresql://nav:nav123@db/indoornav
```

`DATABASE_URL` uses the `asyncpg` driver prefix — for SQLAlchemy's async engine used by FastAPI during normal request handling.

`SYNC_DATABASE_URL` uses the plain `postgresql://` prefix — for the synchronous psycopg2 driver used by `seed.py`, which runs as a plain synchronous script outside the async context.

The only difference between the two is the driver prefix. The credentials, host, and database name are identical. The host is `db` — the Docker Compose service name, only resolvable from inside the Docker network.





  



Sonnet 4.6 Low





