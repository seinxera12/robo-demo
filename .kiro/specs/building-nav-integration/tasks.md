# Implementation Plan: building-nav-integration

## Overview

Expose Robo as an HTTP REST service layer consumed by the `building-nav` indoor navigation
project. Implementation follows dependency order: foundation changes first (config, CORS,
language detector, intent classifier, TTS engines), then shared function extraction, then
endpoint modules, then module wiring in `main.py`, then configuration files, then tests.

The existing WebSocket pipeline (`/ws`, `/ws/ui`) must remain fully functional throughout.

---

## Tasks

- [ ] 1. Extend `server/config.py` with `BUILDING_NAV_ORIGIN`
  - Add `building_nav_origin: str = "http://localhost:8001"` field to the `Config` dataclass
  - In `Config.from_env()`, read `os.getenv("BUILDING_NAV_ORIGIN", "")` and default to
    `"http://localhost:8001"` when unset, logging a startup warning via `logging.getLogger(__name__).warning()`
  - Add `BUILDING_NAV_ORIGIN=` (empty) to `.env.example` with a comment explaining the default
  - _Requirements: 1.4, 1.5_

- [ ] 2. Expand `server/lang/detector.py` to support Korean and Mandarin Chinese
  - Change `_SUPPORTED` frozenset from `{"en", "ja"}` to `{"en", "ja", "ko", "zh"}`
  - Leave `detect()` method body unchanged — the frozenset membership check handles the rest
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [ ] 3. Extend `server/llm/intent.py` with navigation intents and new `IntentResult` fields
  - [ ] 3.1 Add `"navigation"` and `"accessibility_request"` to `_VALID_INTENTS` frozenset
    - _Requirements: 6.8_
  - [ ] 3.2 Add `destination_query: str | None = None` and `accessibility_flag: bool = False`
    fields to the `IntentResult` dataclass (append after existing fields so existing construction
    sites are not broken)
    - In `IntentResult.__post_init__()`, normalise empty-string `destination_query` to `None`:
      `if self.destination_query == "": self.destination_query = None`
    - _Requirements: 6.4, 6.9_
  - [ ] 3.3 Parse the new fields in `IntentClassifier.classify()`: add
    `destination_query=data.get("destination_query") or None` and
    `accessibility_flag=bool(data.get("accessibility_flag", False))` to the `IntentResult(...)` call
    - Ensure `_default_result()` also passes `destination_query=None, accessibility_flag=False`
    - _Requirements: 6.3, 6.5, 6.6, 6.9_

- [ ] 4. Update `server/prompts/classifier.txt` with navigation intent definitions
  - Add `"navigation"` intent definition: user wants to find or go to a location/room/facility;
    extract destination in `destination_query`; set `accessibility_flag: true` for mobility constraints
  - Add `"accessibility_request"` intent definition: user expresses a mobility/accessibility need
    without a specific navigation goal
  - Add `destination_query` (string or null) and `accessibility_flag` (boolean) to the example
    JSON schema block; show both fields present in every response (null/false for non-navigation intents)
  - _Requirements: 6.7_

- [ ] 5. Add `KokoroChineseTTS` class to `server/tts/kokoro_tts.py`
  - Implement `KokoroChineseTTS` following the exact pattern of `KokoroJapaneseTTS`:
    - `DEFAULT_VOICE = "zf_xiaobei"`
    - `__init__` sets `self._pipeline = None`
    - `_get_pipeline()` lazy-initialises `KPipeline(lang_code='z')` with `tts_log` calls
      (`init_pipeline engine=KokoroChineseTTS lang=zh`, `pipeline_ready ...`)
    - `_synthesize_sync(text)` iterates pipeline output, concatenates audio chunks with numpy,
      writes 24 kHz WAV to `io.BytesIO` via `soundfile`, logs
      `synthesised engine=KokoroChineseTTS text=... wav_bytes=... synthesis_ms=... audio_duration_ms=...`
    - `warm_up()` calls `_get_pipeline()` then `pipeline.load_voice(DEFAULT_VOICE)`,
      logs `warm_up_complete engine=KokoroChineseTTS voice=...`
    - `async synthesize(text)` uses `loop.run_in_executor(None, _synthesize_sync, text)`
  - _Requirements: 7.6, 7.13_

- [ ] 6. Update `server/tts/tts_router.py` to route Mandarin Chinese
  - [ ] 6.1 Split `_CJK_RE` into two separate compiled patterns:
    - `_HIRAGANA_KATAKANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')`
    - `_CJK_IDEOGRAPH = re.compile(r'[\u4e00-\u9fff]')`
    - Update `_detect_language_from_text(text)` to return `"ja"` if `_HIRAGANA_KATAKANA.search(text)`,
      else `"zh"` if `_CJK_IDEOGRAPH.search(text)`, else `"en"`
    - _Requirements: 7.7, 7.8_
  - [ ] 6.2 Add `zh_tts: KokoroChineseTTS` parameter to `TTSRouter.__init__`, store as `self._zh_tts`
    - Update the import at the top of the file: `from server.tts.kokoro_tts import KokoroTTS, KokoroJapaneseTTS, KokoroChineseTTS`
    - _Requirements: 7.6_
  - [ ] 6.3 Update `TTSRouter.synthesize()` to route `"zh"` to `self._zh_tts`:
    - Call `effective = _detect_language_from_text(text)`
    - If `effective != language` and `language in ("en", "ja", "zh")`, set `effective = language`
    - Route: `"ja"` → `self._ja_tts`, `"zh"` → `self._zh_tts`, else → `self._en_tts`
    - Update the debug log call to reflect three-way routing
    - _Requirements: 7.7, 7.8, 7.9_

- [ ] 7. Update `server/llm/assembler.py` to load KO/ZH prompt blocks and accept `building_context`
  - [ ] 7.1 In `PromptAssembler._load_prompt_files()`, load `lang_ko.txt` and `lang_zh.txt` with
    the same optional-with-warning fallback pattern used for `lang_ja_formal.txt`:
    ```python
    for lang_code, filename in [("ko", "lang_ko.txt"), ("zh", "lang_zh.txt")]:
        lang_path = self.prompts_dir / filename
        if lang_path.exists():
            with open(lang_path, 'r', encoding='utf-8') as f:
                self._prompt_cache[filename] = f.read()
        else:
            logger.warning("Prompt file %s not found, falling back to lang_unknown.txt", filename)
            self._prompt_cache[filename] = self._prompt_cache["lang_unknown.txt"]
    ```
    - _Requirements: 7.10, 10.7_
  - [ ] 7.2 In `PromptAssembler._select_language_block()`, add `"ko": "lang_ko.txt"` and
    `"zh": "lang_zh.txt"` to the `language_files` dict
    - _Requirements: 7.10_
  - [ ] 7.3 Add optional `building_context: dict | None = None` parameter to
    `PromptAssembler.assemble_prompt()`
    - Add helper `_render_building_context_block(ctx: dict) -> str` at module level:
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
    - When `building_context` is provided, assemble blocks as
      `[base_block, deployment_block, building_block, language_block, route_context_block]`
    - When absent, preserve the existing four-block order
    - _Requirements: 5.4_

- [ ] 8. Create Korean and Mandarin Chinese prompt files
  - [ ] 8.1 Create `server/prompts/lang_ko.txt` with Korean language/tone instructions:
    ```
    # Language: Korean (한국어)

    Respond in Korean (한국어). Use natural, polite Korean appropriate for a public building
    navigation assistant. Use 존댓말 (formal speech level, -요/-습니다 endings). Keep responses
    concise and clear for voice output. Avoid complex sentence structures.

    When giving directions, use clear Korean directional terms (왼쪽, 오른쪽, 앞으로, etc.).
    ```
    - _Requirements: 7.11_
  - [ ] 8.2 Create `server/prompts/lang_zh.txt` with Mandarin Chinese language/tone instructions:
    ```
    # Language: Mandarin Chinese (普通话)

    Respond in Mandarin Chinese (普通话). Use natural, polite Mandarin appropriate for a public
    building navigation assistant. Use formal but approachable language. Keep responses concise
    and clear for voice output. Avoid overly complex sentence structures.

    When giving directions, use clear Mandarin directional terms (左边, 右边, 前方, etc.).
    ```
    - _Requirements: 7.12_

- [ ] 9. Create `server/api/` package with endpoint modules
  - [ ] 9.1 Create `server/api/__init__.py` — exports a single `api_router`:
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
    - _Requirements: 11.4, 11.5_

  - [ ] 9.2 Create `server/api/stt.py` — `POST /api/stt`:
    - Define `STTResponse(BaseModel)` with `text: str` and `language: str`
    - `stt_endpoint(request: Request, file: UploadFile = File(...)) -> STTResponse`:
      - Read `await file.read()` → `audio_bytes`; raise HTTP 422 if zero bytes
      - Call `await request.app.state.stt_backend.transcribe(audio_bytes)` → `result`
      - Log at INFO: endpoint, status 200, latency_ms, language (never log `text`)
      - On exception: log at DEBUG, raise `HTTPException(502, {"error":"stt_failed","detail":str(exc)})`
      - Return `STTResponse(text=result.text, language=result.language)`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 11.1, 12.1, 12.4_

  - [ ] 9.3 Create `server/api/tts.py` — `POST /api/tts`:
    - Define `TTSRequest(BaseModel)` with `text: str = Field(..., min_length=1)` and `language: str`
      - Add `@validator("text")` that rejects whitespace-only strings with `ValueError`
      - Add `@validator("language")` that rejects values not in `{"en","ja","zh","ko"}` with `ValueError`
    - `tts_endpoint(request: Request, body: TTSRequest) -> Response`:
      - If `body.language == "ko"`: return `JSONResponse({"fallback":"browser_tts","language":"ko"}, 406)`
        without calling `TTSRouter.synthesize()`
      - `wav_bytes = await request.app.state.tts_router.synthesize(body.text, body.language)`
      - Log at INFO: endpoint, status 200, latency_ms, language (never log `text`)
      - On exception: log at DEBUG, raise `HTTPException(502, {"error":"tts_failed","detail":str(exc)})`
      - Return `Response(content=wav_bytes, media_type="audio/wav")`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 11.2, 12.2, 12.4_

  - [ ] 9.4 Create `server/api/detect_language.py` — `POST /api/detect-language`:
    - Define `DetectLanguageRequest(BaseModel)` with `text: str = Field(..., min_length=1)`
    - Define `DetectLanguageResponse(BaseModel)` with `language: str`
    - Implement module-level pure function:
      ```python
      _HIRAGANA_KATAKANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')
      _CJK_IDEOGRAPH     = re.compile(r'[\u4e00-\u9fff]')
      _HANGUL            = re.compile(r'[\uac00-\ud7a3\u1100-\u11ff]')

      def detect_language_from_text(text: str) -> str:
          if _HANGUL.search(text): return "ko"
          if _HIRAGANA_KATAKANA.search(text): return "ja"
          if _CJK_IDEOGRAPH.search(text): return "zh"
          return "en"
      ```
      Priority order: KO > JA > ZH > EN
    - `detect_language_endpoint(body: DetectLanguageRequest) -> DetectLanguageResponse`:
      returns `DetectLanguageResponse(language=detect_language_from_text(body.text))`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [ ] 9.5 Create `server/api/navigate.py` — `POST /api/navigate` and `run_navigate_turn()`:
    - Define `BuildingContext(BaseModel)` with `current_node_label: str`,
      `available_pois: list[str]`, `floor_name: str`
    - Define `NavigateRequest(BaseModel)` with `text`, `language`, `session_id`,
      `building_context: BuildingContext`; add `@validator("text")` for whitespace rejection
    - Define `NavigateResponse(BaseModel)` with all seven required fields:
      `intent`, `destination_query: str | None`, `accessibility_flag: bool`,
      `response_text: str`, `needs_clarification: bool`, `language: str`, `session_id: str`
    - Define `NavigateTurnResult` dataclass with the same six fields (no `session_id`)
    - Define custom exceptions `ClassifyError(Exception)` and `LLMError(Exception)`
    - Implement `async run_navigate_turn(text, language, session_history, building_context, intent_classifier, prompt_assembler, llm_chain, router, post_processor) -> NavigateTurnResult`:
      1. Call `await intent_classifier.classify(text)` → `intent_result`; raise `ClassifyError` on exception
      2. Call `await router.route(intent_result, language)` → `route_result`
      3. If `route_result.direct_response` is not None: return early with that text
      4. Call `prompt_assembler.assemble_prompt(text, intent_result, session_history, ..., building_context=building_context.dict() if building_context else None)`
      5. Collect all tokens from `llm_chain.stream(messages)` into a full string; raise `LLMError` on exception
      6. Apply `post_processor.clean()` to the assembled response
      7. Return `NavigateTurnResult` with fields populated from `intent_result` and the assembled response
    - Implement `navigate_endpoint(request: Request, body: NavigateRequest) -> NavigateResponse`:
      1. Look up `body.session_id` in `request.app.state.navigate_sessions`; create `NavigateSession` if not found
      2. Update `session.last_active = datetime.utcnow()`
      3. Call `await run_navigate_turn(...)` passing `session.history` and all `app.state` components
      4. On `ClassifyError`: raise `HTTPException(502, {"error":"classify_failed","detail":str(exc)})`
      5. On `LLMError`: raise `HTTPException(502, {"error":"llm_failed","detail":str(exc)})`
      6. Append `{"role":"user","content":body.text}` and `{"role":"assistant","content":result.response_text}` to `session.history`
      7. Trim: while `len(session.history) > session_memory_turns * 2`: pop first two items
      8. Return `NavigateResponse(session_id=body.session_id, **result fields)`
    - Log at INFO: endpoint, status, latency_ms, language, intent (never log `text`)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 8.1, 8.2, 8.7, 11.3, 12.3, 12.4_

  - [ ] 9.6 Create `server/api/health.py` — `GET /api/health`:
    - Define `HealthResponse(BaseModel)` with `status: str`, `tts_ready: bool`, `stt_ready: bool`
    - `health_endpoint(request: Request) -> HealthResponse`:
      - `tts_ready = getattr(request.app.state, "tts_warmed_up", False)`
      - `stt_ready = hasattr(request.app.state, "stt_backend") and bool(getattr(request.app.state.config, "groq_api_key", ""))`
      - Return `HealthResponse(status="ok", tts_ready=tts_ready, stt_ready=stt_ready)`
    - No blocking I/O, no external calls
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [ ] 10. Checkpoint — verify module structure compiles before wiring
  - Ensure all new files in `server/api/` import without errors
  - Run `python -m py_compile server/api/stt.py server/api/tts.py server/api/detect_language.py server/api/navigate.py server/api/health.py server/api/__init__.py`
  - Ensure all modified files also compile: `server/lang/detector.py`, `server/llm/intent.py`, `server/tts/kokoro_tts.py`, `server/tts/tts_router.py`, `server/llm/assembler.py`

- [ ] 11. Wire all changes into `server/main.py`
  - [ ] 11.1 Add `CORSMiddleware` registration immediately after `app = FastAPI(...)`:
    ```python
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.building_nav_origin],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    ```
    Note: CORS middleware must be added before `app.include_router()`; read `config` from `app.state.config` or restructure so config is available at app-creation time
    - _Requirements: 1.1, 1.2, 1.3_
  - [ ] 11.2 Add `KokoroChineseTTS` import and instantiation in the lifespan startup block:
    ```python
    from server.tts.kokoro_tts import KokoroTTS, KokoroJapaneseTTS, KokoroChineseTTS
    kokoro_zh_tts = KokoroChineseTTS()
    tts_router = TTSRouter(en_tts=kokoro_tts, ja_tts=kokoro_ja_tts, zh_tts=kokoro_zh_tts)
    ```
    - _Requirements: 7.6_
  - [ ] 11.3 Add `kokoro_zh_tts.warm_up` to the `asyncio.gather` warm-up call, and set
    `app.state.tts_warmed_up = True` inside the `try` block after the gather succeeds
    (leave `tts_warmed_up` at `False` if the warm-up raises):
    ```python
    await asyncio.gather(
        loop.run_in_executor(None, kokoro_tts.warm_up),
        loop.run_in_executor(None, kokoro_ja_tts.warm_up),
        loop.run_in_executor(None, kokoro_zh_tts.warm_up),
    )
    app.state.tts_warmed_up = True
    ```
    - _Requirements: 7.13, 9.2, 9.6_
  - [ ] 11.4 Store additional state entries in `app.state` after all components are ready:
    ```python
    app.state.kokoro_zh_tts = kokoro_zh_tts
    app.state.navigate_sessions = {}  # dict[str, NavigateSession]
    ```
    - _Requirements: 8.1_
  - [ ] 11.5 Create and launch the background session cleanup task inside the lifespan block
    (before `yield`):
    ```python
    from datetime import datetime, timedelta

    async def _cleanup_sessions() -> None:
        while True:
            await asyncio.sleep(300)
            cutoff = datetime.utcnow() - timedelta(minutes=30)
            sessions = app.state.navigate_sessions
            stale = [sid for sid, s in sessions.items() if s.last_active < cutoff]
            for sid in stale:
                sessions.pop(sid, None)
            if stale:
                logger.info("session_cleanup  removed=%d  remaining=%d", len(stale), len(sessions))

    asyncio.create_task(_cleanup_sessions())
    ```
    - _Requirements: 8.5_
  - [ ] 11.6 Include the API router after the middleware and before the static file mount:
    ```python
    from server.api import api_router
    app.include_router(api_router)
    ```
    - _Requirements: 11.4, 11.5, 11.6_

- [ ] 12. Checkpoint — verify server starts and WebSocket endpoints are unbroken
  - Run `python -m py_compile server/main.py` to confirm no import or syntax errors
  - Ensure all `app.state` accesses in new endpoint modules match the keys set in `lifespan`
  - Confirm existing `/ws` and `/ws/ui` routes still appear in `app.routes`

- [ ] 13. Update `config/deployment.yaml` for the building-nav deployment
  - Replace the contents of `config/deployment.yaml` with the building-nav configuration:
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
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.6_

- [ ] 14. Create `docs/building_context.txt` building context document
  - Create the file `docs/building_context.txt` as a Markdown/plain-text placeholder:
    ```
    # Building Context

    Building Name: [To be filled for each deployment]
    Floors: [To be filled]
    General layout: [To be filled]
    ```
  - _Requirements: 10.4, 10.5_

- [ ] 15. Write property-based tests for `detect_language_from_text()` (Properties 1–5)
  - Create `tests/properties/test_detect_language_properties.py`
  - [ ]* 15.1 Write property test for output domain invariant (Property 1)
    - `@given(text=st.text(min_size=1))` — assert result in `{"en","ja","zh","ko"}`
    - **Property 1: detect-language output domain**
    - **Validates: Requirements 4.1, 4.7**
  - [ ]* 15.2 Write property test for Japanese detection priority (Property 2)
    - `@given(kana=st.sampled_from(hiragana+katakana chars), suffix=st.text())` — assert result == `"ja"`
    - **Property 2: Japanese detection priority**
    - **Validates: Requirements 4.2, 7.8**
  - [ ]* 15.3 Write property test for Korean detection (Property 3)
    - `@given(text=st.text(alphabet=st.characters(min_codepoint=0xAC00, max_codepoint=0xD7A3), min_size=1))`
    - **Property 3: Korean detection**
    - **Validates: Requirements 4.4**
  - [ ]* 15.4 Write property test for Chinese detection without kana (Property 4)
    - Generate strings from CJK block (U+4E00–U+9FFF) with no hiragana/katakana
    - **Property 4: Chinese detection (CJK without kana)**
    - **Validates: Requirements 4.3, 7.7**
  - [ ]* 15.5 Write property test for idempotence (Property 5)
    - `@given(text=st.text(min_size=1))` — assert two consecutive calls return same value
    - **Property 5: detect-language idempotence**
    - **Validates: Requirements 4.7**

- [ ] 16. Write property-based tests for `LanguageDetector` (Properties 6–7)
  - Create `tests/properties/test_language_detector_properties.py`
  - [ ]* 16.1 Write property test for LanguageDetector output domain (Property 6)
    - `@given(language=st.text())` — create `TranscriptionResult(text="x", language=language)`,
      assert `LanguageDetector().detect(result)` in `{"en","ja","ko","zh"}`
    - **Property 6: LanguageDetector output domain invariant**
    - **Validates: Requirements 7.1–7.5**
  - [ ]* 16.2 Write property test for round-trip of supported codes (Property 7)
    - `@given(language=st.sampled_from(["en","ja","ko","zh"]))` — assert detector returns same code
    - **Property 7: LanguageDetector round-trip for supported codes**
    - **Validates: Requirements 7.1–7.4**

- [ ] 17. Write property-based tests for `_detect_language_from_text()` in `tts_router.py` (Property 8)
  - Create `tests/properties/test_tts_router_properties.py`
  - [ ]* 17.1 Write property test for TTSRouter text-based language discrimination (Property 8)
    - Three sub-cases: kana present → `"ja"`; CJK only (no kana) → `"zh"`; neither → `"en"`
    - **Property 8: TTSRouter text-based language discrimination**
    - **Validates: Requirements 7.7, 7.8**

- [ ] 18. Write property-based tests for `IntentResult` field invariants (Properties 9–10)
  - Create `tests/properties/test_intent_result_properties.py`
  - [ ]* 18.1 Write property test for `accessibility_flag` type invariant (Property 9)
    - `@given(accessibility_flag=st.one_of(st.booleans(), st.integers(), st.none(), st.text()))`
    - Construct `IntentResult` with `bool(accessibility_flag) if accessibility_flag is not None else False`
    - Assert `isinstance(result.accessibility_flag, bool)`
    - **Property 9: IntentResult accessibility_flag type invariant**
    - **Validates: Requirements 6.4**
  - [ ]* 18.2 Write property test for `destination_query` normalisation (Property 10)
    - `@given(destination_query=st.one_of(st.just(""), st.text(min_size=1)))` — construct `IntentResult`,
      assert `None` when input was `""`, else value preserved
    - **Property 10: IntentResult destination_query normalisation**
    - **Validates: Requirements 6.4, 6.9**

- [ ] 19. Write property-based tests for session management (Properties 14–15)
  - Create `tests/properties/test_session_management_properties.py`
  - [ ]* 19.1 Write property test for history length bound (Property 14)
    - `@given(num_calls=st.integers(1,30), memory_turns=st.integers(1,10))` — simulate N append+trim
      cycles on a `NavigateSession`, assert `len(history) <= memory_turns * 2` after each call
    - **Property 14: History length bound**
    - **Validates: Requirements 5.7, 8.2**
  - [ ]* 19.2 Write property test for session cleanup correctness (Property 15)
    - `@given(num_fresh=st.integers(0,10), num_stale=st.integers(0,10))` — build store with fresh
      (5 min ago) and stale (45 min ago) sessions, run cleanup logic, assert fresh remain and stale removed
    - **Property 15: Session cleanup correctness**
    - **Validates: Requirements 8.3, 8.5**

- [ ] 20. Write unit tests for each endpoint and modified module
  - Create `tests/api/` package with the following test files:
  - [ ]* 20.1 Create `tests/api/test_stt_endpoint.py`
    - Mock `app.state.stt_backend.transcribe` to return a known `TranscriptionResult`
    - Test: HTTP 200 with correct `text` and `language` fields
    - Test: HTTP 422 when file field is absent
    - Test: HTTP 422 when file is zero bytes
    - Test: HTTP 502 when `transcribe()` raises `Exception`
    - Test: response `text` and `language` match exactly what `transcribe()` returned (passthrough)
    - _Requirements: 2.1–2.7_
  - [ ]* 20.2 Create `tests/api/test_tts_endpoint.py`
    - Mock `app.state.tts_router.synthesize` to return a minimal valid WAV bytes constant
    - Test: HTTP 200 with `Content-Type: audio/wav` for `language` in `{"en","ja","zh"}`
    - Test: HTTP 406 with `{"fallback":"browser_tts","language":"ko"}` for `language="ko"` (synthesize NOT called)
    - Test: HTTP 422 for empty `text`
    - Test: HTTP 422 for whitespace-only `text`
    - Test: HTTP 422 for unknown `language`
    - Test: HTTP 502 when `synthesize()` raises
    - _Requirements: 3.1–3.8_
  - [ ]* 20.3 Create `tests/api/test_detect_language_endpoint.py`
    - No mocking needed — pure function
    - Test: hiragana text → `"ja"`; katakana text → `"ja"`; mixed kana+CJK → `"ja"`
    - Test: CJK-only text → `"zh"`
    - Test: Hangul text → `"ko"`
    - Test: ASCII text → `"en"`
    - Test: HTTP 422 for empty `text`
    - _Requirements: 4.1–4.7_
  - [ ]* 20.4 Create `tests/api/test_navigate_endpoint.py`
    - Mock `run_navigate_turn` to return a fixed `NavigateTurnResult`
    - Test: HTTP 200 with all seven required response fields present and typed correctly
    - Test: `session_id` in response equals `session_id` in request
    - Test: new `session_id` creates a new `NavigateSession` in `app.state.navigate_sessions`
    - Test: existing `session_id` reuses the existing session and updates `last_active`
    - Test: after N calls with same `session_id`, `len(session.history) <= session_memory_turns * 2`
    - Test: HTTP 422 for empty `text`
    - Test: HTTP 422 for missing required fields
    - Test: HTTP 502 when `run_navigate_turn` raises `ClassifyError`
    - Test: HTTP 502 when `run_navigate_turn` raises `LLMError`
    - _Requirements: 5.1–5.12_
  - [ ]* 20.5 Create `tests/api/test_health_endpoint.py`
    - Test: HTTP 200 with `{"status":"ok","tts_ready":true,"stt_ready":true}` when fully warmed up
    - Test: `tts_ready: false` when `app.state.tts_warmed_up` is `False`
    - Test: `stt_ready: false` when `groq_api_key` is empty string
    - Test: no request body required
    - _Requirements: 9.1–9.6_
  - [ ]* 20.6 Create `tests/api/test_intent_result.py`
    - Test: `destination_query=""` is normalised to `None` in `__post_init__`
    - Test: `destination_query="cafeteria"` is preserved unchanged
    - Test: `accessibility_flag` is always a `bool` instance
    - Test: `_default_result()` returns `destination_query=None, accessibility_flag=False`
    - Test: LLM JSON missing `destination_query` does not raise, defaults to `None`
    - Test: LLM JSON missing `accessibility_flag` does not raise, defaults to `False`
    - Test: existing intents (`general`, `environment`, etc.) still accepted by `_VALID_INTENTS`
    - _Requirements: 6.4, 6.6, 6.8, 6.9_
  - [ ]* 20.7 Create `tests/api/test_cors.py`
    - Test: preflight `OPTIONS /api/stt` from the allowed origin returns correct `Access-Control-Allow-*` headers
    - Test: preflight from a disallowed origin does not return `Access-Control-Allow-Origin` for that origin
    - _Requirements: 1.1, 1.2, 1.3_

- [ ] 21. Final checkpoint — run the full test suite
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Property tests use `max_examples=200` (minimum 100 per design doc) with `@settings(max_examples=200)`
- Each property test docstring must include `Feature: building-nav-integration, Property N: <description>`
- The `NavigateSession` dataclass can be defined in `server/api/navigate.py` or a shared `server/api/models.py`
- CORS middleware must be registered before `app.include_router()` calls; FastAPI processes middleware in registration order
- `app.state.tts_warmed_up` is only set to `True` inside the `try` block of the warm-up gather — if warm-up raises, the `except` branch leaves it absent/False so `health_endpoint` correctly returns `tts_ready: false`
- `_cleanup_sessions` uses `datetime.utcnow()` (same as `NavigateSession.last_active`) — both sides must use the same clock source
- All six existing `app.state` keys (`stt_backend`, `tts_router`, `intent_classifier`, `prompt_assembler`, `llm_chain`, `router`) are already set by the current lifespan; new endpoints can access them immediately without lifespan changes to those keys
- The `run_navigate_turn()` function is intentionally free of `asyncio.Queue`, `InterruptController`, and `PipelineState` so it can be unit-tested without spinning up the full pipeline
