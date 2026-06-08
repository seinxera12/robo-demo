# Robo-BN Integration — Implementation Design
### Audit-based design for building-nav integration

**Source plan:** `.kiro/specs/robo-bn-integration/plan.md`  
**Codebase audited:** `server/` (all modules read in full)  
**Env file audited:** `.env`, `.env.example`  
**Author:** Kiro  
**Status:** Ready for implementation

---

## 1. Executive Summary — What the Audit Found

The codebase audit reveals that **the integration is substantially already implemented**. This is not a greenfield build. The architects who branched `robo-demo` into `robo-bn` have already done the structural work. The design task is therefore to identify the **gaps, bugs, and missing details** precisely, not to describe a full rewrite.

### Already implemented and conformant

| Plan requirement | Implementation location | Status |
|---|---|---|
| `POST /api/stt` endpoint | `server/api/stt.py` | ✅ Complete |
| `POST /api/navigate` endpoint | `server/api/navigate.py` | ✅ Complete |
| `POST /api/tts` endpoint | `server/api/tts.py` | ✅ Complete |
| Korean 406 fallback | `server/api/tts.py` | ✅ Complete |
| Intent classification (5 types + navigation + accessibility_request) | `server/llm/intent.py` | ✅ Complete |
| `destination_query` + `accessibility_flag` extraction | `server/llm/intent.py`, `IntentResult` | ✅ Complete |
| Session management (in-memory, 30-min TTL, background cleanup) | `server/api/navigate.py`, `server/main.py` | ✅ Complete |
| `building_context` injection into LLM prompt | `server/llm/assembler.py` (`_render_building_context_block`) | ✅ Complete |
| Multi-turn history passed to LLM | `server/api/navigate.py` + `server/llm/assembler.py._trim_history` | ✅ Complete |
| CORS middleware for `BUILDING_NAV_ORIGIN` | `server/main.py`, `server/config.py` | ✅ Complete |
| `BUILDING_NAV_ORIGIN` env var with startup warning | `server/config.py` | ✅ Complete |
| Privacy: no user content in INFO logs | All three API files | ✅ Complete |
| Two Kokoro engines (EN + JA) | `server/tts/kokoro_tts.py`, `server/tts/tts_router.py` | ✅ Complete |
| Chinese TTS (ZH) engine | `server/tts/kokoro_tts.py` (`KokoroChineseTTS`) | ✅ Complete |
| Language detection via Whisper output | `server/lang/detector.py` | ✅ Complete |
| Test suite for all three endpoints | `server/tests/api/test_stt_endpoint.py`, `test_tts_endpoint.py`, `test_navigate_endpoint.py` | ✅ Complete |

### Gaps and issues found by audit

| ID | Severity | Location | Description |
|---|---|---|---|
| G-1 | **Critical** | `server/stt/groq_stt.py` | STT backend wraps bytes in PCM16→WAV always, but building-nav sends `audio/webm` blobs. `webm` cannot be frame-written as PCM16 — transcription will silently fail or produce garbage. |
| G-2 | **High** | `server/lang/detector.py` | No ISO 639-2 → ISO 639-1 normalisation. If Whisper returns `"eng"` or `"jpn"`, the detector falls back to `"en"` rather than correctly detecting the language. |
| G-3 | **High** | `server/api/navigate.py` | The `BuildingContext` model does not accept the `poi_not_found` and `query` fields from the clarification variant request (§5.1 of plan). Those fields are silently dropped, so the LLM never receives the clarification signal. |
| G-4 | **Medium** | `server/api/navigate.py` | The clarification variant's `text` from building-nav ("Clarify: The user wants 'blue section', but no POIs match this query.") is passed straight to the intent classifier (Call 1), which will classify it as `navigation` with `destination_query="blue section"` again — causing an infinite loop. Need a special handling path when `poi_not_found=true`. |
| G-5 | **Medium** | `.env.example` / `.env` | `BUILDING_NAV_ORIGIN` is present but the server listens on port `8000`, not `8001`. The plan says Robo-BN must listen on `8001`. `SERVER_PORT` defaults to `8000`. This mismatch means building-nav (which calls `ROBO_BN_URL=http://localhost:8001`) won't connect in the default dev config. |
| G-6 | **Medium** | `server/api/stt.py` | ISO 639-1 normalisation is not applied to the Whisper language output before returning in `STTResponse`. The raw Whisper `language` string is returned directly. |
| G-7 | **Low** | `server/lang/detector.py` | The `detect()` method only handles `en`, `ja`, `ko`, `zh`. ISO 639-2 codes like `eng`, `jpn`, `zho`, `kor` are not in `_SUPPORTED` and fall back silently to `en`. |
| G-8 | **Low** | `server/prompts/classifier.txt` | Cannot confirm whether the classifier prompt instructs the LLM to keep `destination_query` as a short noun phrase vs. a full sentence. This needs to be verified and amended if necessary. |
| G-9 | **Low** | `.env` / `.env.example` | `BUILDING_NAV_ORIGIN` is set to empty string in both files. The startup warning fires every time even in dev. Should default-populate to `http://localhost:8001` in `.env.example` to guide users. |

---

## 2. Architecture — Current State vs. Required State

### How the three endpoints already work (correct behaviour)

```
building-nav backend
  │
  ├─ POST /api/stt  ──────────────────────────────────────────────────────┐
  │    file: audio bytes (multipart)                                       │
  │    → stt_endpoint() reads bytes → stt_backend.transcribe(bytes)       │
  │      → GroqSTTBackend wraps bytes as WAV → Groq Whisper API           │  ← G-1: wrong wrapping
  │      → returns TranscriptionResult(text, language)                    │  ← G-6, G-7: no 639-1 normalisation
  │    ← {text: str, language: str}                                        │
  │                                                                        │
  ├─ POST /api/navigate  ─────────────────────────────────────────────────┐
  │    body: {text, language, session_id, building_context}               │
  │    → navigate_endpoint() looks up / creates NavigateSession           │
  │    → run_navigate_turn(text, language, history, building_context, ...) │
  │        → IntentClassifier.classify(text)  [Call 1]                    │  ← G-3, G-4: clarification not handled
  │        → Router.route(intent_result, language)                        │
  │        → PromptAssembler.assemble_prompt(..., building_context)       │
  │        → LLMChain.stream(messages)  [Call 2]                          │
  │        → PostProcessor.clean(raw_response)                            │
  │    → updates NavigateSession.history                                  │
  │    ← {intent, destination_query, accessibility_flag, response_text,   │
  │        needs_clarification, language, session_id}                     │
  │                                                                        │
  └─ POST /api/tts  ──────────────────────────────────────────────────────┐
       body: {text, language}
       → tts_endpoint(): if language=="ko" → 406 immediately
       → TTSRouter.synthesize(text, language)
       ← audio/wav bytes (200) or 406 for ko
```

### What changes are needed (the six targeted fixes)

```
Fix 1 (G-1):  GroqSTTBackend.transcribe() — detect audio format; pass webm/ogg
              directly to Groq rather than wrapping as WAV PCM16.

Fix 2 (G-2,G-6,G-7): Add ISO 639-1 normalisation — one utility function,
              called in GroqSTTBackend before returning, and in LanguageDetector.

Fix 3 (G-3,G-4): Extend BuildingContext + navigate_endpoint to handle the
              clarification variant (poi_not_found: true). Bypass Call 1
              entirely and force the LLM (Call 2) to generate a clarification
              response without a destination_query.

Fix 4 (G-5):  Update .env.example SERVER_PORT default comment and BUILDING_NAV_ORIGIN
              example to reflect correct Robo-BN port (8001).

Fix 5 (G-8):  Verify/update classifier.txt prompt to enforce short noun phrase
              for destination_query extraction.

Fix 6 (G-9):  Set BUILDING_NAV_ORIGIN=http://localhost:8001 in .env.example
              as a sensible default.
```

---

## 3. Detailed Design per Fix

### Fix 1 — Audio Format Handling in STT (`server/stt/groq_stt.py`)

**Problem:** `GroqSTTBackend.transcribe()` always wraps the incoming bytes with Python's `wave.open(..., "wb")` as PCM16 at 16kHz. When the bytes are actually `audio/webm` (from `MediaRecorder`), this produces a malformed WAV file. The Groq API may reject it or produce an empty/incorrect transcript.

**Root cause:** The original `robo-demo` project used a binary PCM16 WebSocket stream from a native audio client, where wrapping as WAV was correct. The building-nav integration path sends pre-encoded `audio/webm` bytes decoded from base64.

**Design:**

The STT endpoint receives the audio as an `UploadFile`. The `file.content_type` and `file.filename` carry the format hint from the caller. The building-nav backend sends:
```python
files={"file": (bytes, "audio.wav", "audio/wav")}
```
However, the bytes are actually webm-encoded. The content-type in the multipart header is therefore unreliable.

Instead, implement **magic-byte sniffing** in `GroqSTTBackend` to detect the true format:

| Magic bytes (hex) | Format |
|---|---|
| `1A 45 DF A3` | WebM / MKV (EBML header) |
| `4F 67 67 53` ("OggS") | Ogg/Opus |
| `52 49 46 46` ("RIFF") | WAV |
| `FF FB`, `FF F3`, `FF F2` | MP3 |
| Anything else | Treat as PCM16 (legacy path) |

**Implementation plan:**

1. Add a `_detect_audio_format(bytes) -> str` helper function that returns `"webm"`, `"ogg"`, `"wav"`, `"mp3"`, or `"pcm16"` based on the first 4 bytes.
2. Modify `GroqSTTBackend.transcribe()`:
   - Call `_detect_audio_format(pcm16_bytes)` (rename param to `audio_bytes`)
   - If format is `"pcm16"`: use existing WAV wrapping path (backward compatible for WebSocket pipeline)
   - If format is `"webm"`, `"ogg"`, `"wav"`, `"mp3"`: wrap in `io.BytesIO`, set `.name` to `"audio.<ext>"`, pass directly to Groq API without re-encoding
3. Update the parameter name from `pcm16_bytes` to `audio_bytes` across the call chain (stt_endpoint → transcribe)
4. Update `stt_log` statements to include `format=` field

**Backward compatibility:** The WebSocket pipeline at `/ws` sends raw PCM16 bytes. The magic bytes of raw PCM16 audio are not a known header (they start with audio sample data). The `"pcm16"` detection branch must remain, so the existing `VoicePipeline` path is unaffected.

**Files affected:**
- `server/stt/groq_stt.py` — add `_detect_audio_format`, modify `transcribe()`
- `server/tests/api/test_stt_endpoint.py` — add test for `audio/webm` input (magic bytes `\x1a\x45\xdf\xa3`)

---

### Fix 2 — ISO 639-1 Normalisation (`server/lang/detector.py`, `server/stt/groq_stt.py`)

**Problem:** The plan requires that ISO 639-2 codes from Whisper be normalised to ISO 639-1. Currently `LanguageDetector.detect()` uses exact string match against `{"en", "ja", "ko", "zh"}`. If Whisper returns `"eng"`, `"jpn"`, `"zho"`, or `"kor"`, the detector silently falls back to `"en"` instead of correctly identifying the language.

The Groq Whisper API documentation confirms it can return either form depending on context.

**Design:**

Add a `_NORMALISE_MAP` dict to `server/lang/detector.py`:

```python
_NORMALISE_MAP: dict[str, str] = {
    # ISO 639-2 → ISO 639-1
    "eng": "en", "jpn": "ja", "kor": "ko",
    "zho": "zh", "cmn": "zh", "chi": "zh",
    # Common Whisper variants
    "zh-cn": "zh", "zh-tw": "zh",
    "ja-jp": "ja", "ko-kr": "ko",
    "en-us": "en", "en-gb": "en",
}
```

Update `LanguageDetector.detect()`:
1. Strip and lowercase the code as before
2. If code is in `_NORMALISE_MAP`, map it first
3. Then apply the `_SUPPORTED` lookup with `_DEFAULT` fallback

Additionally, apply normalisation at the `STTResponse` boundary in `server/api/stt.py` (belt-and-suspenders):
- After receiving `TranscriptionResult` from `stt_backend.transcribe()`, run the language field through a `normalise_language(lang: str) -> str` utility before building the response

**Files affected:**
- `server/lang/detector.py` — add `_NORMALISE_MAP`, update `detect()`
- `server/api/stt.py` — import and apply normalisation before returning `STTResponse`
- `server/tests/api/test_stt_endpoint.py` — add parametrised test cases for ISO 639-2 inputs (`"eng"` → `"en"`, `"jpn"` → `"ja"`)

**Normalisation utility location:** Define `normalise_language(lang: str) -> str` in `server/lang/detector.py` as a module-level function (not on the class) so it can be imported without instantiating the class.

---

### Fix 3 — Clarification Variant Handling (`server/api/navigate.py`)

**Problem (G-3):** The `BuildingContext` Pydantic model only has three fields:
```python
class BuildingContext(BaseModel):
    current_node_label: str
    available_pois: list[str]
    floor_name: str
```
When building-nav sends the clarification variant with `poi_not_found: true` and `query: "blue section"`, those extra fields are silently discarded by Pydantic. The LLM receives no signal that this is a clarification turn.

**Problem (G-4):** Even if the fields were received, the current code passes the clarification text straight into `intent_classifier.classify()`. The classifier would see `"Clarify: The user wants 'blue section', but no POIs match"` and return `intent="navigation", destination_query="blue section"` — triggering the same POI lookup again. This is an infinite loop.

**Design:**

**Step 1 — Extend `BuildingContext`:**

```python
class BuildingContext(BaseModel):
    current_node_label: str
    available_pois: list[str]
    floor_name: str
    # Clarification variant fields (optional, sent only on second call)
    poi_not_found: bool = False
    query: str | None = None
```

Using `= False` / `= None` defaults preserves backward compatibility with all existing callers.

**Step 2 — Short-circuit in `navigate_endpoint` when `poi_not_found=True`:**

When `poi_not_found` is true, the intent is already determined by building-nav: it's a `clarify` turn with no new destination. Bypassing Call 1 entirely avoids the classification loop and saves ~1s of latency.

Add a branch in `navigate_endpoint` (before calling `run_navigate_turn`):

```python
if body.building_context.poi_not_found:
    result = await run_clarification_turn(
        original_query=body.building_context.query or "",
        available_pois=body.building_context.available_pois,
        language=body.language,
        session_history=list(session.history),
        prompt_assembler=...,
        llm_chain=...,
        post_processor=...,
    )
```

**Step 3 — Add `run_clarification_turn()` function to `server/api/navigate.py`:**

A standalone async function (mirrors `run_navigate_turn` pattern) that:
1. Skips Call 1 (intent classification) entirely — the intent is hardcoded to `"clarify"`
2. Builds a synthetic `IntentResult`-equivalent with `intent="clarify"`, `destination_query=None`, `accessibility_flag=False`
3. Calls `prompt_assembler.assemble_prompt()` with `route_type="clarify"` and the full building context including the failed query
4. Calls `llm_chain.stream()` to generate a natural-language response suggesting alternatives from `available_pois`
5. Returns `NavigateTurnResult(intent="clarify", destination_query=None, accessibility_flag=False, needs_clarification=True, ...)`

**The clarification prompt block** (to be added to `PromptAssembler._get_route_context_block`):

```python
"clarify": (
    "# Task\n"
    "The user asked for '{failed_query}' but no matching location was found.\n"
    "Apologise briefly and offer 2-3 of the closest-matching options from the "
    "Available destinations list. Keep it conversational and brief (2 sentences max).\n"
    "Do NOT suggest destinations not in the Available destinations list."
)
```

The `failed_query` value is injected before calling the assembler, either via a synthesised `IntentResult.query_clean` field or a custom route_type context.

**Files affected:**
- `server/api/navigate.py` — extend `BuildingContext`, add `run_clarification_turn()`, branch in `navigate_endpoint`
- `server/llm/assembler.py` — add `"clarify"` route context template to `ROUTE_CONTEXTS`
- `server/tests/api/test_navigate_endpoint.py` — add tests for the clarification variant path

---

### Fix 4 — Server Port Configuration (`.env.example`, `.env`)

**Problem (G-5):** The plan specifies Robo-BN listens on port `8001`. `SERVER_PORT` currently defaults to `8000` in both the code (`Config.from_env`) and the `.env.example`. Building-nav sets `ROBO_BN_URL=http://localhost:8001` by default. This means in the default dev config, building-nav calls port 8001 but Robo-BN listens on 8000.

**Design:**

Two changes:

1. Update `.env.example` — change the `SERVER_PORT` default example from `8000` to `8001` and add a comment explaining this is the Robo-BN port:

```ini
# Port for the FastAPI HTTP server (Robo-BN listens here; building-nav calls this port)
# Default: 8001 — matches building-nav's default ROBO_BN_URL=http://localhost:8001
SERVER_PORT=8001
```

2. Update `.env` accordingly for the local development environment.

**Note:** The code default in `Config.from_env()` (`os.getenv("SERVER_PORT", "8000")`) should also be updated to `"8001"` for consistency. This is a one-line change in `server/config.py`.

**Files affected:**
- `.env.example` — update `SERVER_PORT` and `BUILDING_NAV_ORIGIN` lines
- `.env` — update `SERVER_PORT` to `8001`
- `server/config.py` — update `os.getenv("SERVER_PORT", "8000")` default to `"8001"`

---

### Fix 5 — Classifier Prompt Enforcement of Short Noun Phrase

**Problem (G-8):** The classifier prompt at `server/prompts/classifier.txt` must instruct the LLM to set `destination_query` as a **short noun phrase** (e.g. `"cafeteria"`, `"elevator bank"`) rather than a sentence. This is critical because building-nav uses the value verbatim in a SQL LIKE query.

**Action:** Read the classifier prompt file and check if this constraint is already stated. If not, add a constraint line to the `destination_query` field description in the JSON schema section of the prompt.

The expected addition (to be added to the destination_query description in the JSON schema section):

```
"destination_query": "Short noun phrase (1-4 words max) naming the destination.
  NOT a full sentence. Will be used in a database search. 
  Example good: 'cafeteria', 'elevator bank', 'conference room a'.
  Example bad: 'I want to go to the cafeteria on the ground floor'.
  null if intent is not navigation or accessibility_request."
```

**Files affected:**
- `server/prompts/classifier.txt` — add noun phrase constraint to `destination_query` field description

---

### Fix 6 — `.env.example` `BUILDING_NAV_ORIGIN` Default

**Problem (G-9):** `BUILDING_NAV_ORIGIN` is blank in both files, which causes the startup warning to fire on every dev startup even though `http://localhost:8001` is the correct value for local development.

**Design:**

Update `.env.example`:
```ini
# CORS origin allowed for building-nav backend server-to-server calls.
# Set this to the URL of the building-nav FastAPI backend.
BUILDING_NAV_ORIGIN=http://localhost:8001
```

Update `.env` similarly.

**Files affected:**
- `.env.example` — set `BUILDING_NAV_ORIGIN=http://localhost:8001`
- `.env` — set `BUILDING_NAV_ORIGIN=http://localhost:8001`

---

## 4. Non-Changes — What Must NOT Be Modified

These existing implementations are already correct. Do not alter them.

| Component | Why it must not change |
|---|---|
| `server/api/tts.py` — Korean 406 logic | Exact contract match with plan (§4.3). Already tested. |
| `server/api/tts.py` — binary WAV response | `Response(content=wav_bytes, media_type="audio/wav")` is exactly what building-nav's `StreamingResponse` proxy expects. |
| `server/api/navigate.py` — `NavigateResponse` fields | All 7 required fields are present. Adding fields would be additive and safe; removing any would break building-nav. |
| Session cleanup task in `server/main.py` | Already correct: 5-min interval, 30-min TTL, in-memory only. |
| Privacy logging in all three API files | `logger.info` lines already exclude text content. Do not add any log that includes `body.text`, `result.response_text`, or `body.session_id` at INFO level. |
| `server/llm/assembler.py` — `_render_building_context_block` | Already injects `current_node_label`, `available_pois`, `floor_name` correctly. |
| `server/tts/tts_router.py` — language routing | EN/JA/ZH routing is correct. Korean is handled at the endpoint layer before `synthesize()` is called. |
| CORS middleware registration | Already reads `BUILDING_NAV_ORIGIN` correctly from config. |
| `server/api/__init__.py` — router registration | All three endpoint routers are already included under `/api` prefix. |

---

## 5. File Change Map

| File | Change type | Fixes | Risk |
|---|---|---|---|
| `server/stt/groq_stt.py` | Modify — add format detection + conditional path | G-1 | Medium — core audio path; tested by existing + new tests |
| `server/lang/detector.py` | Modify — add normalisation map + utility function | G-2, G-7 | Low — additive; backward compatible |
| `server/api/stt.py` | Modify — apply normalisation to response language | G-6 | Low — one-line change |
| `server/api/navigate.py` | Modify — extend `BuildingContext`, add `run_clarification_turn`, add branch | G-3, G-4 | Medium — new code path; requires careful testing |
| `server/llm/assembler.py` | Modify — add `"clarify"` route context template | G-3, G-4 | Low — additive |
| `server/prompts/classifier.txt` | Modify — add noun phrase constraint to JSON schema | G-8 | Low — prompt-only; LLM response will improve |
| `.env.example` | Modify — `SERVER_PORT=8001`, `BUILDING_NAV_ORIGIN=http://localhost:8001` | G-5, G-9 | None |
| `.env` | Modify — `SERVER_PORT=8001`, `BUILDING_NAV_ORIGIN=http://localhost:8001` | G-5, G-9 | None |
| `server/config.py` | Modify — default `SERVER_PORT` from `"8000"` to `"8001"` | G-5 | Low — one character change |
| `server/tests/api/test_stt_endpoint.py` | Modify — add webm format test, add ISO 639-2 normalisation test | G-1, G-2 | None |
| `server/tests/api/test_navigate_endpoint.py` | Modify — add clarification variant tests | G-3, G-4 | None |

---

## 6. Detailed Implementation Specs

### 6.1 `GroqSTTBackend.transcribe()` — Audio Format Detection

```python
# Magic byte signatures for format detection
_AUDIO_MAGIC: list[tuple[bytes, str]] = [
    (b'\x1a\x45\xdf\xa3', 'webm'),   # WebM/MKV EBML header
    (b'OggS',             'ogg'),    # Ogg container (Opus/Vorbis)
    (b'RIFF',             'wav'),    # WAV RIFF header
    (b'\xff\xfb',         'mp3'),    # MP3 (MPEG sync + flags)
    (b'\xff\xf3',         'mp3'),
    (b'\xff\xf2',         'mp3'),
    (b'fLaC',             'flac'),   # FLAC
]

def _detect_audio_format(audio_bytes: bytes) -> str:
    """Detect audio container format from magic bytes.
    Returns one of: 'webm', 'ogg', 'wav', 'mp3', 'flac', 'pcm16'.
    """
    for magic, fmt in _AUDIO_MAGIC:
        if audio_bytes[:len(magic)] == magic:
            return fmt
    return 'pcm16'  # no known header → assume raw PCM16 (legacy WebSocket path)
```

Modified `transcribe()` signature and body:

```python
async def transcribe(self, audio_bytes: bytes) -> TranscriptionResult:
    fmt = _detect_audio_format(audio_bytes)
    stt_log.info("transcribe_start  model=%s  audio_kb=%d  format=%s",
                 self.model, len(audio_bytes) // 1024, fmt)
    t0 = time.monotonic()

    if fmt == 'pcm16':
        # Legacy path: raw PCM16 from WebSocket client → wrap as WAV
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_bytes)
        buf.seek(0)
        buf.name = 'audio.wav'
        file_obj = buf
    else:
        # Pre-encoded audio (webm/ogg/wav/mp3): pass bytes directly
        buf = io.BytesIO(audio_bytes)
        buf.name = f'audio.{fmt}'
        file_obj = buf

    try:
        result = await self.client.audio.transcriptions.create(
            model=self.model,
            file=file_obj,
            response_format='verbose_json',
        )
    except Exception as exc:
        stt_log.error("transcribe_failed  model=%s  error=%s", self.model, exc)
        raise

    latency_ms = int((time.monotonic() - t0) * 1000)
    stt_log.info("transcribe_done  latency_ms=%d  lang=%s", latency_ms, result.language)
    return TranscriptionResult(text=result.text, language=result.language)
```

**Backward compatibility check:** All callers of `transcribe()`:
- `server/api/stt.py`: passes `UploadFile` bytes — can be anything → correct
- `server/pipeline.py` `stt_worker`: passes raw PCM16 from `audio_queue` → format detected as `pcm16` → WAV wrapping path unchanged ✅

---

### 6.2 ISO 639-1 Normalisation — `server/lang/detector.py`

Add at module level (before the class):

```python
# ISO 639-2 and common variant → ISO 639-1 normalisation map
_NORMALISE_MAP: dict[str, str] = {
    "eng": "en", "jpn": "ja", "kor": "ko",
    "zho": "zh", "cmn": "zh", "chi": "zh",
    "zh-cn": "zh", "zh-tw": "zh",
    "ja-jp": "ja", "ko-kr": "ko",
    "en-us": "en", "en-gb": "en",
    "en-au": "en", "en-ca": "en",
}


def normalise_language(lang: str) -> str:
    """Normalise a raw Whisper language code to an ISO 639-1 code.

    Applies the _NORMALISE_MAP first, then falls back to _DEFAULT ("en")
    for any unrecognised code.

    Args:
        lang: Raw language string from Whisper (e.g. "eng", "en", "zh-cn").

    Returns:
        Normalised ISO 639-1 code: one of "en", "ja", "ko", "zh".
    """
    code = lang.strip().lower()
    code = _NORMALISE_MAP.get(code, code)
    return code if code in _SUPPORTED else _DEFAULT
```

Update `LanguageDetector.detect()` to call `normalise_language()`:

```python
def detect(self, result: TranscriptionResult) -> str:
    return normalise_language(result.language)
```

Update `server/api/stt.py` to normalise the returned language:

```python
from server.lang.detector import normalise_language

# ... after transcription ...
return STTResponse(
    text=result.text,
    language=normalise_language(result.language),
)
```

---

### 6.3 Clarification Variant — `server/api/navigate.py`

**Extended `BuildingContext`:**

```python
class BuildingContext(BaseModel):
    current_node_label: str
    available_pois: list[str]
    floor_name: str
    poi_not_found: bool = False      # True on second call when no POIs matched
    query: str | None = None         # The failed query string, for the LLM
```

**New `run_clarification_turn()` function:**

```python
async def run_clarification_turn(
    original_query: str,
    available_pois: list[str],
    language: str,
    session_history: list[dict],
    building_context: dict,
    prompt_assembler: Any,
    llm_chain: Any,
    post_processor: Any,
) -> NavigateTurnResult:
    """Execute a clarification LLM turn when building-nav found 0 POI matches.

    Skips intent classification entirely. Forces intent='clarify' and
    destination_query=None so building-nav does not re-run a POI search.

    Args:
        original_query: The query string that produced 0 results.
        available_pois:  Full POI list from building_context.
        language:        Detected language from the request.
        session_history: Current session history for context.
        building_context: Full building_context dict (includes poi_not_found/query).
        prompt_assembler: app.state.prompt_assembler
        llm_chain:        app.state.llm_chain
        post_processor:   app.state.post_processor

    Returns:
        NavigateTurnResult with intent='clarify', destination_query=None.

    Raises:
        LLMError: If the LLM stream fails or produces no tokens.
    """
    # Build a synthetic IntentResult-compatible object for assemble_prompt
    # We reuse the dataclass import to stay consistent with the assembler's
    # type-agnostic duck-typed interface.
    from server.llm.intent import IntentResult

    synthetic_intent = IntentResult(
        intent="clarify",
        language=language if language != "unknown" else "en",
        confidence=1.0,
        needs_clarification=True,
        clarification_reason="poi_not_found",
        query_clean=original_query,
        destination_query=None,
        accessibility_flag=False,
    )

    _, messages = prompt_assembler.assemble_prompt(
        user_input=original_query,
        intent_result=synthetic_intent,
        session_history=session_history,
        retrieved_context="",
        route_type="clarify",
        building_context=building_context,
    )

    try:
        tokens: list[str] = []
        async for token in llm_chain.stream(messages, max_tokens=120, temperature=0.5):
            tokens.append(token)
    except Exception as exc:
        raise LLMError(str(exc)) from exc

    if not tokens:
        raise LLMError("LLM stream produced no tokens for clarification turn")

    raw_response = "".join(tokens)
    response_text = post_processor.clean(raw_response, language)

    return NavigateTurnResult(
        intent="clarify",
        destination_query=None,       # critical — must not be set
        accessibility_flag=False,
        response_text=response_text,
        needs_clarification=True,
        language=language,
    )
```

**Branch in `navigate_endpoint`:**

Add before the `run_navigate_turn` call:

```python
# -- Clarification short-circuit (poi_not_found variant) ------------------
if body.building_context.poi_not_found:
    try:
        result = await run_clarification_turn(
            original_query=body.building_context.query or body.text,
            available_pois=body.building_context.available_pois,
            language=body.language,
            session_history=list(session.history),
            building_context=body.building_context.model_dump(),
            prompt_assembler=request.app.state.prompt_assembler,
            llm_chain=request.app.state.llm_chain,
            post_processor=request.app.state.post_processor,
        )
    except LLMError as exc:
        logger.debug("navigate_clarify_llm_failed  detail=%s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "llm_failed", "detail": str(exc)},
        )
    # Skip the normal run_navigate_turn block — jump straight to history update
    # (same code as below, so we fall into the shared return block)
```

**Add clarification route template to `PromptAssembler._get_route_context_block`:**

```python
"clarify": (
    "# Task\n"
    "The user asked for a location that does not exist in this building.\n"
    "Apologise briefly in one sentence, then suggest 2-3 of the most relevant "
    "destinations from the Available destinations list.\n"
    "Do NOT suggest any location not in that list.\n"
    "Keep the response to 2 sentences maximum."
),
```

---

### 6.4 Port and Origin Configuration Changes

**`server/config.py`** — change one line:
```python
# Before:
server_port=int(os.getenv("SERVER_PORT", "8000")),
# After:
server_port=int(os.getenv("SERVER_PORT", "8001")),
```

**`.env.example`** — update two lines:
```ini
# Port for the FastAPI HTTP server (Robo-BN integration port — building-nav calls 8001)
SERVER_PORT=8001

# CORS origin for building-nav backend server-to-server calls
BUILDING_NAV_ORIGIN=http://localhost:8001
```

**`.env`** — update:
```ini
SERVER_PORT=8001
BUILDING_NAV_ORIGIN=http://localhost:8001
```

---

### 6.5 Classifier Prompt Update — `server/prompts/classifier.txt`

Add the following constraint to the `destination_query` field description inside the JSON schema block of the prompt. (Exact surrounding context will be confirmed by reading the file before editing.)

Target addition:
```
"destination_query": Must be a SHORT NOUN PHRASE of 1-4 words naming the
  destination — NOT a full sentence or clause. This value is used directly in 
  a database LIKE search. Examples that work: "cafeteria", "elevator bank",
  "conference room a", "main entrance". Examples that break the search: 
  "I want to go to the cafeteria", "the elevator on the ground floor".
  Set to null if intent is not navigation or accessibility_request.
```

---

## 7. Test Coverage Plan

### New tests to add to existing test files

#### `server/tests/api/test_stt_endpoint.py`

```
test_webm_audio_accepted
  — Send bytes starting with \x1a\x45\xdf\xa3 (webm magic)
  — Expect: transcribe() called once, 200 returned with correct text/language

test_iso_639_2_normalised_to_639_1
  — Mock transcribe() returning language="eng"
  — Expect: STTResponse.language == "en"

test_iso_639_2_japanese_normalised
  — Mock transcribe() returning language="jpn"
  — Expect: STTResponse.language == "ja"
```

#### `server/tests/api/test_navigate_endpoint.py`

```
test_poi_not_found_variant_returns_clarify_intent
  — POST /api/navigate with building_context.poi_not_found=True, query="blue section"
  — Mock run_clarification_turn returning clarify result
  — Expect: response.intent == "clarify", response.destination_query is None

test_poi_not_found_variant_skips_run_navigate_turn
  — Same as above but also verify run_navigate_turn is NOT called
  — (patch both functions; assert run_navigate_turn.call_count == 0)

test_poi_not_found_502_on_llm_error
  — Mock run_clarification_turn raising LLMError
  — Expect: 502 with error=="llm_failed"

test_poi_not_found_building_context_accepted_by_pydantic
  — POST with full clarification variant body (poi_not_found, query present)
  — Expect: 422 is NOT returned (validation passes)
```

#### `server/tests/api/test_cors.py`

Verify that `BUILDING_NAV_ORIGIN` from the test app's mock config is the allowed origin (already exists — verify it still passes after port change).

### Existing tests that must continue to pass (regression check)

Run the full test suite after all changes. Key regression risks:

| Test file | Risk area |
|---|---|
| `test_stt_endpoint.py` | `pcm16_bytes` parameter rename → must update mock calls if needed |
| `test_navigate_endpoint.py` | `BuildingContext` now has extra optional fields — all existing test bodies remain valid since new fields have defaults |
| `test_tts_endpoint.py` | No changes to tts.py — all tests should pass unchanged |
| `test_health_endpoint.py` | No changes — should pass unchanged |

---

## 8. Implementation Order (Sequenced for Safety)

Execute in this order to avoid partially-broken states between steps:

1. **Fix 6** — `.env` / `.env.example` `BUILDING_NAV_ORIGIN` value (no code change, safe)
2. **Fix 4** — `SERVER_PORT` in `.env`, `.env.example`, `server/config.py` (config-only)
3. **Fix 2** — `server/lang/detector.py` normalisation + `server/api/stt.py` usage (additive, low risk)
4. **Fix 1** — `server/stt/groq_stt.py` audio format detection (medium risk; run tests after)
5. **Fix 5** — `server/prompts/classifier.txt` noun phrase constraint (prompt-only)
6. **Fix 3** — `server/api/navigate.py` + `server/llm/assembler.py` clarification path (highest complexity; test last)

Run `pytest server/tests/api/` after each step.

---

## 9. Things That Are Already Correct (Do Not Second-Guess)

The audit confirms these patterns are correct as-is and should not be questioned or refactored during this integration pass:

- The two-call LLM pipeline in `run_navigate_turn` (classify → route → generate) is the right architecture for the navigate endpoint. The pipeline already handles `out_of_scope`, direct responses, and clarification suffixes.
- The `NavigateSession` + background cleanup task is correctly scoped to the REST path only. WebSocket sessions use `PipelineState` separately.
- The CORS middleware reads `BUILDING_NAV_ORIGIN` from config and applies it before the lifespan starts — this is correct because `add_middleware` must happen at module load time.
- The `tts_router.synthesize()` method already handles the ZH/JA/EN routing. Korean is intentionally rejected at the endpoint layer before `synthesize()` is called.
- The privacy constraints are already met in the logging code (INFO logs contain only metadata).
- The `PromptAssembler._render_building_context_block()` function correctly formats `available_pois` as a comma-separated list and instructs the LLM not to hallucinate POIs.

---

## 10. Environment Variable Summary

| Variable | Old default/value | New default/value | File |
|---|---|---|---|
| `SERVER_PORT` | `8000` | `8001` | `.env`, `.env.example`, `server/config.py` |
| `BUILDING_NAV_ORIGIN` | (empty) | `http://localhost:8001` | `.env`, `.env.example` |
| All others | Unchanged | Unchanged | — |

---

## 11. Stability Guarantees

The following design decisions ensure long-term stability:

**Backward compatibility of the STT format detection:** The `pcm16` code path is preserved as the default for unrecognised magic bytes. The WebSocket audio pipeline never changes. Existing tests all continue to work.

**Additive-only schema changes:** `BuildingContext` gains two optional fields with explicit defaults. All existing callers sending the original three fields continue to work without modification.

**No changes to the WebSocket pipeline:** `VoicePipeline`, `PipelineState`, and the five workers are untouched. All desktop/demo functionality is unaffected.

**Idempotent LLM prompt changes:** The classifier prompt change is additive — it adds a constraint to an existing field description. If the LLM was already returning short noun phrases (which it often does), behaviour is unchanged. If it was returning sentences, behaviour improves.

**Port change is isolated to config:** The `SERVER_PORT` change is a single-source change in config with no cascading effects on routing logic, CORS, or WebSocket handling.

**No changes to response schemas:** `NavigateResponse`, `STTResponse`, and `TTSRequest/Response` field sets are not modified. Building-nav's contracts are fully preserved.
