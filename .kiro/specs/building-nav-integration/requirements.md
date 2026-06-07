# Requirements Document

## Introduction

This document specifies the changes required to the Robo voice assistant (`robo-bn` branch) to expose it as an HTTP REST service layer consumed by the `building-nav` indoor navigation project. Robo currently runs as a WebSocket-first, single-process voice pipeline with no REST endpoints, no CORS middleware, and no external-facing API. The `building-nav` project needs Robo to act as its NLU/voice backend: transcribing audio, classifying navigation intent, generating conversational responses, and synthesising speech in four languages (EN, JA, ZH, KO).

The scope of this document is limited to changes on the Robo side (`robo-bn` branch). Building-nav backend and frontend changes are out of scope. All new REST endpoints are called server-to-server by the building-nav backend; the browser never contacts Robo directly.

---

## Glossary

- **Robo_Server**: The FastAPI uvicorn process running the Robo voice assistant (`server/main.py`).
- **STT_Endpoint**: The new `POST /api/stt` HTTP endpoint on the Robo_Server.
- **TTS_Endpoint**: The new `POST /api/tts` HTTP endpoint on the Robo_Server.
- **Detect_Language_Endpoint**: The new `POST /api/detect-language` HTTP endpoint on the Robo_Server.
- **Navigate_Endpoint**: The new `POST /api/navigate` HTTP endpoint on the Robo_Server.
- **Health_Endpoint**: The new `GET /api/health` HTTP endpoint on the Robo_Server.
- **GroqSTTBackend**: The existing `server/stt/groq_stt.py` class that wraps Groq Whisper transcription.
- **TTSRouter**: The existing `server/tts/tts_router.py` class that routes text to the appropriate Kokoro TTS engine and performs sentence-boundary accumulation.
- **KokoroChineseTTS**: A new Kokoro-based TTS engine instance for Mandarin Chinese, following the same pattern as the existing `KokoroTTS` (EN) and `KokoroJapaneseTTS` classes.
- **LanguageDetector**: The existing `server/lang/detector.py` class that maps Groq Whisper ISO 639-1 language codes to supported language codes.
- **IntentClassifier**: The existing `server/llm/intent.py` class that performs LLM-based intent classification (Call 1).
- **IntentResult**: The dataclass returned by `IntentClassifier.classify()`, to be extended with navigation-specific fields.
- **NavigateSession**: An in-memory record for a single `session_id` on the REST path, holding conversation history for the Navigate_Endpoint.
- **Building_Context**: A JSON object passed by the building-nav backend to the Navigate_Endpoint containing `current_node_label`, `available_pois`, and `floor_name`.
- **CORS_Middleware**: FastAPI `CORSMiddleware` configuration that allows the building-nav backend origin.
- **Session_Store**: The in-memory dict (`app.state.navigate_sessions`) that maps `session_id` UUIDs to `NavigateSession` records.
- **POI**: Point of Interest — a named location in the building (e.g. "Cafeteria", "Elevator Bank").

---

## Requirements

### Requirement 1: CORS Middleware

**User Story:** As the building-nav backend, I want the Robo_Server to accept cross-origin HTTP requests from my origin, so that I can call its REST endpoints without receiving CORS errors.

#### Acceptance Criteria

1. THE Robo_Server SHALL include FastAPI `CORSMiddleware` that allows requests from the configured building-nav backend origin.
2. WHEN a preflight `OPTIONS` request is received from the allowed origin, THE Robo_Server SHALL respond with the appropriate `Access-Control-Allow-Origin`, `Access-Control-Allow-Methods`, and `Access-Control-Allow-Headers` headers.
3. THE Robo_Server SHALL allow at minimum the HTTP methods `GET`, `POST`, and `OPTIONS` in the CORS configuration.
4. THE Robo_Server SHALL read the allowed origin from an environment variable (`BUILDING_NAV_ORIGIN`) so that the value can be changed per deployment without modifying source code.
5. IF `BUILDING_NAV_ORIGIN` is not set, THEN THE Robo_Server SHALL default to allowing `http://localhost:8001` and log a startup warning that the default is in use.

---

### Requirement 2: STT Endpoint (`POST /api/stt`)

**User Story:** As the building-nav backend, I want to submit an audio file to Robo and receive a text transcription with a detected language code, so that I can pass the transcribed text on to the navigation intent classifier.

#### Acceptance Criteria

1. WHEN a `POST /api/stt` request is received with a valid audio file upload (multipart `file` field, WAV or PCM16 format), THE STT_Endpoint SHALL call `GroqSTTBackend.transcribe()` with the submitted audio bytes and return a JSON response containing `text` (string) and `language` (ISO 639-1 string).
2. THE STT_Endpoint SHALL accept audio uploaded as `multipart/form-data` with the field name `file`.
3. WHEN `GroqSTTBackend.transcribe()` returns successfully, THE STT_Endpoint SHALL respond with HTTP 200 and a JSON body `{"text": "<transcribed text>", "language": "<iso code>"}`.
4. IF the submitted file field is absent or the file size is zero bytes, THEN THE STT_Endpoint SHALL respond with HTTP 422 and a JSON error body describing the validation failure.
5. IF `GroqSTTBackend.transcribe()` raises an exception, THEN THE STT_Endpoint SHALL respond with HTTP 502 and a JSON body `{"error": "stt_failed", "detail": "<error message>"}`.
6. THE STT_Endpoint SHALL not alter the `text` or `language` values returned by `GroqSTTBackend.transcribe()`.
7. THE STT_Endpoint SHALL reuse the `GroqSTTBackend` instance stored in `app.state.stt_backend` rather than instantiating a new client per request.

#### Correctness Properties

- **Response schema invariant**: FOR ALL valid audio inputs, the JSON response from STT_Endpoint SHALL contain both `text` (a string) and `language` (a non-empty string). The response SHALL never contain `text: null`.
- **Passthrough fidelity**: FOR ALL inputs where `GroqSTTBackend.transcribe()` succeeds with result `r`, the STT_Endpoint response `text` SHALL equal `r.text` and `language` SHALL equal `r.language`, with no modification.

---

### Requirement 3: TTS Endpoint (`POST /api/tts`)

**User Story:** As the building-nav backend, I want to submit a text string and language code to Robo and receive synthesised WAV audio bytes, so that I can play navigation instructions and chatbot responses aloud to the user.

#### Acceptance Criteria

1. WHEN a `POST /api/tts` request is received with a JSON body containing `text` (non-empty string) and `language` (string), THE TTS_Endpoint SHALL call `TTSRouter.synthesize(text, language)` and return the resulting WAV bytes with Content-Type `audio/wav`.
2. THE TTS_Endpoint SHALL accept `language` values `"en"`, `"ja"`, and `"zh"`.
3. WHEN `language` is `"ko"`, THE TTS_Endpoint SHALL respond with HTTP 406 and a JSON body `{"fallback": "browser_tts", "language": "ko"}` without calling `TTSRouter.synthesize()`.
4. IF the `text` field is absent or an empty string, THEN THE TTS_Endpoint SHALL respond with HTTP 422 and a JSON error body.
5. IF the `language` field is absent or not one of the accepted values (`"en"`, `"ja"`, `"zh"`, `"ko"`), THEN THE TTS_Endpoint SHALL respond with HTTP 422 and a JSON error body.
6. IF `TTSRouter.synthesize()` raises an exception, THEN THE TTS_Endpoint SHALL respond with HTTP 502 and a JSON body `{"error": "tts_failed", "detail": "<error message>"}`.
7. THE TTS_Endpoint SHALL reuse the `TTSRouter` instance stored in `app.state.tts_router` rather than instantiating a new engine per request.
8. WHEN `language` is `"en"` or `"ja"`, THE TTS_Endpoint SHALL use the existing `TTSRouter.synthesize()` path, which already handles language-based engine routing and CJK auto-detection.

#### Correctness Properties

- **Valid WAV header invariant**: FOR ALL non-empty text inputs with `language` in `{"en", "ja", "zh"}`, the HTTP 200 response body SHALL begin with the RIFF WAV header bytes `b"RIFF"` at offset 0 and `b"WAVE"` at offset 8.
- **KO fallback consistency**: FOR ALL requests with `language = "ko"`, the response status SHALL be 406 and the body SHALL contain `{"fallback": "browser_tts", "language": "ko"}`.
- **Empty text rejection**: FOR ALL requests where `text` is an empty string or whitespace-only, the response status SHALL be 422.

---

### Requirement 4: Detect-Language Endpoint (`POST /api/detect-language`)

**User Story:** As the building-nav backend, I want to detect the language of a text string without performing STT, so that I can determine language from keyboard input submitted via the text fallback path.

#### Acceptance Criteria

1. WHEN a `POST /api/detect-language` request is received with a JSON body containing `text` (non-empty string), THE Detect_Language_Endpoint SHALL analyse the text and return a JSON body `{"language": "<code>"}` where `<code>` is one of `"en"`, `"ja"`, `"zh"`, or `"ko"`.
2. THE Detect_Language_Endpoint SHALL detect Japanese when the text contains hiragana (U+3040–U+309F) or katakana (U+30A0–U+30FF) characters.
3. THE Detect_Language_Endpoint SHALL detect Chinese when the text contains CJK Unified Ideographs (U+4E00–U+9FFF) without hiragana or katakana.
4. THE Detect_Language_Endpoint SHALL detect Korean when the text contains Hangul syllables (U+AC00–U+D7A3) or Hangul Jamo (U+1100–U+11FF).
5. IF the text does not match any of the above patterns, THEN THE Detect_Language_Endpoint SHALL return `"en"` as the default language code.
6. IF the `text` field is absent or an empty string, THEN THE Detect_Language_Endpoint SHALL respond with HTTP 422.
7. THE Detect_Language_Endpoint SHALL be stateless: the response for a given input text SHALL be identical regardless of the order in which requests are made.

#### Correctness Properties

- **Output domain invariant**: FOR ALL non-empty text inputs, the `language` value in the response SHALL be one of `{"en", "ja", "zh", "ko"}`.
- **Idempotence**: FOR ALL text inputs `t`, calling `POST /api/detect-language` with `t` twice in sequence SHALL return the same `language` value both times.
- **Japanese vs Chinese discrimination**: FOR ALL text strings containing hiragana or katakana characters, the response SHALL be `"ja"` and not `"zh"`, even when CJK ideographs are also present.
- **Korean detection**: FOR ALL text strings containing only Hangul syllables (U+AC00–U+D7A3), the response SHALL be `"ko"`.

---

### Requirement 5: Navigate Endpoint (`POST /api/navigate`)

**User Story:** As the building-nav backend, I want to send a user utterance with building context to Robo and receive a structured navigation intent response including a destination query, accessibility flag, and a generated conversational response text, so that I can resolve the destination against my POI database and present results to the user.

#### Acceptance Criteria

1. WHEN a `POST /api/navigate` request is received with a valid JSON body (see schema below), THE Navigate_Endpoint SHALL run the two-call LLM pipeline (intent classification → response generation) and return a JSON response.
2. THE Navigate_Endpoint SHALL accept a JSON request body with the following fields:
   - `text` (string, required): the user utterance
   - `language` (string, required): the detected language code (`"en"`, `"ja"`, `"zh"`, `"ko"`)
   - `session_id` (string, required): a UUID identifying the conversation session
   - `building_context` (object, required): contains `current_node_label` (string), `available_pois` (array of strings), and `floor_name` (string)
3. THE Navigate_Endpoint SHALL respond with a JSON body containing:
   - `intent` (string): the classified intent, one of `"navigation"`, `"accessibility_request"`, `"general"`, `"out_of_scope"`, `"clarify"`
   - `destination_query` (string or null): the extracted destination phrase, populated when `intent` is `"navigation"` or `"accessibility_request"`
   - `accessibility_flag` (boolean): `true` when the user expressed a mobility or accessibility constraint
   - `response_text` (string): the LLM-generated conversational response
   - `needs_clarification` (boolean): `true` when the LLM response requests disambiguation from the user
   - `language` (string): the detected or input language code
   - `session_id` (string): echoed from the request
4. THE Navigate_Endpoint SHALL inject `building_context.available_pois` and `building_context.current_node_label` into the prompt assembled by `PromptAssembler` so the LLM does not hallucinate destinations that are absent from the list.
5. THE Navigate_Endpoint SHALL load and maintain per-session conversation history from the Session_Store, keyed by `session_id`, and pass it to `PromptAssembler.assemble_prompt()` as `session_history`.
6. WHEN the `session_id` is new (not found in Session_Store), THE Navigate_Endpoint SHALL create a new NavigateSession with an empty history.
7. WHEN the Navigate_Endpoint returns a successful response, THE Robo_Server SHALL append the user utterance and assistant response to the NavigateSession history, trimmed to the configured `session_memory_turns` limit.
8. IF the `text` field is an empty string or whitespace-only, THEN THE Navigate_Endpoint SHALL respond with HTTP 422.
9. IF any required field is absent from the request body, THEN THE Navigate_Endpoint SHALL respond with HTTP 422 and a JSON error body.
10. IF the intent classification call (Call 1) fails, THEN THE Navigate_Endpoint SHALL respond with HTTP 502 and a JSON body `{"error": "classify_failed", "detail": "<error message>"}`.
11. IF the LLM response generation call (Call 2) fails after retry, THEN THE Navigate_Endpoint SHALL respond with HTTP 502 and a JSON body `{"error": "llm_failed", "detail": "<error message>"}`.
12. THE Navigate_Endpoint SHALL be implemented by extracting the per-turn LLM orchestration logic from `VoicePipeline._llm_worker` into a standalone async function reusable by both the WebSocket pipeline and the REST endpoint, without altering the behaviour of the existing WebSocket pipeline.

#### Correctness Properties

- **Response schema invariant**: FOR ALL valid requests, the Navigate_Endpoint JSON response SHALL contain all seven required fields (`intent`, `destination_query`, `accessibility_flag`, `response_text`, `needs_clarification`, `language`, `session_id`), with correct types, and none of them null except `destination_query`.
- **Session_id echo**: FOR ALL valid requests, the `session_id` in the response SHALL equal the `session_id` in the request.
- **History growth bound**: FOR ALL sequences of N successful Navigate_Endpoint calls with the same `session_id`, the NavigateSession history entry count SHALL never exceed `session_memory_turns * 2`.
- **History trim invariant**: WHEN the history for a `session_id` reaches `session_memory_turns * 2` entries and a new call is made, the oldest user+assistant pair SHALL be removed before appending the new pair, so the count remains at `session_memory_turns * 2`.

---

### Requirement 6: Navigation Intent Classification

**User Story:** As the building-nav backend, I want Robo to classify user utterances as `navigation` or `accessibility_request` in addition to existing intent types, so that I can distinguish navigation requests from general conversation and detect accessibility needs.

#### Acceptance Criteria

1. THE IntentClassifier SHALL recognise a `"navigation"` intent when the user utterance expresses a desire to go to or find a location, landmark, room, or facility.
2. THE IntentClassifier SHALL recognise an `"accessibility_request"` intent when the user utterance expresses a mobility or physical accessibility constraint (e.g. wheelchair use, inability to use stairs, need for elevator access).
3. WHEN an utterance expresses both a navigation goal and an accessibility constraint in the same utterance, THE IntentClassifier SHALL return `intent = "navigation"`, `destination_query` set to the extracted destination phrase, and `accessibility_flag = true`.
4. THE IntentResult dataclass SHALL be extended with two new fields: `destination_query` (string or null, default null) and `accessibility_flag` (boolean, default false).
5. WHEN `intent` is `"navigation"` or `"accessibility_request"`, THE IntentClassifier SHALL populate `destination_query` with the destination name or category extracted from the utterance, or null if no specific destination can be identified.
6. WHEN `intent` is not `"navigation"` and not `"accessibility_request"`, THE IntentClassifier SHALL set `destination_query` to null and `accessibility_flag` to false.
7. THE `classifier.txt` system prompt SHALL be updated to include the two new intent values, their definitions, examples, and the JSON schema for the new fields `destination_query` and `accessibility_flag`.
8. THE `_VALID_INTENTS` frozenset in `server/llm/intent.py` SHALL be updated to include `"navigation"` and `"accessibility_request"`.
9. WHEN the LLM classifier returns JSON missing `destination_query` or `accessibility_flag`, THE IntentClassifier SHALL treat the missing fields as null and false respectively, without raising an exception.

#### Correctness Properties

- **accessibility_flag type invariant**: FOR ALL IntentResult instances, `accessibility_flag` SHALL be a boolean (True or False), never null or a non-boolean type.
- **destination_query type invariant**: FOR ALL IntentResult instances, `destination_query` SHALL be either a non-empty string or null. An empty string SHALL be normalised to null.
- **Backward compatibility**: FOR ALL inputs that would have previously been classified as `"general"`, `"environment"`, `"web_search"`, `"out_of_scope"`, or `"clarify"`, the updated IntentClassifier SHALL still return one of those intents for such inputs, with `destination_query = null` and `accessibility_flag = false`.

---

### Requirement 7: Language Support Expansion (KO and ZH)

**User Story:** As a Korean or Mandarin Chinese-speaking user of the building-nav system, I want Robo to understand my speech, generate responses in my language, and synthesise audio in my language (or fall back gracefully for Korean), so that I can use the voice navigation assistant in my preferred language.

#### Acceptance Criteria

1. THE LanguageDetector SHALL map the Groq Whisper ISO 639-1 code `"ko"` to the supported language code `"ko"`.
2. THE LanguageDetector SHALL map the Groq Whisper ISO 639-1 code `"zh"` to the supported language code `"zh"`.
3. WHEN `LanguageDetector.detect()` receives a `TranscriptionResult` with `language = "ko"`, THE LanguageDetector SHALL return `"ko"`.
4. WHEN `LanguageDetector.detect()` receives a `TranscriptionResult` with `language = "zh"`, THE LanguageDetector SHALL return `"zh"`.
5. FOR ALL language codes not in the expanded supported set `{"en", "ja", "ko", "zh"}`, THE LanguageDetector SHALL return `"en"` as the default fallback.
6. THE Robo_Server SHALL instantiate a `KokoroChineseTTS` engine for Mandarin Chinese TTS synthesis, using the appropriate Kokoro ZH voice, following the same pattern as `KokoroTTS` and `KokoroJapaneseTTS`.
7. THE TTSRouter SHALL route text containing only CJK Unified Ideographs (U+4E00–U+9FFF) without hiragana or katakana to `KokoroChineseTTS`.
8. THE TTSRouter SHALL continue to route text containing hiragana (U+3040–U+309F) or katakana (U+30A0–U+30FF) characters to `KokoroJapaneseTTS`, regardless of co-present CJK ideographs.
9. WHERE Korean language is requested for TTS, THE TTS_Endpoint SHALL return HTTP 406 with `{"fallback": "browser_tts", "language": "ko"}` without calling any Kokoro engine.
10. THE PromptAssembler SHALL load `lang_ko.txt` and `lang_zh.txt` prompt blocks when the detected language is `"ko"` or `"zh"` respectively.
11. THE `server/prompts/lang_ko.txt` file SHALL exist and contain language and tone instructions directing the LLM to respond in Korean.
12. THE `server/prompts/lang_zh.txt` file SHALL exist and contain language and tone instructions directing the LLM to respond in Mandarin Chinese.
13. WHEN the Robo_Server starts up, THE Robo_Server SHALL pre-warm the `KokoroChineseTTS` engine concurrently with the existing EN and JA engine warm-ups.

#### Correctness Properties

- **LanguageDetector output domain invariant**: FOR ALL possible `TranscriptionResult.language` strings (including empty strings, unknown codes, and mixed-case variants), `LanguageDetector.detect()` SHALL return a value in `{"en", "ja", "ko", "zh"}`.
- **Japanese vs Chinese TTSRouter discrimination**: FOR ALL text strings `t` where `_detect_language_from_text(t)` is called, if `t` contains any hiragana or katakana character, the result SHALL be `"ja"`. If `t` contains only CJK ideographs and no kana, the result SHALL be `"zh"`. If `t` contains neither, the result SHALL be `"en"`.
- **Round-trip language passthrough**: FOR ALL valid `TranscriptionResult` instances with `language` in `{"en", "ja", "ko", "zh"}`, `LanguageDetector.detect(result)` SHALL return the same code, preserving the language information without loss.

---

### Requirement 8: Session Management for the REST Path

**User Story:** As the building-nav backend, I want Robo to maintain conversation history across multiple calls within a session, so that the LLM can refer to previous turns when generating navigation responses.

#### Acceptance Criteria

1. THE Robo_Server SHALL maintain a Session_Store (`app.state.navigate_sessions`) as an in-memory dictionary mapping `session_id` (UUID string) to NavigateSession records.
2. A NavigateSession SHALL contain: `history` (list of message dicts in OpenAI format), `last_active` (UTC timestamp of the most recent request).
3. WHEN a Navigate_Endpoint request arrives with a `session_id` that is not in the Session_Store, THE Robo_Server SHALL create a new NavigateSession with an empty history and the current timestamp.
4. WHEN a Navigate_Endpoint request arrives with a `session_id` that is in the Session_Store, THE Robo_Server SHALL update the NavigateSession's `last_active` to the current timestamp and use its history for prompt assembly.
5. THE Robo_Server SHALL run a background cleanup task that removes NavigateSession records whose `last_active` is more than 30 minutes in the past, running at a minimum of once every 5 minutes.
6. THE Session_Store SHALL never persist to disk or any external storage; all session data is in-memory only.
7. THE NavigateSession history SHALL never contain raw audio bytes, file paths, or personally identifiable information — only role/content message dicts as used by OpenAI-compatible APIs.
8. WHEN the Robo_Server shuts down, THE Session_Store SHALL be discarded without any persistence or serialisation.

#### Correctness Properties

- **Session count monotonicity under expiry**: AFTER the background cleanup task runs, the number of sessions in Session_Store SHALL be less than or equal to the number of sessions that were active within the last 30 minutes.
- **History length bound**: FOR ALL NavigateSession records, the length of `history` SHALL never exceed `session_memory_turns * 2` entries.
- **No stale sessions after TTL**: FOR ALL NavigateSession records whose `last_active` is more than 30 minutes ago at the time the cleanup task runs, the record SHALL not be present in Session_Store after the task completes.

---

### Requirement 9: Health Endpoint (`GET /api/health`)

**User Story:** As the building-nav backend, I want to check whether Robo is operational before making NLU calls, so that I can implement a circuit breaker and show appropriate degradation UI to users.

#### Acceptance Criteria

1. WHEN a `GET /api/health` request is received, THE Health_Endpoint SHALL return HTTP 200 with a JSON body containing `{"status": "ok", "tts_ready": <bool>, "stt_ready": <bool>}`.
2. THE Health_Endpoint SHALL set `tts_ready: true` when at least one Kokoro TTS engine has completed its warm-up successfully.
3. THE Health_Endpoint SHALL set `stt_ready: true` when the `GroqSTTBackend` instance is initialised and the Groq API key is present.
4. THE Health_Endpoint SHALL respond within 200 milliseconds under normal operating conditions, performing no blocking I/O or external API calls.
5. THE Health_Endpoint SHALL be accessible without authentication and shall not require any request body.
6. WHEN Robo_Server has started but TTS warm-up has not yet completed, THE Health_Endpoint SHALL return HTTP 200 with `tts_ready: false` rather than failing.

---

### Requirement 10: Building-Nav Deployment Configuration

**User Story:** As a Robo deployment operator, I want a `deployment.yaml` and associated prompt files configured for the building-nav use case, so that the LLM persona, out-of-scope responses, and context are appropriate for indoor navigation assistance.

#### Acceptance Criteria

1. THE Robo_Server SHALL include a `config/deployment.yaml` configured for the building-nav deployment with `deployment_id: "building-nav"`, `deployment_type: "reception"`, and multilingual `out_of_scope_response` entries for `"en"`, `"ja"`, `"ko"`, and `"zh"` languages.
2. THE `out_of_scope_response` values in the building-nav `deployment.yaml` SHALL communicate that the assistant only helps with navigation within this building.
3. THE `config/deployment.yaml` SHALL set `web_search_enabled: false` for the building-nav deployment, since the assistant must not retrieve external web content for navigation queries.
4. THE `config/deployment.yaml` SHALL include at least one entry in `environment_docs` pointing to a building context document that describes the building name, floors, and general layout.
5. THE building context document referenced by `environment_docs` SHALL be a plain text or Markdown file located within the project directory.
6. THE `language_primary` field SHALL be configurable and default to `"en"` in the building-nav deployment.
7. WHEN `PromptAssembler` loads prompt blocks for `"ko"` or `"zh"` detected languages, it SHALL fall back to the `lang_unknown.txt` block only if the corresponding `lang_ko.txt` or `lang_zh.txt` file is not present, and SHALL log a warning.

---

### Requirement 11: Endpoint Integration — Reuse of Existing Components

**User Story:** As a developer on the `robo-bn` branch, I want the new REST endpoints to reuse existing `app.state` component instances rather than creating new ones, so that I do not introduce resource duplication or break the existing WebSocket pipeline.

#### Acceptance Criteria

1. THE STT_Endpoint SHALL access `app.state.stt_backend` for transcription and SHALL not instantiate a new `GroqSTTBackend` or `groq.AsyncGroq` client.
2. THE TTS_Endpoint SHALL access `app.state.tts_router` for synthesis and SHALL not instantiate new `KokoroTTS`, `KokoroJapaneseTTS`, or `KokoroChineseTTS` instances.
3. THE Navigate_Endpoint SHALL access `app.state.intent_classifier`, `app.state.prompt_assembler`, `app.state.llm_chain`, and `app.state.router` from `app.state` and SHALL not instantiate duplicate instances.
4. THE new REST endpoints SHALL be registered on the same `FastAPI` `app` instance as the existing WebSocket endpoints in `server/main.py`.
5. THE new REST endpoint handlers SHALL be defined in a new module (`server/api/`) and imported into `server/main.py` as APIRouter instances, keeping `server/main.py` from becoming a monolith.
6. THE existing `/ws` and `/ws/ui` WebSocket endpoints SHALL continue to function without modification after the REST endpoints are added.

---

### Requirement 12: Logging and Privacy

**User Story:** As a compliance-conscious operator, I want Robo's REST endpoints to not log personally identifiable information or speech content, so that the system does not inadvertently store user data in violation of privacy requirements (FR-017 of the building-nav spec).

#### Acceptance Criteria

1. THE Robo_Server SHALL not log the `text` content of STT transcriptions at INFO level or above in any REST endpoint handler.
2. THE Robo_Server SHALL not log the `text` content of TTS requests at INFO level or above in any REST endpoint handler.
3. THE Robo_Server SHALL not log the `text` content of Navigate_Endpoint requests at INFO level or above.
4. THE Robo_Server SHALL log request metadata at INFO level (endpoint path, response status, latency in ms, language code) without logging the content of audio or text fields.
5. THE Robo_Server SHALL log full request content only at DEBUG level, which is disabled by default (`LOG_LEVEL=INFO` in `.env.example`).
6. THE Session_Store SHALL not write NavigateSession history to any log file.
