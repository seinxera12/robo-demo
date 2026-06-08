# Robo-BN Integration — Implementation Tasks

**Spec:** `.kiro/specs/robo-bn-integration/`  
**Design:** `design.md`  
**Execution order:** Tasks are numbered in safe dependency order — complete them sequentially. Run `pytest server/tests/api/` after each task that touches Python code.

---

## Task 2 — Add ISO 639-1 language normalisation utility

**Design ref:** Fix 2 (G-2, G-7)  
**Files:** `server/lang/detector.py`  
**Risk:** Low — additive only, existing behaviour preserved

### What to do

Add the `_NORMALISE_MAP` dict and `normalise_language()` module-level function to `server/lang/detector.py`, then update `LanguageDetector.detect()` to use it.

**Add before the `LanguageDetector` class definition:**

```python
# ISO 639-2 and common variant codes → ISO 639-1 normalisation map.
# Whisper may return either form; always normalise before returning to callers.
_NORMALISE_MAP: dict[str, str] = {
    # ISO 639-2 alphabetic codes
    "eng": "en", "jpn": "ja", "kor": "ko",
    "zho": "zh", "cmn": "zh", "chi": "zh",
    # BCP-47 region variants
    "zh-cn": "zh", "zh-tw": "zh",
    "ja-jp": "ja", "ko-kr": "ko",
    "en-us": "en", "en-gb": "en",
    "en-au": "en", "en-ca": "en",
}


def normalise_language(lang: str) -> str:
    """Normalise a raw Whisper language code to a supported ISO 639-1 code.

    Applies _NORMALISE_MAP first (ISO 639-2 / BCP-47 variants), then falls
    back to _DEFAULT ("en") for any still-unrecognised code.

    Args:
        lang: Raw language string from Whisper (e.g. "eng", "en", "zh-cn").

    Returns:
        One of "en", "ja", "ko", "zh".
    """
    code = lang.strip().lower()
    code = _NORMALISE_MAP.get(code, code)
    return code if code in _SUPPORTED else _DEFAULT
```

**Update `LanguageDetector.detect()` to delegate to `normalise_language()`:**

```python
def detect(self, result: TranscriptionResult) -> str:
    return normalise_language(result.language)
```

### Acceptance criteria

- [ ] `normalise_language("eng")` returns `"en"`
- [ ] `normalise_language("jpn")` returns `"ja"`
- [ ] `normalise_language("kor")` returns `"ko"`
- [ ] `normalise_language("zho")` returns `"zh"`
- [ ] `normalise_language("cmn")` returns `"zh"`
- [ ] `normalise_language("zh-cn")` returns `"zh"`
- [ ] `normalise_language("en-us")` returns `"en"`
- [ ] `normalise_language("en")` returns `"en"` (already valid — passes through)
- [ ] `normalise_language("fr")` returns `"en"` (unsupported → default)
- [ ] `normalise_language("")` returns `"en"` (empty → default)
- [ ] `LanguageDetector.detect()` calls `normalise_language()` — the two-step process (existing tests in `server/lang/test_detector.py` if present must still pass)

---

## Task 3 — Apply language normalisation at the STT response boundary

**Design ref:** Fix 2 (G-6)  
**Files:** `server/api/stt.py`  
**Risk:** Low — one import + one field change in the response constructor

### What to do

Import `normalise_language` from `server.lang.detector` and apply it to the language field before returning `STTResponse`.

**In `server/api/stt.py`**, add the import and update the return statement:

```python
from server.lang.detector import normalise_language

# ... in stt_endpoint(), replace the return statement:

# Before:
return STTResponse(text=result.text, language=result.language)

# After:
return STTResponse(text=result.text, language=normalise_language(result.language))
```

The log line (`logger.info(..., result.language, ...)`) should also use the normalised value to keep logs accurate. Update it to log `normalise_language(result.language)` or capture the normalised value in a local variable first.

### Acceptance criteria

- [ ] `POST /api/stt` with a mock backend returning `language="eng"` responds with `{"language": "en"}`
- [ ] `POST /api/stt` with a mock backend returning `language="jpn"` responds with `{"language": "ja"}`
- [ ] `POST /api/stt` with a mock backend returning `language="en"` responds with `{"language": "en"}` (unchanged)
- [ ] The INFO log line reflects the normalised language code, not the raw Whisper code
- [ ] All existing `test_stt_endpoint.py` tests still pass

---

## Task 4 — Add STT tests: webm format + ISO 639-2 normalisation

**Design ref:** §7 Test Coverage Plan  
**Files:** `server/tests/api/test_stt_endpoint.py`  
**Risk:** None — test-only additions

### What to do

Add the following test methods to the `TestSTTEndpoint` class. These cover the two issues fixed in Tasks 2 and 3.

```python
def test_webm_audio_bytes_accepted(self, client: TestClient) -> None:
    """HTTP 200 when audio bytes start with WebM EBML magic bytes."""
    client.app.state.stt_backend.transcribe = AsyncMock(
        return_value=TranscriptionResult(text="go to the cafeteria", language="en")
    )
    # WebM magic: first 4 bytes are 0x1a 0x45 0xdf 0xa3
    webm_magic = b'\x1a\x45\xdf\xa3' + b'\x00' * 20
    resp = client.post(
        "/api/stt",
        files={"file": ("audio.webm", webm_magic, "audio/webm")},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "go to the cafeteria"


@pytest.mark.parametrize("raw_lang,expected", [
    ("eng", "en"),
    ("jpn", "ja"),
    ("kor", "ko"),
    ("zho", "zh"),
    ("cmn", "zh"),
    ("en-us", "en"),
    ("zh-cn", "zh"),
])
def test_iso_639_2_language_normalised_in_response(
    self, client: TestClient, raw_lang: str, expected: str
) -> None:
    """STTResponse.language is normalised to ISO 639-1 regardless of Whisper output."""
    client.app.state.stt_backend.transcribe = AsyncMock(
        return_value=TranscriptionResult(text="test", language=raw_lang)
    )
    resp = client.post(
        "/api/stt",
        files={"file": ("audio.wav", b"\x00\x01\x02\x03", "audio/wav")},
    )
    assert resp.status_code == 200
    assert resp.json()["language"] == expected
```

### Acceptance criteria

- [ ] `test_webm_audio_bytes_accepted` passes
- [ ] All 7 parametrised `test_iso_639_2_language_normalised_in_response` cases pass
- [ ] All pre-existing `TestSTTEndpoint` tests continue to pass

---

## Task 5 — Fix STT backend: add audio format detection for webm/ogg/wav/mp3

**Design ref:** Fix 1 (G-1)  
**Files:** `server/stt/groq_stt.py`  
**Risk:** Medium — core audio transcription path. Run full test suite after.

### What to do

Replace the existing `GroqSTTBackend.transcribe()` implementation with one that detects the audio container format using magic bytes and routes accordingly.

**Add the magic-byte table and detection function at module level** (after the imports, before the class):

```python
# Audio container format detection via magic bytes.
# Order matters — longer/more-specific patterns before shorter ones.
_AUDIO_MAGIC: list[tuple[bytes, str]] = [
    (b'\x1a\x45\xdf\xa3', 'webm'),  # WebM / MKV (EBML header)
    (b'OggS',             'ogg'),   # Ogg container (Opus / Vorbis)
    (b'RIFF',             'wav'),   # WAV (RIFF header)
    (b'fLaC',             'flac'),  # FLAC
    (b'\xff\xfb',         'mp3'),   # MP3 MPEG-1 Layer 3
    (b'\xff\xf3',         'mp3'),   # MP3 MPEG-2 Layer 3
    (b'\xff\xf2',         'mp3'),   # MP3 MPEG-2.5 Layer 3
    (b'ID3',              'mp3'),   # MP3 with ID3 tag header
]


def _detect_audio_format(audio_bytes: bytes) -> str:
    """Return the audio container format detected from the leading magic bytes.

    Returns one of: 'webm', 'ogg', 'wav', 'flac', 'mp3', or 'pcm16'.
    'pcm16' is the fallback for raw PCM16 audio from the WebSocket pipeline —
    it has no container header, so no magic bytes match.
    """
    for magic, fmt in _AUDIO_MAGIC:
        if audio_bytes[:len(magic)] == magic:
            return fmt
    return 'pcm16'
```

**Replace the `transcribe()` method** — rename the parameter from `pcm16_bytes` to `audio_bytes` and add the format-conditional path:

```python
async def transcribe(self, audio_bytes: bytes) -> TranscriptionResult:
    """Transcribe audio bytes using the Groq Whisper API.

    Accepts both raw PCM16 (legacy WebSocket path) and pre-encoded container
    formats (webm, ogg, wav, mp3, flac) from the REST /api/stt endpoint.
    Format is detected automatically via magic bytes — the content-type header
    from multipart uploads is not trusted because building-nav sends webm bytes
    labelled as audio/wav.
    """
    fmt = _detect_audio_format(audio_bytes)
    audio_kb = len(audio_bytes) // 1024
    stt_log.info(
        "transcribe_start  model=%s  audio_kb=%d  format=%s",
        self.model, audio_kb, fmt,
    )
    t0 = time.monotonic()

    if fmt == 'pcm16':
        # Legacy path: raw PCM16 from the WebSocket AudioClient.
        # Must be wrapped in a WAV container before sending to Groq.
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_bytes)
        wav_buffer.seek(0)
        wav_buffer.name = "audio.wav"
        file_obj = wav_buffer
    else:
        # Pre-encoded audio from the REST endpoint — pass bytes directly.
        # Groq accepts webm, ogg, wav, mp3, flac natively.
        file_obj = io.BytesIO(audio_bytes)
        file_obj.name = f"audio.{fmt}"

    try:
        result = await self.client.audio.transcriptions.create(
            model=self.model,
            file=file_obj,
            response_format="verbose_json",
        )
    except Exception as exc:
        stt_log.error("transcribe_failed  model=%s  format=%s  error=%s", self.model, fmt, exc)
        raise

    latency_ms = int((time.monotonic() - t0) * 1000)
    stt_log.info(
        "transcribe_done  latency_ms=%d  lang=%s  format=%s",
        latency_ms, result.language, fmt,
    )
    return TranscriptionResult(text=result.text, language=result.language)
```

### Acceptance criteria

- [ ] `_detect_audio_format(b'\x1a\x45\xdf\xa3' + bytes(20))` returns `'webm'`
- [ ] `_detect_audio_format(b'OggS' + bytes(20))` returns `'ogg'`
- [ ] `_detect_audio_format(b'RIFF' + bytes(20))` returns `'wav'`
- [ ] `_detect_audio_format(b'\xff\xfb' + bytes(20))` returns `'mp3'`
- [ ] `_detect_audio_format(b'\x00\x01\x02\x03')` returns `'pcm16'` (no magic match)
- [ ] For `'pcm16'` format: `wave.open` is called and the WAV header is written (existing WebSocket path unchanged)
- [ ] For `'webm'` format: bytes are passed directly to Groq without WAV wrapping
- [ ] `stt_log` entries include `format=` field
- [ ] All existing `test_stt_endpoint.py` tests pass (mocked — format detection is internal)
- [ ] `test_webm_audio_bytes_accepted` from Task 4 passes
- [ ] `server/pipeline.py` `stt_worker` calls `stt_backend.transcribe(audio_bytes)` — verify no signature mismatch (the parameter is positional, so rename is backward-compatible)

---

## Task 6 — Update classifier prompt: enforce short noun phrase for destination_query

**Design ref:** Fix 5 (G-8)  
**Files:** `server/prompts/classifier.txt`  
**Risk:** Low — prompt-only; additive constraint

### What to do

1. Read `server/prompts/classifier.txt` in full.
2. Locate the JSON schema section where `destination_query` is described.
3. If the description does not already specify a short noun phrase constraint, replace or augment the `destination_query` field description with:

```
"destination_query": SHORT NOUN PHRASE (1-4 words) naming the destination.
  NOT a full sentence or clause — this value is used verbatim in a SQL LIKE
  search against a location database.
  GOOD: "cafeteria", "elevator bank", "conference room a", "main entrance"
  BAD:  "I want to go to the cafeteria", "the elevator on the ground floor"
  Set to null if intent is not "navigation" or "accessibility_request".
```

If the description already contains equivalent wording (short noun phrase, 1-4 words, database search), this task is a no-op — mark it done and move on.

### Acceptance criteria

- [ ] `server/prompts/classifier.txt` has been read in full before editing
- [ ] The `destination_query` field description in the classifier prompt specifies: maximum word count (≤4), not a sentence, used in a database search
- [ ] The file has no other content changes beyond the `destination_query` description

---

## Task 7 — Extend BuildingContext model with clarification variant fields

**Design ref:** Fix 3 (G-3)  
**Files:** `server/api/navigate.py`  
**Risk:** Low — additive Pydantic fields with defaults; all existing callers unaffected

### What to do

In `server/api/navigate.py`, extend the `BuildingContext` Pydantic model with two optional fields:

```python
class BuildingContext(BaseModel):
    current_node_label: str
    available_pois: list[str]
    floor_name: str
    # Clarification variant — sent only on the second /api/navigate call
    # when building-nav received 0 POI matches for the previous destination_query.
    poi_not_found: bool = False   # True signals this is a clarification turn
    query: str | None = None      # The failed query string (e.g. "blue section")
```

Both fields use `= False` / `= None` defaults so all existing requests that omit them continue to validate without error.

### Acceptance criteria

- [ ] `BuildingContext(current_node_label="Lobby", available_pois=[], floor_name="G")` still validates (no extra fields required)
- [ ] `BuildingContext(..., poi_not_found=True, query="blue section")` validates
- [ ] `BuildingContext(..., poi_not_found=True)` validates (query is optional)
- [ ] All existing `test_navigate_endpoint.py` tests still pass with no body changes
- [ ] `body.building_context.poi_not_found` and `body.building_context.query` are accessible in the endpoint handler after this change

---

## Task 8 — Add clarification route template to PromptAssembler

**Design ref:** Fix 3 (G-3, G-4)  
**Files:** `server/llm/assembler.py`  
**Risk:** Low — additive entry in an existing dict

### What to do

In `server/llm/assembler.py`, inside `PromptAssembler._get_route_context_block()`, add a `"clarify"` entry to the `ROUTE_CONTEXTS` dict:

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

This template is used when `route_type="clarify"` is passed to `assemble_prompt()`.

### Acceptance criteria

- [ ] `PromptAssembler._get_route_context_block("clarify", "")` returns a non-empty string containing "Apologise" (or equivalent)
- [ ] `PromptAssembler._get_route_context_block("general", "")` still returns the general template (no regression)
- [ ] `PromptAssembler._get_route_context_block("unknown_route", "")` still falls back to the general template (no regression — dict `.get()` with default)

---

## Task 9 — Add run_clarification_turn() and poi_not_found branch in navigate endpoint

**Design ref:** Fix 3 (G-3, G-4)  
**Files:** `server/api/navigate.py`  
**Risk:** Medium — new code path on an existing endpoint; test thoroughly

### What to do

**Step 1 — Add `run_clarification_turn()` function** to `server/api/navigate.py`, after `run_navigate_turn()`:

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

    Bypasses intent classification (Call 1) entirely — the intent is known to be
    'clarify' because building-nav explicitly signals poi_not_found=True.
    Forces destination_query=None so building-nav does not retry the POI search.

    Args:
        original_query:   The query string that produced 0 POI results.
        available_pois:   Full POI name list injected into the LLM context.
        language:         ISO 639-1 language code for the response.
        session_history:  OpenAI-format message history for this session.
        building_context: Full building_context dict from the request.
        prompt_assembler: app.state.prompt_assembler
        llm_chain:        app.state.llm_chain
        post_processor:   app.state.post_processor

    Returns:
        NavigateTurnResult with intent='clarify' and destination_query=None.

    Raises:
        LLMError: If the LLM stream fails or yields no tokens.
    """
    from server.llm.intent import IntentResult

    effective_lang = language if language not in ("", "unknown") else "en"

    # Synthetic IntentResult — no LLM call needed for classification
    synthetic_intent = IntentResult(
        intent="clarify",
        language=effective_lang,
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
    response_text = post_processor.clean(raw_response, effective_lang)

    return NavigateTurnResult(
        intent="clarify",
        destination_query=None,    # must stay None — prevents POI search loop
        accessibility_flag=False,
        response_text=response_text,
        needs_clarification=True,
        language=effective_lang,
    )
```

**Step 2 — Add the `poi_not_found` branch in `navigate_endpoint()`**, inserted immediately after the session `last_active` update and before the `run_navigate_turn` call:

```python
# -- Clarification short-circuit when building-nav signals poi_not_found ------
# This path bypasses Call 1 (intent classification) to prevent the infinite loop
# where the classifier re-extracts destination_query from the clarification text,
# causing building-nav to run another POI search that also returns 0 results.
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
else:
    # -- Normal path: run full two-call LLM pipeline --------------------------
    try:
        result = await run_navigate_turn(
            # ... existing arguments unchanged ...
        )
    except ClassifyError as exc:
        ...
    except LLMError as exc:
        ...
```

The history update and return statement after this block are shared between both branches — no duplication needed.

### Acceptance criteria

- [ ] `run_clarification_turn()` is a standalone importable async function
- [ ] When called, it does NOT call `intent_classifier.classify()` (no Call 1)
- [ ] When called, it calls `prompt_assembler.assemble_prompt()` with `route_type="clarify"`
- [ ] It returns `NavigateTurnResult` with `intent="clarify"` and `destination_query=None`
- [ ] `navigate_endpoint()` with `poi_not_found=True` calls `run_clarification_turn` and does NOT call `run_navigate_turn`
- [ ] `navigate_endpoint()` with `poi_not_found=False` (default) calls `run_navigate_turn` as before
- [ ] On `LLMError` from `run_clarification_turn`, endpoint returns HTTP 502 with `error="llm_failed"`
- [ ] Session history is still updated after the clarification turn (same as normal path)
- [ ] All pre-existing `test_navigate_endpoint.py` tests still pass

---

## Task 10 — Add navigate endpoint tests for the clarification variant

**Design ref:** §7 Test Coverage Plan  
**Files:** `server/tests/api/test_navigate_endpoint.py`  
**Risk:** None — test-only additions

### What to do

Add the following test methods to the `TestNavigateEndpoint` class:

```python
_CLARIFICATION_BODY = {
    "text": "Clarify: The user wants to go to 'blue section', but no POIs match this query.",
    "language": "en",
    "session_id": str(uuid.uuid4()),
    "building_context": {
        "current_node_label": "Main Lobby",
        "available_pois": ["Cafeteria", "Elevator Bank", "Conference Room A"],
        "floor_name": "Ground Floor",
        "poi_not_found": True,
        "query": "blue section",
    },
}

_CLARIFY_TURN_RESULT = NavigateTurnResult(
    intent="clarify",
    destination_query=None,
    accessibility_flag=False,
    response_text="I couldn't find 'blue section'. Did you mean Cafeteria or Elevator Bank?",
    needs_clarification=True,
    language="en",
)


def test_poi_not_found_body_accepted_by_pydantic(self, client: TestClient) -> None:
    """BuildingContext with poi_not_found + query fields must not return 422."""
    with patch(
        "server.api.navigate.run_clarification_turn",
        new=AsyncMock(return_value=_CLARIFY_TURN_RESULT),
    ):
        resp = client.post("/api/navigate", json=_CLARIFICATION_BODY)
    assert resp.status_code != 422, f"Got 422: {resp.json()}"
    assert resp.status_code == 200


def test_poi_not_found_returns_clarify_intent(self, client: TestClient) -> None:
    """poi_not_found=True → response.intent=='clarify' and destination_query is None."""
    with patch(
        "server.api.navigate.run_clarification_turn",
        new=AsyncMock(return_value=_CLARIFY_TURN_RESULT),
    ):
        resp = client.post("/api/navigate", json=_CLARIFICATION_BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "clarify"
    assert data["destination_query"] is None
    assert data["needs_clarification"] is True


def test_poi_not_found_skips_run_navigate_turn(self, client: TestClient) -> None:
    """poi_not_found=True must call run_clarification_turn, NOT run_navigate_turn."""
    with patch(
        "server.api.navigate.run_clarification_turn",
        new=AsyncMock(return_value=_CLARIFY_TURN_RESULT),
    ) as mock_clarify, patch(
        "server.api.navigate.run_navigate_turn",
        new=AsyncMock(return_value=_FIXED_TURN_RESULT),
    ) as mock_navigate:
        resp = client.post("/api/navigate", json=_CLARIFICATION_BODY)
    assert resp.status_code == 200
    mock_clarify.assert_called_once()
    mock_navigate.assert_not_called()


def test_poi_not_found_normal_path_still_calls_run_navigate_turn(
    self, client: TestClient
) -> None:
    """poi_not_found=False (default) must NOT call run_clarification_turn."""
    with patch(
        "server.api.navigate.run_clarification_turn",
        new=AsyncMock(return_value=_CLARIFY_TURN_RESULT),
    ) as mock_clarify, patch(
        "server.api.navigate.run_navigate_turn",
        new=AsyncMock(return_value=_FIXED_TURN_RESULT),
    ) as mock_navigate:
        resp = client.post("/api/navigate", json=_VALID_BODY)
    assert resp.status_code == 200
    mock_navigate.assert_called_once()
    mock_clarify.assert_not_called()


def test_poi_not_found_502_on_llm_error(self, client: TestClient) -> None:
    """poi_not_found=True + LLMError from run_clarification_turn → HTTP 502."""
    with patch(
        "server.api.navigate.run_clarification_turn",
        new=AsyncMock(side_effect=LLMError("stream failed")),
    ):
        resp = client.post("/api/navigate", json=_CLARIFICATION_BODY)
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "llm_failed"


def test_poi_not_found_session_history_updated(self, client: TestClient) -> None:
    """Session history is updated even after a clarification turn."""
    sid = str(uuid.uuid4())
    body = {**_CLARIFICATION_BODY, "session_id": sid}
    with patch(
        "server.api.navigate.run_clarification_turn",
        new=AsyncMock(return_value=_CLARIFY_TURN_RESULT),
    ):
        resp = client.post("/api/navigate", json=body)
    assert resp.status_code == 200
    session = client.app.state.navigate_sessions[sid]
    assert len(session.history) == 2  # one user + one assistant message
    assert session.history[0]["role"] == "user"
    assert session.history[1]["role"] == "assistant"
```

### Acceptance criteria

- [ ] All 6 new test methods pass
- [ ] All pre-existing `TestNavigateEndpoint` tests still pass
- [ ] `pytest server/tests/api/test_navigate_endpoint.py` exits 0

---

## Task 11 — Run the full test suite and verify no regressions

**Design ref:** §8 Implementation Order  
**Files:** None (verification only)  
**Risk:** None

### What to do

Run the complete API test suite:

```bash
pytest server/tests/api/ -v
```

Then run any broader test discovery:

```bash
pytest server/ -v --ignore=server/tests/properties
```

All tests must pass. If any pre-existing test fails, fix the regression before proceeding.

### Specific regression checks

| Test file | What to verify |
|---|---|
| `test_stt_endpoint.py` | All 5 original + 1 new webm + 7 normalisation parametrised = pass |
| `test_tts_endpoint.py` | All 9 tests pass — `tts.py` was not modified |
| `test_navigate_endpoint.py` | All original 12 + 6 new clarification tests = pass |
| `test_health_endpoint.py` | All 6 tests pass — `health.py` was not modified |
| `test_cors.py` | CORS tests pass with updated `BUILDING_NAV_ORIGIN` origin |

### Acceptance criteria

- [ ] `pytest server/tests/api/ -v` exits with code 0
- [ ] No test file reports a regression from a pre-existing test
- [ ] All newly added tests from Tasks 4 and 10 appear in output and pass
- [ ] Test count in summary matches: existing tests + 8 new tests (1 webm + 7 normalisation + 6 clarification - 6 already counted above)

---

## Summary — Gaps Closed by Task Set

| Gap ID | Severity | Closed by task(s) |
|---|---|---|
| G-1 — webm audio silently broken in STT | Critical | Task 5 |
| G-2 — ISO 639-2 not normalised in LanguageDetector | High | Task 2 |
| G-3 — BuildingContext drops poi_not_found / query fields | High | Task 7 |
| G-4 — Clarification variant causes infinite loop | High | Task 9 |
| G-5 — Server port mismatch (8000 vs 8001) | Medium | Task 1 |
| G-6 — Raw Whisper language returned without normalisation | Medium | Task 3 |
| G-7 — LanguageDetector.detect() misses ISO 639-2 codes | Low | Task 2 |
| G-8 — Classifier prompt lacks noun phrase constraint | Low | Task 6 |
| G-9 — BUILDING_NAV_ORIGIN blank causes noisy startup warning | Low | Task 1 |

**Files touched across all tasks:**

| File | Task(s) |
|---|---|
| `.env` | 1 |
| `.env.example` | 1 |
| `server/config.py` | 1 |
| `server/lang/detector.py` | 2 |
| `server/api/stt.py` | 3 |
| `server/stt/groq_stt.py` | 5 |
| `server/prompts/classifier.txt` | 6 |
| `server/api/navigate.py` | 7, 9 |
| `server/llm/assembler.py` | 8 |
| `server/tests/api/test_stt_endpoint.py` | 4 |
| `server/tests/api/test_navigate_endpoint.py` | 10 |
