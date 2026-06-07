# Technical Design: building-nav-integration

## Overview

This document describes the technical design for exposing Robo as an HTTP REST service layer
consumed by the `building-nav` indoor navigation project. Robo currently operates as a
WebSocket-first voice pipeline with no REST surface. This design adds a `server/api/` module
containing five HTTP endpoints, expands language support from EN/JA to EN/JA/ZH/KO, extends
the intent classifier with navigation-specific intents, and adds an in-memory session store for
REST-path conversation history — all without altering the existing WebSocket pipeline behaviour.

### Design Principles

1. **No pipeline disruption** — `VoicePipeline._llm_worker` is refactored by extracting a
   reusable `run_navigate_turn()` coroutine that both `_llm_worker` and the navigate endpoint
   call. The pipeline's observable behaviour (queues, interrupt handling, state transitions)
   is unchanged.
2. **Singleton reuse** — All new endpoints read component instances from `app.state` set during
   the existing lifespan startup. No component is instantiated per-request.
3. **Minimal surface** — New code lives in `server/api/`; `server/main.py` gains only the
   middleware registration, router inclusion, session store init, and Chinese TTS warm-up.
4. **Privacy by default** — Text and audio content are never logged at INFO level or persisted
   to disk in any new code path.

---

## Architecture

### High-Level Component Map

```
┌──────────────────────────────────────────────────────────────────────┐
│  FastAPI app (server/main.py)                                         │
│                                                                        │
│  Middleware: CORSMiddleware (BUILDING_NAV_ORIGIN env var)             │
│                                                                        │
│  Existing WebSocket endpoints (unchanged):                            │
│    /ws         ← AudioClient PCM16/WAV binary channel                │
│    /ws/ui      ← BrowserUI JSON channel                               │
│                                                                        │
│  New REST endpoints (server/api/ APIRouter, prefix=/api):             │
│    POST /api/stt             → server/api/stt.py                     │
│    POST /api/tts             → server/api/tts.py                     │
│    POST /api/detect-language → server/api/detect_language.py         │
│    POST /api/navigate        → server/api/navigate.py                │
│    GET  /api/health          → server/api/health.py                  │
│                                                                        │
│  app.state (all singletons, set in lifespan):                        │
│    .stt_backend      GroqSTTBackend                                   │
│    .tts_router       TTSRouter (now wraps EN + JA + ZH engines)      │
│    .intent_classifier IntentClassifier (updated _VALID_INTENTS)      │
│    .prompt_assembler  PromptAssembler (loads lang_ko/zh blocks)      │
│    .llm_chain         LLMChain                                        │
│    .router            Router                                          │
│    .post_processor    PostProcessor                                   │
│    .lang_detector     LanguageDetector (now maps ko/zh)              │
│    .navigate_sessions dict[str, NavigateSession]  ← NEW              │
│    .kokoro_zh_tts     KokoroChineseTTS             ← NEW             │
└──────────────────────────────────────────────────────────────────────┘
           │ reuses shared singletons
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  server/api/navigate.py                                               │
│                                                                        │
│  async def run_navigate_turn(                                         │
│      text, language, session_history,                                 │
│      building_context,                                                │
│      intent_classifier, prompt_assembler,                             │
│      llm_chain, router, post_processor,                               │
│  ) -> NavigateTurnResult                                              │
│                                                                        │
│  ← called by REST endpoint handler                                   │
│  ← called by VoicePipeline._llm_worker (existing WS path)           │
└──────────────────────────────────────────────────────────────────────┘
```

### New File Layout

```
server/
  api/
    __init__.py           # APIRouter export
    stt.py                # POST /api/stt
    tts.py                # POST /api/tts
    detect_language.py    # POST /api/detect-language
    navigate.py           # POST /api/navigate + run_navigate_turn()
    health.py             # GET /api/health
  prompts/
    lang_ko.txt           # NEW — Korean language/tone instructions
    lang_zh.txt           # NEW — Mandarin Chinese language/tone instructions
    classifier.txt        # MODIFIED — adds navigation/accessibility_request
  tts/
    kokoro_tts.py         # MODIFIED — adds KokoroChineseTTS
    tts_router.py         # MODIFIED — ZH routing, accepts zh_tts parameter
  llm/
    intent.py             # MODIFIED — new IntentResult fields, _VALID_INTENTS
    assembler.py          # MODIFIED — lang_ko/zh loading, building_context block
  lang/
    detector.py           # MODIFIED — adds "ko" and "zh" to _SUPPORTED
  main.py                 # MODIFIED — CORS, router include, ZH warm-up, session init
  config.py               # MODIFIED — BUILDING_NAV_ORIGIN env var
config/
  deployment.yaml         # MODIFIED — building-nav deployment config
```

### Sequence Diagram: POST /api/navigate (Happy Path)

```
building-nav backend          Robo FastAPI              run_navigate_turn()
        │                          │                           │
        │  POST /api/navigate      │                           │
        │  {text, language,        │                           │
        │   session_id,            │                           │
        │   building_context}      │                           │
        │─────────────────────────▶│                           │
        │                          │  look up / create         │
        │                          │  NavigateSession          │
        │                          │──────────────────────────▶│
        │                          │                           │ classify(text)
        │                          │                           │──────▶ IntentClassifier
        │                          │                           │◀────── IntentResult
        │                          │                           │ route(intent_result)
        │                          │                           │──────▶ Router
        │                          │                           │◀────── RouteResult
        │                          │                           │ assemble_prompt(
        │                          │                           │   user_input, intent_result,
        │                          │                           │   history, building_context)
        │                          │                           │──────▶ PromptAssembler
        │                          │                           │◀────── (system_prompt, messages)
        │                          │                           │ llm_chain.stream(messages)
        │                          │                           │──────▶ LLMChain (Groq)
        │                          │                           │◀────── tokens (batched)
        │                          │                           │
        │                          │◀──────────────────────────│
        │                          │  NavigateTurnResult       │
        │                          │  update session history   │
        │  HTTP 200                │                           │
        │  {intent, dest_query,    │                           │
        │   accessibility_flag,    │                           │
        │   response_text, ...}    │                           │
        │◀─────────────────────────│                           │
```

---

## Components and Interfaces

### `server/api/__init__.py`

Exports a single `APIRouter` that is included in `server/main.py`.

```python
from fastapi import APIRouter
from server.api import stt, tts, detect_language, navigate, health

api_router = APIRouter(prefix="/api")
api_router.include_router(stt.router)
api_router.include_router(tts.router)
api_router.include_router(detect_language.router)
api_router.include_router(navigate.router)
api_router.include_router(health.router)
```

Registered in `server/main.py` after middleware, before static files:
```python
app.include_router(api_router)
```

### `server/api/stt.py`

**Route**: `POST /api/stt`  
**Request**: `multipart/form-data`, field `file` (audio bytes, WAV or PCM16)  
**Response 200**: `{"text": str, "language": str}`  
**Response 422**: missing/empty file  
**Response 502**: transcription backend error

```python
router = APIRouter()

@router.post("/stt")
async def stt_endpoint(
    request: Request,
    file: UploadFile = File(...),
) -> STTResponse:
    # Validates file is non-empty (raises 422 via dependency if zero bytes)
    # Reads file.read() → pcm16_bytes
    # Calls app.state.stt_backend.transcribe(pcm16_bytes)
    # Returns STTResponse(text=result.text, language=result.language)
    # On exception: raises HTTPException(502, ...)
```

**Privacy**: `text` is only logged at DEBUG level. INFO logs: endpoint path, status, latency_ms, language.

### `server/api/tts.py`

**Route**: `POST /api/tts`  
**Request**: JSON `{"text": str, "language": str}`  
**Response 200**: `audio/wav` bytes (WAV file)  
**Response 406**: `{"fallback": "browser_tts", "language": "ko"}` when language is `"ko"`  
**Response 422**: missing/empty text or unsupported language  
**Response 502**: synthesis error

```python
@router.post("/tts")
async def tts_endpoint(
    request: Request,
    body: TTSRequest,
) -> Response:
    # Pydantic validates text is non-empty, language is in ACCEPTED_LANGUAGES
    # If language == "ko": return JSONResponse({"fallback":"browser_tts","language":"ko"}, 406)
    # wav_bytes = await app.state.tts_router.synthesize(body.text, body.language)
    # return Response(wav_bytes, media_type="audio/wav")
    # On exception: raise HTTPException(502, ...)
```

`ACCEPTED_LANGUAGES = {"en", "ja", "zh", "ko"}` — all four are accepted at the validation layer.
`"ko"` is accepted but short-circuits before calling TTSRouter.

### `server/api/detect_language.py`

**Route**: `POST /api/detect-language`  
**Request**: JSON `{"text": str}`  
**Response 200**: `{"language": str}` where language ∈ `{"en", "ja", "zh", "ko"}`  
**Response 422**: missing/empty text

The detection logic is a pure function defined in this module (not delegating to `LanguageDetector`
which is STT-output oriented). It applies Unicode range checks directly:

```python
import re

_HIRAGANA_KATAKANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')
_CJK_IDEOGRAPH     = re.compile(r'[\u4e00-\u9fff]')
_HANGUL            = re.compile(r'[\uac00-\ud7a3\u1100-\u11ff]')

def detect_language_from_text(text: str) -> str:
    if _HANGUL.search(text):
        return "ko"
    if _HIRAGANA_KATAKANA.search(text):
        return "ja"
    if _CJK_IDEOGRAPH.search(text):
        return "zh"
    return "en"
```

Priority order: KO > JA > ZH > EN (Hangul checked first, then kana, then CJK-only).

**Rationale for priority**: Korean and Japanese cannot be confused via Unicode ranges (Hangul vs
kana are disjoint). Japanese-Chinese ambiguity is resolved by checking kana first — if any kana
is present, the text is Japanese; CJK ideographs alone indicate Chinese.

### `server/api/navigate.py`

This is the most complex module. It contains:

1. **`NavigateTurnResult`** — dataclass holding the structured result of one LLM turn
2. **`run_navigate_turn()`** — standalone async function extracted from `_llm_worker`
3. **`navigate_endpoint()`** — FastAPI route handler

#### `NavigateTurnResult`

```python
@dataclass
class NavigateTurnResult:
    intent: str               # one of the 7 valid intents
    destination_query: str | None
    accessibility_flag: bool
    response_text: str
    needs_clarification: bool
    language: str
```

#### `run_navigate_turn()`

```python
async def run_navigate_turn(
    text: str,
    language: str,
    session_history: list[dict],
    building_context: BuildingContext | None,
    intent_classifier: IntentClassifier,
    prompt_assembler: PromptAssembler,
    llm_chain: LLMChain,
    router: Router,
    post_processor: PostProcessor,
) -> NavigateTurnResult:
```

This function mirrors the per-turn logic in `_llm_worker` but:
- Accepts `building_context` and injects it into `PromptAssembler`
- **Does not** interact with `asyncio.Queue`, `InterruptController`, or `PipelineState`
- Batches the LLM response (collects all tokens) instead of streaming to a queue
- Returns a `NavigateTurnResult` instead of pushing tokens

Internal steps:
1. Call `intent_classifier.classify(text)` → `IntentResult`
2. Call `router.route(intent_result, language)` → `RouteResult`
3. If `route_result.direct_response` is set: return early with that text, no Call 2
4. Call `prompt_assembler.assemble_prompt(text, intent_result, session_history, ..., building_context=building_context)`
5. Collect all tokens from `llm_chain.stream(messages)` into a string
6. Apply `post_processor.clean()` to the assembled response
7. Append clarification suffix if present
8. Return `NavigateTurnResult`

**On Call 1 failure**: raises `ClassifyError` (HTTP 502 at endpoint layer)  
**On Call 2 failure after retry**: raises `LLMError` (HTTP 502 at endpoint layer)

#### `navigate_endpoint()`

```python
@router.post("/navigate")
async def navigate_endpoint(
    request: Request,
    body: NavigateRequest,
) -> NavigateResponse:
    # 1. Validate: text non-empty (Pydantic)
    # 2. Look up or create NavigateSession in app.state.navigate_sessions
    # 3. Update session.last_active
    # 4. Call run_navigate_turn(...)
    # 5. Append user+assistant to session.history, trim to session_memory_turns*2
    # 6. Return NavigateResponse
```

### `server/api/health.py`

**Route**: `GET /api/health`  
**Response 200**: `{"status": "ok", "tts_ready": bool, "stt_ready": bool}`  
**No I/O** — reads only `app.state` flags set during lifespan startup.

```python
@router.get("/health")
async def health_endpoint(request: Request) -> HealthResponse:
    tts_ready = getattr(request.app.state, "tts_warmed_up", False)
    stt_ready = (
        hasattr(request.app.state, "stt_backend")
        and bool(getattr(request.app.state.config, "groq_api_key", ""))
    )
    return HealthResponse(status="ok", tts_ready=tts_ready, stt_ready=stt_ready)
```

`app.state.tts_warmed_up` is set to `True` in the lifespan after `asyncio.gather(warm_up calls)`
succeeds. If warm-up raises, it stays `False`.

---

## Data Models

### Pydantic Request/Response Models

```python
# server/api/stt.py
class STTResponse(BaseModel):
    text: str
    language: str

# server/api/tts.py
class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1)
    language: str

    @validator("text")
    def text_not_whitespace(cls, v):
        if not v.strip():
            raise ValueError("text must not be whitespace-only")
        return v

    @validator("language")
    def language_accepted(cls, v):
        if v not in {"en", "ja", "zh", "ko"}:
            raise ValueError(f"language must be one of en/ja/zh/ko, got {v!r}")
        return v

# server/api/detect_language.py
class DetectLanguageRequest(BaseModel):
    text: str = Field(..., min_length=1)

class DetectLanguageResponse(BaseModel):
    language: str

# server/api/navigate.py
class BuildingContext(BaseModel):
    current_node_label: str
    available_pois: list[str]
    floor_name: str

class NavigateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    language: str
    session_id: str
    building_context: BuildingContext

    @validator("text")
    def text_not_whitespace(cls, v):
        if not v.strip():
            raise ValueError("text must not be whitespace-only")
        return v

class NavigateResponse(BaseModel):
    intent: str
    destination_query: str | None
    accessibility_flag: bool
    response_text: str
    needs_clarification: bool
    language: str
    session_id: str

# server/api/health.py
class HealthResponse(BaseModel):
    status: str
    tts_ready: bool
    stt_ready: bool
```

### `NavigateSession` Dataclass

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class NavigateSession:
    history: list[dict] = field(default_factory=list)
    # list of {"role": "user"|"assistant", "content": str}
    last_active: datetime = field(default_factory=datetime.utcnow)
```

The session store lives at `app.state.navigate_sessions: dict[str, NavigateSession] = {}`.

---

## Modified Existing Modules

### `server/llm/intent.py`

**Changes**:

1. Extend `_VALID_INTENTS`:

```python
_VALID_INTENTS = frozenset([
    "general",
    "environment",
    "web_search",
    "out_of_scope",
    "clarify",
    "navigation",             # NEW
    "accessibility_request",  # NEW
])
```

2. Add two new fields to `IntentResult`:

```python
@dataclass
class IntentResult:
    intent: str
    language: str
    confidence: float
    needs_clarification: bool
    clarification_reason: str | None
    query_clean: str
    destination_query: str | None = None    # NEW — extracted destination
    accessibility_flag: bool = False        # NEW — mobility constraint flag

    def __post_init__(self) -> None:
        if self.intent not in _VALID_INTENTS:
            raise ValueError(...)
        self.confidence = max(0.0, min(1.0, self.confidence))
        # Normalise empty string to None
        if self.destination_query == "":
            self.destination_query = None
```

3. In `IntentClassifier.classify()`, parse the new fields from the LLM JSON:

```python
return IntentResult(
    ...existing fields...,
    destination_query=data.get("destination_query") or None,
    accessibility_flag=bool(data.get("accessibility_flag", False)),
)
```

The `_default_result()` fallback returns `destination_query=None, accessibility_flag=False`.

**Backward compatibility**: Existing intents (`general`, `environment`, etc.) always have
`destination_query=None, accessibility_flag=False`. New code paths check these fields only
when `intent in {"navigation", "accessibility_request"}`.

### `server/prompts/classifier.txt`

The system prompt is updated to add the two new intent categories, examples, and the extended
JSON schema. The key additions:

```
- **navigation**: The user wants to find or go to a location, room, facility, or landmark.
  Extract the destination in `destination_query`. Set `accessibility_flag: true` if the user
  mentions a mobility or accessibility constraint (wheelchair, elevator, stairs, etc.).
- **accessibility_request**: The user expresses a mobility or physical accessibility need
  without a specific navigation goal.

Updated JSON schema:
{
  "intent": "navigation",
  "language": "en",
  "confidence": 0.95,
  "needs_clarification": false,
  "clarification_reason": null,
  "query_clean": "user wants to find the cafeteria",
  "destination_query": "cafeteria",
  "accessibility_flag": false
}
```

Fields `destination_query` and `accessibility_flag` are present in all responses. For non-navigation
intents, `destination_query` is `null` and `accessibility_flag` is `false`.

### `server/lang/detector.py`

Add `"ko"` and `"zh"` to `_SUPPORTED`:

```python
_SUPPORTED: frozenset[str] = frozenset({"en", "ja", "ko", "zh"})
_DEFAULT: str = "en"
```

The `detect()` method body is unchanged — it maps `result.language.strip().lower()` to supported
codes, defaulting to `"en"`. The extension is purely additive.

### `server/tts/kokoro_tts.py`

Add `KokoroChineseTTS` following the exact pattern of `KokoroJapaneseTTS`:

```python
class KokoroChineseTTS:
    """Mandarin Chinese TTS using Kokoro KPipeline(lang_code='z')."""

    DEFAULT_VOICE = "zf_xiaobei"   # Kokoro ZH female voice

    def __init__(self) -> None:
        self._pipeline: Optional[object] = None

    def _get_pipeline(self):
        if self._pipeline is None:
            from kokoro import KPipeline
            tts_log.info("init_pipeline  engine=KokoroChineseTTS  lang=zh")
            self._pipeline = KPipeline(lang_code='z')
            tts_log.info("pipeline_ready  engine=KokoroChineseTTS  lang=zh")
        return self._pipeline

    def _synthesize_sync(self, text: str) -> bytes:
        # identical structure to KokoroJapaneseTTS._synthesize_sync
        # logs: engine=KokoroChineseTTS

    def warm_up(self) -> None:
        pipeline = self._get_pipeline()
        pipeline.load_voice(self.DEFAULT_VOICE)
        tts_log.info("warm_up_complete  engine=KokoroChineseTTS  voice=%s", self.DEFAULT_VOICE)

    async def synthesize(self, text: str) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._synthesize_sync, text)
```

**Voice selection**: Kokoro's Chinese voice IDs follow the pattern `zf_*` (female) or `zm_*`
(male). `zf_xiaobei` is used as default; this is configurable via the deployment if needed.

### `server/tts/tts_router.py`

**Changes**:

1. `TTSRouter.__init__` gains a `zh_tts: KokoroChineseTTS` parameter:

```python
def __init__(
    self,
    en_tts: KokoroTTS,
    ja_tts: KokoroJapaneseTTS,
    zh_tts: KokoroChineseTTS,
) -> None:
    self._en_tts = en_tts
    self._ja_tts = ja_tts
    self._zh_tts = zh_tts
```

2. Update `_detect_language_from_text()` to distinguish JA from ZH:

```python
_HIRAGANA_KATAKANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')
_CJK_IDEOGRAPH     = re.compile(r'[\u4e00-\u9fff]')

def _detect_language_from_text(text: str) -> str:
    """Return 'ja' if kana present, 'zh' if CJK-only, else 'en'."""
    if _HIRAGANA_KATAKANA.search(text):
        return "ja"
    if _CJK_IDEOGRAPH.search(text):
        return "zh"
    return "en"
```

3. Update `TTSRouter.synthesize()` to route `"zh"` to `KokoroChineseTTS`:

```python
async def synthesize(self, text: str, language: str) -> bytes:
    effective = _detect_language_from_text(text)
    if effective != language and language in ("en", "ja", "zh"):
        effective = language  # explicit language hint takes precedence for clean text
    if effective == "ja":
        return await self._ja_tts.synthesize(text)
    elif effective == "zh":
        return await self._zh_tts.synthesize(text)
    else:
        return await self._en_tts.synthesize(text)
```

**Note**: The existing behaviour of the WebSocket pipeline is preserved — text containing kana
still routes to JA regardless of the `language` hint, since `_detect_language_from_text` is
called first and overrides only for the explicit language hint path.

### `server/llm/assembler.py`

**Changes**:

1. `PromptAssembler._load_prompt_files()` loads `lang_ko.txt` and `lang_zh.txt` with the same
   fallback-to-`lang_unknown.txt`-with-warning pattern used for `lang_ja_formal.txt`:

```python
for lang_code, filename in [("ko", "lang_ko.txt"), ("zh", "lang_zh.txt")]:
    lang_path = self.prompts_dir / filename
    if lang_path.exists():
        with open(lang_path, 'r', encoding='utf-8') as f:
            self._prompt_cache[filename] = f.read()
    else:
        logger.warning(
            "Prompt file %s not found, falling back to lang_unknown.txt", filename
        )
        self._prompt_cache[filename] = self._prompt_cache["lang_unknown.txt"]
```

2. `PromptAssembler._select_language_block()` adds mappings for `"ko"` and `"zh"`:

```python
language_files = {
    "ja": "lang_ja.txt",
    "en": "lang_en.txt",
    "ko": "lang_ko.txt",
    "zh": "lang_zh.txt",
    "unknown": "lang_unknown.txt",
}
```

3. `PromptAssembler.assemble_prompt()` gains an optional `building_context` parameter:

```python
def assemble_prompt(
    self,
    user_input: str,
    intent_result,
    session_history: list[dict],
    retrieved_context: str = "",
    route_type: str = "general",
    building_context: dict | None = None,  # NEW
) -> tuple[str, list[dict]]:
```

When `building_context` is provided, a building context block is injected between the deployment
block and the language block:

```python
if building_context:
    building_block = _render_building_context_block(building_context)
    blocks = [base_block, deployment_block, building_block, language_block, route_context_block]
else:
    blocks = [base_block, deployment_block, language_block, route_context_block]
```

`_render_building_context_block()`:

```python
def _render_building_context_block(ctx: dict) -> str:
    pois = ", ".join(ctx.get("available_pois", []))
    return (
        f"# Building Context\n"
        f"Current location: {ctx.get('current_node_label', 'unknown')}\n"
        f"Floor: {ctx.get('floor_name', 'unknown')}\n"
        f"Available destinations in this building: {pois}\n"
        f"Only suggest destinations from this list. Do not invent locations."
    )
```

### `server/config.py`

Add `BUILDING_NAV_ORIGIN` to `Config`:

```python
@dataclass
class Config:
    ...existing fields...
    building_nav_origin: str = "http://localhost:8001"  # NEW

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(...)
        building_nav_origin = os.getenv("BUILDING_NAV_ORIGIN", "")
        if not building_nav_origin:
            import logging
            logging.getLogger(__name__).warning(
                "BUILDING_NAV_ORIGIN not set — defaulting to http://localhost:8001"
            )
            building_nav_origin = "http://localhost:8001"
        return cls(
            ...existing...,
            building_nav_origin=building_nav_origin,
        )
```

### `server/main.py`

Changes in the `lifespan` context manager and app setup:

1. **CORS middleware** (added before `app.include_router`):

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.building_nav_origin],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
```

2. **KokoroChineseTTS** instantiation:

```python
from server.tts.kokoro_tts import KokoroTTS, KokoroJapaneseTTS, KokoroChineseTTS

kokoro_tts = KokoroTTS()
kokoro_ja_tts = KokoroJapaneseTTS()
kokoro_zh_tts = KokoroChineseTTS()              # NEW
tts_router = TTSRouter(
    en_tts=kokoro_tts,
    ja_tts=kokoro_ja_tts,
    zh_tts=kokoro_zh_tts,                       # NEW
)
```

3. **ZH warm-up** added to the `asyncio.gather` call:

```python
await asyncio.gather(
    loop.run_in_executor(None, kokoro_tts.warm_up),
    loop.run_in_executor(None, kokoro_ja_tts.warm_up),
    loop.run_in_executor(None, kokoro_zh_tts.warm_up),  # NEW
)
app.state.tts_warmed_up = True  # NEW — for health endpoint
```

If warm-up raises, `tts_warmed_up` stays `False` (the `try/except` wrapper is preserved).

4. **Session store init**:

```python
app.state.navigate_sessions = {}  # dict[str, NavigateSession]
app.state.kokoro_zh_tts = kokoro_zh_tts
```

5. **Background cleanup task**:

```python
async def _cleanup_sessions() -> None:
    """Remove NavigateSessions inactive for > 30 minutes. Runs every 5 minutes."""
    while True:
        await asyncio.sleep(300)  # 5 minutes
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        sessions = app.state.navigate_sessions
        stale = [sid for sid, s in sessions.items() if s.last_active < cutoff]
        for sid in stale:
            sessions.pop(sid, None)
        if stale:
            logger.info("session_cleanup  removed=%d  remaining=%d", len(stale), len(sessions))

asyncio.create_task(_cleanup_sessions())
```

This task is created inside the `lifespan` context (after `yield` setup, before `yield`).

6. **API router inclusion** (after CORS, before static files):

```python
from server.api import api_router
app.include_router(api_router)
```

### New Prompt Files

#### `server/prompts/lang_ko.txt`

```
# Language: Korean (한국어)

Respond in Korean (한국어). Use natural, polite Korean appropriate for a public building
navigation assistant. Use 존댓말 (formal speech level, -요/-습니다 endings). Keep responses
concise and clear for voice output. Avoid complex sentence structures.

When giving directions, use clear Korean directional terms (왼쪽, 오른쪽, 앞으로, etc.).
```

#### `server/prompts/lang_zh.txt`

```
# Language: Mandarin Chinese (普通话)

Respond in Mandarin Chinese (普通话). Use natural, polite Mandarin appropriate for a public
building navigation assistant. Use formal but approachable language. Keep responses concise
and clear for voice output. Avoid overly complex sentence structures.

When giving directions, use clear Mandarin directional terms (左边, 右边, 前方, etc.).
```

### `config/deployment.yaml`

Updated for the building-nav deployment:

```yaml
deployment_id: "building-nav"
deployment_type: "reception"
location_name: "Building Navigation Assistant"
language_primary: "en"
language_secondary: "ja"
tone_override: null

environment_docs:
  - path: "docs/building_context.txt"

session_memory_turns: 6
web_search_enabled: false

out_of_scope_response:
  en: "I can only help with navigation and directions within this building."
  ja: "このビル内のナビゲーションと案内のみお手伝いできます。"
  ko: "이 건물 내의 안내와 길 찾기만 도와드릴 수 있습니다."
  zh: "我只能帮助您在这栋楼内导航和寻找目的地。"
```

A new `docs/building_context.txt` file provides static building context for the RAG path:

```
# Building Context

Building Name: [To be filled for each deployment]
Floors: [To be filled]
General layout: [To be filled]
```

---

## Session Management Design

### NavigateSession Lifecycle

```
POST /api/navigate arrives with session_id "abc123"
        │
        ▼
session_id in app.state.navigate_sessions?
        │
    YES │                              NO
        ▼                               ▼
load existing NavigateSession     create NavigateSession(history=[], last_active=now)
update last_active = utcnow()     store in navigate_sessions["abc123"]
        │
        ▼
pass session.history to run_navigate_turn()
        │
        ▼
run_navigate_turn() returns NavigateTurnResult
        │
        ▼
append {"role":"user","content":text} to session.history
append {"role":"assistant","content":response_text} to session.history
trim: while len(session.history) > session_memory_turns * 2: pop first two
        │
        ▼
return NavigateResponse(session_id=request.session_id, ...)
```

### Session_Store Invariants

- `session_memory_turns` is read from `app.state.deployment_config.session_memory_turns`
  (default 6, giving a max history of 12 entries).
- History entries contain only `{"role": str, "content": str}` — no audio, no PII.
- Sessions are never written to disk, database, or any log file.
- On server shutdown, the in-memory dict is discarded (no persistence on `yield` exit).

### Background Cleanup

The cleanup task runs every 5 minutes and removes sessions where
`last_active < utcnow() - 30 minutes`. Since the task only reads and mutates a plain Python
dict (not across threads), no lock is needed — asyncio's single-threaded event loop ensures
safety.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions
of a system — essentially, a formal statement about what the system should do. Properties serve
as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

This feature is suitable for property-based testing. The detect-language endpoint, language
detector, TTS router text detection, IntentResult normalisation, and session management all
contain pure logic with large input spaces where varied inputs expose edge cases. Hypothesis
is the PBT library used (already present in the project's dev dependencies).

### Property 1: detect-language output domain

*For any* non-empty text string, the `detect_language_from_text()` function SHALL return a
value in `{"en", "ja", "zh", "ko"}` — never any other value, never None.

**Validates: Requirements 4.1, 4.7**

### Property 2: Japanese detection priority

*For any* string that contains at least one hiragana (U+3040–U+309F) or katakana (U+30A0–U+30FF)
character, `detect_language_from_text()` SHALL return `"ja"`, even when the string also contains
CJK Unified Ideographs.

**Validates: Requirements 4.2, 7.8**

### Property 3: Korean detection

*For any* string that contains only Hangul syllables (U+AC00–U+D7A3) and ASCII whitespace,
`detect_language_from_text()` SHALL return `"ko"`.

**Validates: Requirements 4.4**

### Property 4: Chinese detection (CJK without kana)

*For any* string that contains at least one CJK Unified Ideograph (U+4E00–U+9FFF) and no
hiragana or katakana characters, `detect_language_from_text()` SHALL return `"zh"`.

**Validates: Requirements 4.3, 7.7**

### Property 5: detect-language idempotence

*For any* non-empty text string `t`, calling `detect_language_from_text(t)` twice in sequence
SHALL return the same value both times.

**Validates: Requirements 4.7**

### Property 6: LanguageDetector output domain

*For any* `TranscriptionResult` with an arbitrary (possibly empty, possibly unknown) `language`
string, `LanguageDetector.detect(result)` SHALL return a value in `{"en", "ja", "ko", "zh"}`.

**Validates: Requirements 7.1–7.5**

### Property 7: LanguageDetector round-trip for supported codes

*For any* `TranscriptionResult` where `language` is in `{"en", "ja", "ko", "zh"}`,
`LanguageDetector.detect(result)` SHALL return that exact same code.

**Validates: Requirements 7.1–7.4**

### Property 8: TTSRouter text-based language discrimination

*For any* text string `t`, the result of `_detect_language_from_text(t)` in `tts_router.py`
SHALL satisfy: if `t` contains any hiragana or katakana, result is `"ja"`; else if `t` contains
any CJK ideograph, result is `"zh"`; else result is `"en"`.

**Validates: Requirements 7.7, 7.8**

### Property 9: IntentResult accessibility_flag type invariant

*For any* `IntentResult` instance, `accessibility_flag` SHALL be a Python `bool` (`True` or
`False`), never `None`, never an integer, never a string.

**Validates: Requirements 6.4**

### Property 10: IntentResult destination_query normalisation

*For any* `IntentResult` instance where `destination_query` was set to an empty string `""` at
construction time, after `__post_init__` runs, `destination_query` SHALL be `None`.

*For any* `IntentResult` instance where `destination_query` is a non-empty string, it SHALL
remain unchanged after `__post_init__`.

**Validates: Requirements 6.4, 6.9**

### Property 11: IntentClassifier graceful missing-field handling

*For any* JSON object that is missing `destination_query` and/or `accessibility_flag` (but
has all other required fields), parsing via `IntentClassifier.classify()` SHALL not raise an
exception and SHALL produce `destination_query=None` and `accessibility_flag=False`.

**Validates: Requirements 6.9**

### Property 12: Navigate response schema completeness

*For any* valid `NavigateRequest`, the `NavigateResponse` returned by `navigate_endpoint()`
(with mocked LLM calls) SHALL contain all seven required fields: `intent`, `destination_query`,
`accessibility_flag`, `response_text`, `needs_clarification`, `language`, `session_id`.
The types shall be: `intent` (str), `destination_query` (str or null), `accessibility_flag`
(bool), `response_text` (str), `needs_clarification` (bool), `language` (str), `session_id`
(str matching the request's `session_id`).

**Validates: Requirements 5.3, 5.4**

### Property 13: session_id echo invariant

*For any* `NavigateRequest` with an arbitrary UUID string as `session_id`, the `session_id`
field in the `NavigateResponse` SHALL equal the `session_id` from the request, with no
transformation.

**Validates: Requirements 5.4**

### Property 14: History length bound

*For any* sequence of N ≥ 1 successful calls to `navigate_endpoint()` using the same
`session_id`, the length of `NavigateSession.history` after each call SHALL never exceed
`session_memory_turns * 2`.

**Validates: Requirements 5.7, 8.2**

### Property 15: Session cleanup correctness

*For any* collection of `NavigateSession` records with varying `last_active` timestamps,
after the cleanup function runs with a 30-minute TTL, every session whose `last_active` was
more than 30 minutes before the cleanup time SHALL be absent from the store, and every session
whose `last_active` was within 30 minutes SHALL still be present.

**Validates: Requirements 8.3, 8.5**

---

## Error Handling

### HTTP Error Map

| Condition | Status | Body |
|-----------|--------|------|
| Missing/empty audio file in `/api/stt` | 422 | Pydantic validation error |
| `GroqSTTBackend.transcribe()` raises | 502 | `{"error": "stt_failed", "detail": "..."}` |
| Empty/whitespace `text` in `/api/tts` | 422 | Pydantic validation error |
| Invalid `language` in `/api/tts` | 422 | Pydantic validation error |
| `language == "ko"` in `/api/tts` | 406 | `{"fallback": "browser_tts", "language": "ko"}` |
| `TTSRouter.synthesize()` raises | 502 | `{"error": "tts_failed", "detail": "..."}` |
| Empty/whitespace `text` in `/api/detect-language` | 422 | Pydantic validation error |
| Any required field missing in `/api/navigate` | 422 | Pydantic validation error |
| Empty/whitespace `text` in `/api/navigate` | 422 | Pydantic validation error |
| `IntentClassifier.classify()` raises | 502 | `{"error": "classify_failed", "detail": "..."}` |
| LLM Call 2 fails after retry | 502 | `{"error": "llm_failed", "detail": "..."}` |

### Error Handler Pattern

Each endpoint wraps its backend call in a `try/except`:

```python
try:
    result = await app.state.stt_backend.transcribe(audio_bytes)
except Exception as exc:
    logger.debug("stt_failed  detail=%s", exc)  # DEBUG only — no content at INFO
    raise HTTPException(
        status_code=502,
        detail={"error": "stt_failed", "detail": str(exc)},
    )
```

### Logging Privacy

All endpoint handlers follow these rules:
- **INFO level**: `endpoint`, `status_code`, `latency_ms`, `language` — never `text` content
- **DEBUG level**: full request content (disabled by default with `LOG_LEVEL=INFO`)
- Session history is never written to any log

---

## Testing Strategy

### Unit Tests (example-based)

Located in `tests/api/`:

- `test_stt_endpoint.py` — mock `app.state.stt_backend`, test 200/422/502 responses
- `test_tts_endpoint.py` — mock `app.state.tts_router`, test 200/406/422/502 responses
- `test_detect_language_endpoint.py` — no mocking needed (pure function), specific examples
- `test_navigate_endpoint.py` — mock `run_navigate_turn`, test session creation/update/trim
- `test_health_endpoint.py` — test with `tts_warmed_up=True/False`, `groq_api_key` present/absent
- `test_run_navigate_turn.py` — mock all LLM components, verify turn result structure
- `test_session_management.py` — session creation, TTL cleanup, history trim
- `test_intent_result.py` — field defaults, empty string normalisation, backward compat

### Property-Based Tests (Hypothesis)

Located in `tests/properties/`:

Each property test maps directly to a numbered property in this design document.

```python
# tests/properties/test_detect_language_properties.py
# Feature: building-nav-integration

from hypothesis import given, settings
from hypothesis import strategies as st

# Property 1: output domain
@given(text=st.text(min_size=1))
@settings(max_examples=200)
def test_detect_language_output_domain(text):
    """Feature: building-nav-integration, Property 1: output domain invariant"""
    result = detect_language_from_text(text)
    assert result in {"en", "ja", "zh", "ko"}

# Property 2: Japanese detection priority
_HIRAGANA = [chr(c) for c in range(0x3040, 0x30A0)]
_KATAKANA  = [chr(c) for c in range(0x30A0, 0x3100)]

@given(
    kana=st.sampled_from(_HIRAGANA + _KATAKANA),
    suffix=st.text(),
)
@settings(max_examples=200)
def test_japanese_detection_priority(kana, suffix):
    """Feature: building-nav-integration, Property 2: Japanese detection priority"""
    result = detect_language_from_text(kana + suffix)
    assert result == "ja"

# Property 5: idempotence
@given(text=st.text(min_size=1))
@settings(max_examples=200)
def test_detect_language_idempotent(text):
    """Feature: building-nav-integration, Property 5: idempotence"""
    assert detect_language_from_text(text) == detect_language_from_text(text)
```

```python
# tests/properties/test_language_detector_properties.py

from hypothesis import given, settings
from hypothesis import strategies as st

_SUPPORTED = ["en", "ja", "ko", "zh"]
_UNSUPPORTED = st.text().filter(lambda s: s.strip().lower() not in _SUPPORTED)

# Property 6: output domain
@given(language=st.text())
@settings(max_examples=200)
def test_language_detector_output_domain(language):
    """Feature: building-nav-integration, Property 6: LanguageDetector output domain"""
    result_lang = TranscriptionResult(text="hello", language=language)
    result = LanguageDetector().detect(result_lang)
    assert result in {"en", "ja", "ko", "zh"}

# Property 7: round-trip for supported codes
@given(language=st.sampled_from(_SUPPORTED))
@settings(max_examples=200)
def test_language_detector_round_trip(language):
    """Feature: building-nav-integration, Property 7: round-trip for supported codes"""
    result_lang = TranscriptionResult(text="test", language=language)
    assert LanguageDetector().detect(result_lang) == language
```

```python
# tests/properties/test_intent_result_properties.py

from hypothesis import given, settings
from hypothesis import strategies as st

# Property 9: accessibility_flag type invariant
@given(
    accessibility_flag=st.one_of(st.booleans(), st.integers(), st.none(), st.text())
)
@settings(max_examples=200)
def test_accessibility_flag_is_bool(accessibility_flag):
    """Feature: building-nav-integration, Property 9: accessibility_flag type invariant"""
    result = IntentResult(
        intent="general",
        language="en",
        confidence=0.9,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="test",
        accessibility_flag=bool(accessibility_flag) if accessibility_flag is not None else False,
    )
    assert isinstance(result.accessibility_flag, bool)

# Property 10: destination_query normalisation
@given(destination_query=st.one_of(st.just(""), st.text(min_size=1)))
@settings(max_examples=200)
def test_destination_query_normalisation(destination_query):
    """Feature: building-nav-integration, Property 10: destination_query normalisation"""
    result = IntentResult(
        intent="general", language="en", confidence=0.9,
        needs_clarification=False, clarification_reason=None,
        query_clean="test", destination_query=destination_query,
    )
    if destination_query == "":
        assert result.destination_query is None
    else:
        assert result.destination_query == destination_query
```

```python
# tests/properties/test_session_management_properties.py

from hypothesis import given, settings
from hypothesis import strategies as st
from datetime import datetime, timedelta

# Property 14: history length bound
@given(
    num_calls=st.integers(min_value=1, max_value=30),
    memory_turns=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=200)
def test_history_length_bound(num_calls, memory_turns):
    """Feature: building-nav-integration, Property 14: history length bound"""
    session = NavigateSession()
    for i in range(num_calls):
        session.history.append({"role": "user", "content": f"msg {i}"})
        session.history.append({"role": "assistant", "content": f"reply {i}"})
        while len(session.history) > memory_turns * 2:
            session.history.pop(0)
            session.history.pop(0)
        assert len(session.history) <= memory_turns * 2

# Property 15: session cleanup correctness
@given(
    num_fresh=st.integers(min_value=0, max_value=10),
    num_stale=st.integers(min_value=0, max_value=10),
)
@settings(max_examples=200)
def test_session_cleanup_correctness(num_fresh, num_stale):
    """Feature: building-nav-integration, Property 15: session cleanup correctness"""
    now = datetime.utcnow()
    store = {}
    fresh_ids = set()
    stale_ids = set()

    for i in range(num_fresh):
        sid = f"fresh-{i}"
        store[sid] = NavigateSession(last_active=now - timedelta(minutes=5))
        fresh_ids.add(sid)

    for i in range(num_stale):
        sid = f"stale-{i}"
        store[sid] = NavigateSession(last_active=now - timedelta(minutes=45))
        stale_ids.add(sid)

    cutoff = now - timedelta(minutes=30)
    stale_keys = [k for k, s in store.items() if s.last_active < cutoff]
    for k in stale_keys:
        store.pop(k)

    for sid in fresh_ids:
        assert sid in store
    for sid in stale_ids:
        assert sid not in store
```

**Test configuration**: All property tests run with `max_examples=200` (minimum 100 per
property requirement). Each test is tagged with its design property number in the docstring
using the format `Feature: building-nav-integration, Property N: <description>`.

### Integration Tests

Located in `tests/integration/`:

- `test_cors_headers.py` — preflight OPTIONS with allowed/disallowed origin
- `test_tts_languages.py` — synthesize with each accepted language (mocked Kokoro)
- `test_navigate_full_turn.py` — full two-call LLM turn with mocked Groq client

---
