# TTS Latency Audit & Fix Guide
> For coding agent use. Audit the codebase against every checklist item, then apply fixes in priority order.

---

## Observed symptoms (from logs)

| Metric | Value |
|---|---|
| LLM `turn_complete` timestamp | 17:24:35 |
| TTS `route` (first sentence received) | 17:24:35 |
| TTS `synthesised` (first sentence done) | 17:24:38 |
| `AUDIO_OUT first_audio_chunk` | 17:24:38 |
| **TTS synthesis latency, sentence 1** | **3,562 ms** (Japanese, 12 chars → 2,375 ms audio) |
| **TTS synthesis latency, sentence 2** | **4,782 ms** (Japanese, long → 15,875 ms audio) |
| Gap between sentence 1 `synthesised` and sentence 2 `route` | ~0 ms (back-to-back, no overlap) |
| English TTS synthesis latency | 3,203 ms for 7,375 ms audio |

Key ratio: Japanese engine produces audio at **0.67× real-time** (slower than real-time). English at **2.3× real-time**. Both block first audio by ~3.2–3.6 s.

---

## Audit checklist

### A. LLM → TTS handoff timing

- [ ] **A1.** Locate where the LLM stream is consumed. Confirm it is sentence-streaming: does the code split on sentence boundaries (`.`, `。`, `?`, `!`, `\n`) as tokens arrive, or does it wait for `turn_complete` before sending anything to TTS?
  - **Red flag**: any pattern like `full_text = await llm.generate(); tts.synthesise(full_text)` — this is full-response buffering.
  - **Expected**: first TTS call fires as soon as a sentence boundary token is seen in the stream.

- [ ] **A2.** Check the `tts.route` log timestamp vs LLM `call2_first_token` timestamp. In turn 1: LLM first token ~17:24:34 (+1703 ms from turn_start), `turn_complete` at 17:24:35, TTS `route` also at 17:24:35. This suggests TTS is called only after `turn_complete` — i.e. the full response is buffered before TTS starts. **Confirm this in code.**

- [ ] **A3.** If sentence streaming is implemented, check whether there is an `await` on the TTS call inside the stream loop that blocks consuming the next tokens until synthesis finishes.

- [ ] **A4.** Check whether the text sent to TTS matches what was available at first-token time or only at stream-end. Log shows sentence 1 is only "ラーメンのレシピですね。" (12 chars) — this could be correct sentence splitting OR it could be the entire response was buffered and then split. Verify by checking if sentence 2 was dispatched to TTS before or after sentence 1 synthesis completed.

---

### B. TTS synthesis pipeline internals

- [ ] **B1.** Confirm whether Kokoro model is loaded into memory at startup or loaded on first request. A cold-load on first synthesis would add 1–3 s on the first call per session.

- [ ] **B2.** Check whether the KokoroJapaneseTTS and KokoroTTS instances are singletons (loaded once) or instantiated per request.

- [ ] **B3.** Confirm device used: `model.device` — should be `cuda:0` for GPU, `cpu` for CPU. On CPU, Japanese Kokoro at 0.67× real-time is expected. English at 2.3× real-time on CPU is also consistent. **Document the actual device in your audit findings.**

- [ ] **B4.** Check if synthesis runs synchronously in the async event loop (blocking), or is dispatched via `asyncio.run_in_executor` / `loop.run_in_executor(executor, ...)`. A synchronous Kokoro call inside an `async def` blocks the entire event loop for the full 3.5 s.

- [ ] **B5.** Check whether audio is chunked/streamed out of Kokoro during synthesis, or if the full WAV is generated before any bytes are sent. Kokoro's `generate()` API is batch — it returns complete audio. Check if a streaming API or phoneme-level output is used.

- [ ] **B6.** Check the sentence splitting logic: what is the minimum sentence length before dispatch? If there is a minimum token/char threshold (e.g. "wait for at least 20 chars"), this adds latency on short first sentences.

---

### C. Inter-sentence gap (sentence 1 → sentence 2)

From logs:
- Sentence 1 `synthesised` at 17:24:38.000
- Sentence 2 `route` at 17:24:38.000 (same timestamp)
- Sentence 2 `synthesised` at 17:24:43.000 (4,782 ms later)

The gap in playback: sentence 1 audio = 2,375 ms. Sentence 1 starts playing at 17:24:38. Sentence 2 synthesis starts at 17:24:38 and finishes at 17:24:43. Since sentence 1 finishes playing at ~17:24:40.4 and sentence 2 is ready at 17:24:43, there is a **~2.6 s silence gap between sentences during playback.**

- [ ] **C1.** Confirm the audio queue implementation: is sentence 2 synthesis started while sentence 1 is still playing? Correct behaviour: sentence N+1 synthesis begins immediately after sentence N is dispatched to queue, not after sentence N playback ends.

- [ ] **C2.** Check if TTS calls for subsequent sentences are sequential-awaited (sentence 2 starts only after sentence 1 synthesis returns) or pipelined (sentence 2 synthesis runs concurrently with sentence 1 playback).

- [ ] **C3.** Check whether the audio queue drains before the next chunk is enqueued (pull model) vs chunks being pushed as soon as synthesised (push model). A pull model introduces jitter equal to synthesis time.

---

### D. Language detection and routing

- [ ] **D1.** STT detects `lang=Japanese` and logs it. The `forwarded_to_llm` log shows `lang=en` — the detected language is not propagated. TTS `route` log shows `passed_lang=en, effective_lang=ja` — meaning the TTS router is re-detecting language from the text. This adds a detection step and risks misrouting short mixed-language sentences. **Fix: pass STT-detected language through LLM context and into the TTS call.**

- [ ] **D2.** Check if the language re-detection in TTS router uses a library call (e.g. `langdetect`, `lingua`) that has its own latency. Even 20–50 ms per call compounds.

- [ ] **D3.** Verify the TTS route selection is a simple conditional, not a dynamic model lookup with I/O.

---

## Root cause summary

Based on log evidence, the most likely architecture is:

```
LLM stream → buffer full response → split into sentences → sequential TTS calls → audio queue
```

This means:
1. No audio starts until the full LLM response is received (~1 s wasted)
2. Sentence 1 synthesis blocks before sentence 2 is dispatched (~3.5 s wasted on CPU)
3. Total TTFA = STT + LLM + full-response-buffer-wait + TTS synthesis of sentence 1

Target architecture:

```
LLM stream → sentence boundary detector → dispatch sentence N to TTS immediately
                                         → while sentence N synthesises, keep consuming LLM tokens
                                         → audio queue: play sentence N when ready, sentence N+1 already being synthesised
```

---

## Fix plan (priority order)

### Fix 1 — Stream LLM tokens into TTS without full-response buffering `[highest impact]`

**Goal**: first TTS synthesis call fires on first sentence boundary in the token stream, not at `turn_complete`.

```python
# Pattern to implement
async def stream_to_tts(llm_stream, tts_engine, audio_queue):
    buffer = ""
    async for token in llm_stream:
        buffer += token
        if has_sentence_boundary(buffer):
            sentence, buffer = split_at_boundary(buffer)
            # Do NOT await here — fire and forget into a task
            asyncio.create_task(synthesise_and_enqueue(sentence, tts_engine, audio_queue))
    if buffer.strip():
        asyncio.create_task(synthesise_and_enqueue(buffer, tts_engine, audio_queue))

def has_sentence_boundary(text):
    # Include Japanese boundaries
    return bool(re.search(r'[.。!！?？\n]', text))
```

**Check**: after this fix, TTS `route` timestamp should be within ~200 ms of LLM `call2_first_token`, not at `turn_complete`.

---

### Fix 2 — Run TTS synthesis off the event loop `[prevents blocking]`

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

_tts_executor = ThreadPoolExecutor(max_workers=2)  # one per engine

async def synthesise_and_enqueue(text, engine, queue):
    loop = asyncio.get_event_loop()
    wav = await loop.run_in_executor(_tts_executor, engine.synthesise, text)
    await queue.put(wav)
```

Without this, a 3.5 s CPU synthesis blocks the entire async server — no other requests or stream consumption happens during that window.

---

### Fix 3 — Pipeline sentence synthesis concurrently with playback `[eliminates inter-sentence gap]`

```python
# Audio queue consumer plays sentence N
# TTS producer synthesises sentence N+1 simultaneously
# The queue should hold pre-synthesised WAV bytes, not text-to-be-synthesised

# Wrong pattern (pull, serial):
for sentence in sentences:
    wav = await tts.synthesise(sentence)   # blocks
    await play(wav)

# Correct pattern (push, pipelined):
synthesis_tasks = [asyncio.create_task(synthesise(s)) for s in sentences_as_they_arrive]
# playback dequeues as soon as each task completes, in order
```

For the observed case: sentence 1 (2,375 ms audio, 3,562 ms to synthesise) plays while sentence 2 (4,782 ms to synthesise) is being synthesised in parallel. With pipelining, sentence 2 is ready before sentence 1 finishes playing → zero inter-sentence gap.

---

### Fix 4 — Warm model pre-load and synthesis cache `[reduces cold-start and repeated phrases]`

```python
# At server startup, not on first request:
class KokoroJapaneseTTS:
    def __init__(self):
        self.model = load_kokoro_model(lang='ja')   # load once
        self._warmup()

    def _warmup(self):
        # Synthesise a dummy string to initialise JIT / ONNX graph
        self.synthesise("はい")

# Optional: LRU cache for common short phrases ("はい", "わかりました", etc.)
from functools import lru_cache

@lru_cache(maxsize=64)
def synthesise_cached(text: str) -> bytes:
    return self.model.generate(text)
```

---

### Fix 5 — Propagate detected language through pipeline `[avoids re-detection cost + misrouting]`

```python
# STT result:
stt_result = {"text": "ラーメン...", "lang": "ja"}

# Forward lang to LLM context and TTS call:
tts_engine = router.select_engine(lang=stt_result["lang"])  # skip re-detection
wav = tts_engine.synthesise(text, lang=stt_result["lang"])
```

---

### Fix 6 — GPU acceleration `[2–5× synthesis speedup, resource dependent]`

If the deployment can support CUDA:

```python
# Check and log device at startup
import torch
device = "cuda" if torch.cuda.is_available() else "cpu"
logger.info(f"TTS device: {device}")
model = KokoroModel.load(device=device)
```

On CPU, Japanese Kokoro at 0.67× real-time means every second of speech takes 1.5 s to synthesise — structurally incompatible with low-latency streaming. Even a modest GPU (T4) typically achieves 10–20× real-time, reducing 3,562 ms → ~200 ms.

Until GPU is available, fixes 1–3 are the only CPU-side improvements possible.

---

## Expected outcome after fixes 1–3 (CPU, no GPU change)

| Metric | Before | After fixes 1–3 |
|---|---|---|
| TTFA (Japanese, CPU) | 5,671 ms | ~2,700 ms (STT 1171 + LLM intent 391 + LLM response first-token ~141 + TTS fires immediately, overlaps remaining LLM stream) |
| Inter-sentence gap | ~2,600 ms | ~0 ms (pipelined) |
| Event loop blocked during synthesis | Yes, 3.5 s | No |
| TTFA with GPU (fix 6) | 5,671 ms | ~1,900 ms |

---

## Files to audit

Locate and inspect these (names may differ):

| What to find | Look for |
|---|---|
| LLM stream consumer | `async for token in stream` or `on_token` callback |
| Sentence splitter | regex on `.。!？\n` |
| TTS dispatch call | where `tts.synthesise(` or `engine.generate(` is called |
| Audio queue | `asyncio.Queue` or equivalent holding WAV bytes |
| TTS class init | where Kokoro model is loaded |
| Language routing | `if lang == 'ja'` or similar engine selector |
