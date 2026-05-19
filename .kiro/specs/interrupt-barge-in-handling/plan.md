Interrupt & Barge-In
Two distinct interaction phases with different input rules and TTS cancellation on barge-in.

1. Input lock during LLM generation

Disable the text send button and the mic button for the duration of LLM generation — from the moment the request is sent until the last token is received and rendered. This lock is tied directly to the LLM generation lifecycle, not to any other state. Re-enable both buttons as soon as generation is complete.
2. TTS barge-in

Once LLM generation is complete and TTS playback is running, both the send button and mic button are active. If the user submits a new text message or presses the mic button while TTS is playing:

  1. Cancel the active TTS synthesis and stop audio playback immediately.
  2. Flush any pending audio buffer for the interrupted turn.
  3. Release audio resources cleanly — await full cleanup before proceeding.
  4. Process the new user input through the LLM → TTS pipeline as normal.
3. Logging & pipeline integrity

- Match existing log format and levels for all new log statements.
  - All cancellations must be awaited — no fire-and-forget.
  - Do not alter the async pipeline structure outside of what is described above.
Do not modify

LLM generation logic, TTS synthesis parameters, voice input / VAD pipeline, chat history, existing state management, log format or levels.