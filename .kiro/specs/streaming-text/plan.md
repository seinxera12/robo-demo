# Simulated Word-by-Word Streaming

## Current Issue
LLM response arrives near-instantly — render it word by word on the frontend to simulate natural streaming.

## Proposed changes plan
### 1. SIMULATED STREAM RENDERER

Once the full LLM response is received, render it to the chat window word by word with a calculated per-word delay instead of displaying it all at once.

Delay per word = total_response_length_in_words / target_duration
Target duration: scale between 3s (short responses) and 6s (long responses). A response of ~60+ words should complete in ~5–6s. Short responses (under 15 words) should feel snappy — cap minimum duration at ~1.5s.

### 2. TTS COORDINATION

TTS synthesis should still begin as soon as the full LLM response text is available — do not wait for the simulated render to complete before starting TTS. The simulated stream is frontend-only; it does not affect when TTS starts or how it receives text.

### 3. INPUT LOCK INTERACTION

The send button and mic button remain disabled for the entire duration of the simulated word render. Input is re-enabled only after the final word is displayed. The simulated render is not interruptible — it always runs to completion before handing control back to the user.

## DO NOT MODIFY

LLM API call, response handling, TTS pipeline, chat history, async pipeline structure, log format or levels.