# Requirements Document

## Introduction

This feature addresses three confirmed latency bottlenecks in the real-time voice pipeline that together cause a Time-to-First-Audio (TTFA) of ~10 seconds on LLM-backed turns. The goal is to reduce TTFA to under 3 seconds on warm sessions by:

1. **Streaming LLM tokens to TTS** — eliminating full-response buffering in `_llm_worker` so the existing sentence splitter (`TTSRouter.accumulate`) receives tokens one at a time and can begin synthesis on the first complete sentence.
2. **Pipelining inter-sentence synthesis** — starting synthesis of sentence N+1 while sentence N is still playing, eliminating silence gaps between sentences.
3. **Eager model loading** — loading both Kokoro TTS pipelines at server startup instead of on the first synthesis call, eliminating the ~1–2 s cold-start penalty per session.

No changes are required to the sentence boundary detection logic, language routing, or the `run_in_executor` pattern already in place.

## Glossary

- **Pipeline**: The five-worker asyncio pipeline in `server/pipeline.py` that processes one WebSocket session.
- **LLM_Worker**: The `_llm_worker` coroutine in `VoicePipeline` that streams LLM tokens and pushes them to `token_queue`.
- **TTS_Worker**: The `_tts_worker` coroutine in `VoicePipeline` that drains `token_queue`, accumulates tokens to sentence boundaries, and synthesises WAV.
- **TTSRouter**: The `TTSRouter` class in `server/tts/tts_router.py` that routes synthesis to the correct Kokoro engine and accumulates tokens via `accumulate()`.
- **Token_Queue**: The `asyncio.Queue` (`PipelineState.token_queue`) that carries individual LLM tokens (strings) and the `_END_OF_TOKENS` sentinel between LLM_Worker and TTS_Worker.
- **Audio_Out_Queue**: The `asyncio.Queue` (`PipelineState.audio_out_queue`) that carries synthesised WAV bytes from TTS_Worker to Audio_Output_Worker.
- **KokoroTTS**: The English TTS engine class in `server/tts/kokoro_tts.py`.
- **KokoroJapaneseTTS**: The Japanese TTS engine class in `server/tts/kokoro_tts.py`.
- **TTFA**: Time-to-First-Audio — the elapsed time from the end of the user's speech (or text submission) to when the first audio byte is sent to the client.
- **Sentence**: A span of text ending at a boundary detected by `TTSRouter.accumulate()` (`.`, `?`, `!`, `。`, `？`, `！`).
- **Warm Session**: A session where both Kokoro pipelines have already been loaded into memory (i.e., after server startup pre-warm).
- **Cold Start**: The first synthesis call on a Kokoro engine instance, which triggers model loading.
- **END_OF_TOKENS**: The sentinel object pushed to `token_queue` by LLM_Worker to signal that the LLM stream for the current turn is complete.

---

## Requirements

### Requirement 1: Stream LLM Tokens to TTS Incrementally

**User Story:** As a voice chatbot user, I want the bot to start speaking as soon as the first sentence of the LLM response is ready, so that I do not wait for the entire response to be generated before hearing anything.

#### Acceptance Criteria

1. WHEN the LLM stream produces a token, THE LLM_Worker SHALL push that token to `token_queue` immediately, without waiting for the full response to complete.
2. WHEN the LLM stream is interrupted by `PipelineState.interrupt`, THE LLM_Worker SHALL stop pushing tokens and push `END_OF_TOKENS` to `token_queue`.
3. WHEN the LLM stream completes normally, THE LLM_Worker SHALL push `END_OF_TOKENS` to `token_queue` after the last token.
4. WHEN a retry is required because the first LLM stream attempt returns an empty response, THE LLM_Worker SHALL push tokens from the retry stream to `token_queue` incrementally.
5. WHEN the fallback message is used after two failed stream attempts, THE LLM_Worker SHALL push the fallback message as a single token followed by `END_OF_TOKENS`.
6. THE LLM_Worker SHALL apply `PostProcessor.clean` to the complete assembled response before broadcasting the `llm_text_chunk` UI event, without delaying token delivery to `token_queue`.
7. WHEN a `clarification_suffix` is present on the route result, THE LLM_Worker SHALL append it as an additional token to `token_queue` before pushing `END_OF_TOKENS`.

### Requirement 2: Pipeline Inter-Sentence TTS Synthesis

**User Story:** As a voice chatbot user, I want the bot's speech to flow continuously without audible gaps between sentences, so that the conversation feels natural.

#### Acceptance Criteria

1. WHEN TTS_Worker enqueues a synthesised WAV for sentence N, THE TTS_Worker SHALL begin synthesising sentence N+1 concurrently, without waiting for sentence N playback to complete.
2. WHEN multiple sentences are ready for synthesis in the same turn, THE TTS_Worker SHALL synthesise them concurrently up to a maximum of 2 simultaneous synthesis tasks.
3. WHEN `PipelineState.interrupt` is set during synthesis, THE TTS_Worker SHALL cancel any in-progress synthesis tasks and discard their results.
4. WHEN a synthesis task completes while `PipelineState.interrupt` is set, THE TTS_Worker SHALL discard the resulting WAV bytes without enqueuing them.
5. THE TTS_Worker SHALL preserve sentence ordering in `audio_out_queue` regardless of which synthesis task completes first.

### Requirement 3: Eager Kokoro Model Loading at Server Startup

**User Story:** As a voice chatbot operator, I want both TTS models to be loaded before the first user request, so that the first synthesis call of every session has no cold-start penalty.

#### Acceptance Criteria

1. WHEN the FastAPI server starts, THE Server SHALL load the `KokoroTTS` pipeline into memory before accepting WebSocket connections.
2. WHEN the FastAPI server starts, THE Server SHALL load the `KokoroJapaneseTTS` pipeline into memory before accepting WebSocket connections.
3. WHEN either Kokoro pipeline fails to load during startup, THE Server SHALL log a warning and continue starting up, allowing the pipeline to fall back to lazy loading on first use.
4. THE `KokoroTTS` class SHALL expose a method to pre-load its pipeline without performing synthesis, so that startup warm-up does not produce audio output.
5. THE `KokoroJapaneseTTS` class SHALL expose a method to pre-load its pipeline without performing synthesis, so that startup warm-up does not produce audio output.
6. WHEN both pipelines are loaded successfully at startup, THE Server SHALL log a confirmation message indicating warm-up is complete.

### Requirement 4: Latency Observability

**User Story:** As a developer, I want the pipeline logs to capture the timing of first-sentence synthesis start relative to LLM first-token, so that I can verify the streaming fix is working and measure TTFA improvements.

#### Acceptance Criteria

1. WHEN TTS_Worker begins synthesising the first sentence of a turn, THE TTS_Worker SHALL log the elapsed time since `PipelineState.speech_end_time` as `ttfs_ms` (time-to-first-synthesis).
2. WHEN TTS_Worker enqueues the first WAV chunk of a turn to `audio_out_queue`, THE TTS_Worker SHALL log the elapsed time since `PipelineState.speech_end_time` as `tts_enqueue_ms`.
3. WHEN a synthesis task completes, THE TTS_Worker SHALL log the sentence index within the current turn, the synthesis duration in milliseconds, and the WAV size in bytes.
