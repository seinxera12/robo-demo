# Robo — Prompt Engineering & LLM Architecture Guide
> Implementation reference for the coding agent

---

## 0. Project Summary

**Robo** is a voice-to-voice (and text) assistant primarily serving Japanese users (English secondary), deployed across multiple fixed environments: enterprise buildings, receptions, and desktop applications. It runs on lightweight models (Ollama local / Groq free tier) and must compensate for model limitations through strong prompt engineering and a well-structured orchestration pipeline.

**Core design philosophy**: The LLM is a component, not the system. Routing, context injection, confidence gating, and structured output handling carry most of the intelligence.

---

## 1. Recommended Models

### Groq (primary, highest quality)
- **Model**: `llama-3.3-70b-versatile`
- **Why**: Best overall quality on free Groq tier. Strong multilingual (JA/EN), good instruction following, fast inference on Groq's hardware. Handles structured JSON output reliably.
- **Context window**: 128k tokens — use it for richer RAG injection.
- **Fallback**: `llama-3.1-8b-instant` for latency-critical voice turns.

### Ollama (local, small + smart)
- **Model**: `qwen2.5:7b` (primary recommendation)
- **Why**: Best small model for Japanese + English among locally-runnable options. Qwen2.5 was trained heavily on CJK data. 7B fits on most hardware, strong instruction following, supports system prompts well.
- **Alternative**: `gemma3:4b` — smaller, decent JA support, faster on CPU.
- **Avoid**: `mistral:7b` — weak Japanese support.

### Prompt adaptation per model
```
GROQ (llama-3.3-70b):
- More detailed system prompts are fine (large context, good adherence)
- Can handle nuanced conditional instructions
- Structured JSON output is reliable without heavy scaffolding

OLLAMA/qwen2.5:7b:
- Keep system prompts concise and directive
- Use simpler JSON schemas (fewer fields, flat structure)
- Repeat key constraints in user-turn prefix for small models
- Use explicit output format examples in the prompt
```

---

## 2. Architecture Overview

```
[STT / raw text input]
        ↓
[Language detection + confidence gate]
        ↓
[Intent classifier] → structured JSON: { intent, lang, confidence, slot }
        ↓
[Router] → one of:
    A. General Q&A      → LLM direct
    B. Environment RAG  → retrieve docs → LLM with context
    C. Web search       → search API → LLM with results
    D. Small talk/OOS   → lightweight handler
        ↓
[Prompt assembler]
  base_prompt + deployment_block + route_context + conversation_history
        ↓
[LLM completion]
        ↓
[Response post-processor] → TTS / text output
```

---

## 3. Modular Prompt Block System

Prompts are never a single monolithic string. They are assembled from independent blocks at runtime.

### 3.1 Block Types

| Block | File / Source | Always included? |
|---|---|---|
| `base_prompt` | `prompts/base.txt` | Yes |
| `deployment_block` | `config/deployment.yaml` → rendered | Yes |
| `language_block` | Selected by detected language | Yes |
| `route_context` | Injected by router | Yes |
| `rag_context` | Retrieved chunks | Only on ENV route |
| `search_context` | Web search results | Only on SEARCH route |
| `conversation_history` | Session memory | Yes (capped) |

### 3.2 Assembly Order (system prompt)

```
[base_prompt]
[deployment_block]
[language_block]
---
[route_context]        ← injected here per turn
[rag_context]          ← if RAG route
[search_context]       ← if search route
```

Conversation history goes into the `messages` array as alternating user/assistant turns, NOT into the system prompt.

---

## 4. Base Prompt

This is the fixed identity and behavior layer. It does not change across deployments.

```
# Identity
You are Robo, a helpful voice and text assistant. You are friendly, natural, and concise.
Your primary users speak Japanese. You also serve English speakers.
Always respond in the same language the user is speaking.

# Tone
Use a warm, casual, friendly tone — not formal or stiff.
In Japanese, default to polite but friendly style (です/ます), not keigo (honorifics),
unless the deployment config specifies otherwise.
Keep responses short and natural — this is a voice interface, not a document.
Avoid bullet points or markdown formatting in spoken responses.

# Core behavior
- Answer clearly and directly.
- If you don't know something, say so simply. Do not make things up.
- If a question is outside your knowledge, offer to search for it or suggest who to ask.
- Never break character or discuss your own model, architecture, or training.

# Output format
When asked to classify or route (internal turns only), respond ONLY with valid JSON.
For all user-facing turns, respond with plain natural language only.
```

---

## 5. Deployment Block

Each deployment has a `deployment.yaml` config that renders into the deployment block injected after the base prompt.

### 5.1 Config Schema (`deployment.yaml`)

```yaml
deployment_id: "building_a_reception"
deployment_type: "reception"          # reception | enterprise | desktop
location_name: "Sakura Tower, 2F Reception"
language_primary: "ja"
language_secondary: "en"
tone_override: null                    # null | "formal" | "casual"
environment_docs:
  - path: "docs/floor_map.md"
  - path: "docs/tenant_directory.json"
  - path: "docs/facilities.md"
session_memory_turns: 6               # how many turns to keep
web_search_enabled: true
out_of_scope_response:
  ja: "申し訳ありませんが、それについてはお答えできません。スタッフにお声がけください。"
  en: "I'm sorry, I can't help with that. Please speak with a staff member."
```

### 5.2 Rendered Deployment Block Template

```
# Deployment context
You are deployed at: {location_name}.
Your role here: {deployment_type_description}

## What you know about this location
{injected_doc_summaries_or_full_text}

## What you can help with here
{capabilities_list_from_config}

## What is out of scope
{out_of_scope_description}
If asked something out of scope, respond with:
"{out_of_scope_response[detected_language]}"
```

### 5.3 Deployment type descriptions (used in block rendering)

```python
DEPLOYMENT_DESCRIPTIONS = {
    "reception": "You help visitors and staff at the building entrance. Answer questions about floors, rooms, tenants, facilities, and directions. You do not handle bookings or security matters.",
    "enterprise": "You assist employees with internal questions about the company, facilities, and procedures. You have access to internal documentation.",
    "desktop": "You are a personal assistant on the user's computer. Help with general questions, productivity tasks, and information lookup."
}
```

---

## 6. Language Block

Injected after the deployment block. Selected based on the detected language from STT/VAD.

### Japanese block (`prompts/lang_ja.txt`)
```
# Language mode: Japanese
Respond in Japanese. Use natural, conversational Japanese (です/ます調).
If the user mixes in English words, respond naturally in Japanese.
Keep sentences short — this is a voice response.
Do not use markdown, lists, or formatting in your response.
```

### English block (`prompts/lang_en.txt`)
```
# Language mode: English
Respond in English. Keep responses concise and natural for voice.
Do not use markdown, lists, or formatting in your response.
```

### Uncertainty block (low confidence on language detection)
```
# Language mode: uncertain
The input language was not clearly detected.
Respond in Japanese first, then offer the same in English briefly.
Example: "〇〇です。 / That means: [English]."
```

---

## 7. Intent Classification

This is a dedicated LLM call (or a prefix call) that runs before the main response. It is NOT shown to the user.

### 7.1 Classifier Prompt

```
You are an intent classifier for a voice assistant called Robo.
Given the user's message, output ONLY a JSON object with this exact structure.
Do not output any text before or after the JSON.

{
  "intent": "<one of: general | environment | web_search | small_talk | out_of_scope | clarify>",
  "language": "<ja | en | unknown>",
  "confidence": <float 0.0 to 1.0>,
  "needs_clarification": <true | false>,
  "clarification_reason": "<brief reason if needs_clarification is true, else null>",
  "query_clean": "<the user's intent restated clearly in one sentence>"
}

Intent definitions:
- general: factual questions, explanations, general knowledge
- environment: questions about this specific location (floors, rooms, people, facilities, directions)
- web_search: questions requiring current/live information (news, weather, prices, events)
- small_talk: greetings, thanks, casual conversation
- out_of_scope: requests Robo cannot or should not handle
- clarify: input was too unclear to classify (STT error likely)

Examples:
Input: "3階にトイレはありますか？"
Output: {"intent":"environment","language":"ja","confidence":0.97,"needs_clarification":false,"clarification_reason":null,"query_clean":"Is there a restroom on the 3rd floor?"}

Input: "今日の天気は？"
Output: {"intent":"web_search","language":"ja","confidence":0.95,"needs_clarification":false,"clarification_reason":null,"query_clean":"What is today's weather?"}

Input: "ええと、あの、なんか..."
Output: {"intent":"clarify","language":"ja","confidence":0.2,"needs_clarification":true,"clarification_reason":"Input too vague or incomplete","query_clean":"Unclear input"}
```

### 7.2 Classifier Implementation Notes

```python
# For small local models (ollama/qwen2.5:7b), add this prefix to the user message:
USER_PREFIX_SMALL_MODEL = "Classify this input and return only JSON:\n\n"

# Confidence thresholds
CLARIFY_THRESHOLD = 0.45       # below this → trigger clarification
ENVIRONMENT_THRESHOLD = 0.65   # for RAG route, require higher confidence

# Fallback: if JSON parse fails, default to "general" intent
def safe_parse_intent(raw_output: str) -> dict:
    try:
        return json.loads(raw_output.strip())
    except Exception:
        return {
            "intent": "general",
            "language": "unknown",
            "confidence": 0.5,
            "needs_clarification": False,
            "clarification_reason": None,
            "query_clean": raw_output[:200]
        }
```

---

## 8. Clarification Strategy

When `needs_clarification: true` or confidence is below threshold:

### Step 1: Best-effort response attempt
Before asking for clarification, check if the partial input is enough to give a reasonable response. If `confidence >= 0.45`, attempt a response and append a soft confirmation.

### Step 2: Clarification prompt templates

```python
CLARIFICATION_TEMPLATES = {
    "ja": {
        "low_confidence": "すみません、もう一度おっしゃっていただけますか？",
        "partial_match": "{best_guess_response}……そういったことでよろしいでしょうか？",
        "too_vague": "もう少し詳しく教えていただけますか？",
    },
    "en": {
        "low_confidence": "Sorry, could you say that again?",
        "partial_match": "{best_guess_response} — is that what you were asking?",
        "too_vague": "Could you tell me a bit more?",
    }
}
```

### Clarification logic flow

```
confidence < 0.3  → ask to repeat (low_confidence template)
0.3 ≤ conf < 0.45 → attempt partial response + confirm (partial_match template)
conf ≥ 0.45       → proceed normally, no clarification
```

---

## 9. Route Handlers

### 9.1 Route A — General Q&A

No extra context needed. Just assemble base + deployment + language + history and call the LLM.

```python
GENERAL_ROUTE_CONTEXT = """
# Task
Answer the user's question directly and conversationally.
Keep it brief — 1-3 sentences for voice responses.
"""
```

### 9.2 Route B — Environment / RAG

```python
RAG_ROUTE_CONTEXT_TEMPLATE = """
# Task
The user is asking about this specific location. Use ONLY the information provided below.
If the answer is not in the documents, say you're not sure and suggest asking staff.
Do not guess or invent details about the location.

# Location documents
{retrieved_chunks}
"""
```

**RAG retrieval notes:**
- Use embedding similarity search over the deployment's docs (floor maps, directories, etc.)
- Retrieve top 3-5 chunks, max ~800 tokens total for small models, ~2000 for Groq
- Include source label per chunk: `[Floor Map - 3F]`, `[Tenant Directory]`, etc.
- For structured data (JSON directories), convert to natural language summaries before embedding

**Chunk format example:**
```
[Floor Map - 3F]
3階には会議室A（30名収容）、会議室B（10名収容）、男女トイレがあります。
コピー機は廊下の突き当たりにあります。

[Tenant Directory]
株式会社テックコープ: 5階 東側 / TechCorp Inc: 5F East Wing
```

### 9.3 Route C — Web Search

```python
SEARCH_ROUTE_CONTEXT_TEMPLATE = """
# Task
The user asked about current or live information. Use the search results below to answer.
Summarize the key facts in 1-3 sentences. Cite nothing — just give the answer naturally.
If results don't contain the answer, say so simply.

# Search results
{search_results_summary}
"""
```

**Search trigger conditions** (from classifier):
- intent == "web_search"
- OR query contains time signals: 今日/今週/最新/現在/today/latest/current/now
- `web_search_enabled: true` in deployment config

**Search result preprocessing:**
```python
# Strip HTML, limit to top 2 results, summarize to ~300 tokens before injecting
# For local models, reduce to ~150 tokens
def prepare_search_context(results: list, model_tier: str) -> str:
    token_budget = 300 if model_tier == "groq" else 150
    # truncate and format results
```

### 9.4 Route D — Small Talk / OOS

Small talk uses a lightweight hardcoded response map — do NOT call the full LLM pipeline for greetings.

```python
SMALL_TALK_RESPONSES = {
    "ja": {
        "greeting": ["こんにちは！何かお手伝いできることはありますか？", "いらっしゃいませ！何かご質問はありますか？"],
        "thanks": ["どういたしまして！", "お役に立てて嬉しいです。"],
        "goodbye": ["ありがとうございました！またいつでもどうぞ。"],
        "how_are_you": ["元気ですよ！あなたはいかがですか？"],
    },
    "en": {
        "greeting": ["Hello! How can I help you today?", "Hi there! What can I do for you?"],
        "thanks": ["You're welcome!", "Happy to help!"],
        "goodbye": ["Goodbye! Feel free to ask anytime."],
        "how_are_you": ["I'm doing well! How about you?"],
    }
}

# OOS: use deployment config's out_of_scope_response
# Do NOT call LLM for OOS — return the configured message directly
```

---

## 10. Session Memory

Keep conversation history short — optimized for voice/kiosk contexts.

```python
SESSION_CONFIG = {
    "max_turns": 6,           # from deployment config, default 6
    "max_tokens_history": 600,  # hard cap on history tokens injected
    "memory_scope": "session",  # no cross-session persistence for kiosk
}

# History format for messages array:
# [
#   {"role": "user", "content": "3階のトイレはどこですか？"},
#   {"role": "assistant", "content": "3階のトイレはエレベーターを出て右手にございます。"},
#   ...
# ]

# Truncation: always keep the most recent N turns
# If a turn's content is very long (RAG response), trim to 120 tokens for history
```

---

## 11. Response Post-Processing

Before sending to TTS, clean the LLM output:

```python
def clean_for_voice(text: str, lang: str) -> str:
    # Remove markdown artifacts
    text = re.sub(r'[*_`#>\-]+', '', text)
    # Remove parenthetical asides that don't speak well
    text = re.sub(r'\(.*?\)', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # For Japanese: remove unnecessary spaces between CJK characters
    if lang == "ja":
        text = re.sub(r'(?<=[\u3000-\u9fff])\s+(?=[\u3000-\u9fff])', '', text)
    return text
```

---

## 12. Full Prompt Assembly (Pseudocode)

```python
def assemble_prompt(
    user_input: str,
    intent_result: dict,
    deployment_config: dict,
    session_history: list,
    retrieved_context: str = None,
) -> tuple[str, list]:

    lang = intent_result["language"]
    intent = intent_result["intent"]

    # 1. Load blocks
    system_parts = []
    system_parts.append(load_file("prompts/base.txt"))
    system_parts.append(render_deployment_block(deployment_config))
    system_parts.append(load_lang_block(lang))

    # 2. Add route context
    if intent == "general":
        system_parts.append(GENERAL_ROUTE_CONTEXT)
    elif intent == "environment" and retrieved_context:
        system_parts.append(RAG_ROUTE_CONTEXT_TEMPLATE.format(
            retrieved_chunks=retrieved_context
        ))
    elif intent == "web_search" and retrieved_context:
        system_parts.append(SEARCH_ROUTE_CONTEXT_TEMPLATE.format(
            search_results_summary=retrieved_context
        ))

    system_prompt = "\n\n".join(system_parts)

    # 3. Build messages array
    messages = []
    messages.extend(trim_history(session_history, max_turns=deployment_config["session_memory_turns"]))
    messages.append({"role": "user", "content": user_input})

    return system_prompt, messages
```

---

## 13. Two-Call Pipeline (per user turn)

```
Turn N:
  Call 1: Intent classifier (fast, small prompt, JSON output)
    → model: same LLM, but classifier-specific system prompt
    → max_tokens: 150
    → temperature: 0.0 (deterministic classification)

  [Router decides path, retrieves context if needed]

  Call 2: Main response (full assembled prompt)
    → model: same LLM
    → max_tokens: 200 (voice) / 400 (text)
    → temperature: 0.6–0.7
    → stream: true (for voice latency)
```

**Latency optimization for voice:**
- Run intent classification with `max_tokens: 100` and `temperature: 0`
- For small_talk/OOS intents, skip Call 2 entirely — use hardcoded response
- For Groq: use streaming, pipe first tokens to TTS as soon as sentence boundary detected
- For Ollama: pre-warm the model context on startup

---

## 14. Prompt Templates — Model-Specific Variants

### For Groq (llama-3.3-70b) — richer instructions OK

The full prompts as described in sections 4–9 work as-is.

### For Ollama/qwen2.5:7b — simplified variants

**Simplified base (qwen2.5 variant):**
```
You are Robo, a friendly assistant. You help people at {location_name}.
Always reply in the same language the user is using.
Keep answers short and natural (1-3 sentences). No lists or formatting.
If you don't know, say so honestly.
```

**Simplified classifier (qwen2.5 variant) — add output example directly:**
```
Classify the input. Reply with ONLY this JSON, nothing else:
{"intent":"...","language":"...","confidence":0.0,"needs_clarification":false,"query_clean":"..."}

Intents: general, environment, web_search, small_talk, out_of_scope, clarify

Input: {user_input}
```

**Key difference**: For small models, always include a filled-in JSON example with the exact field names and types, directly before the input. This dramatically improves schema adherence.

---

## 15. Configuration Files Structure

```
robo/
├── prompts/
│   ├── base.txt
│   ├── base_small.txt          # qwen2.5/gemma variant
│   ├── lang_ja.txt
│   ├── lang_en.txt
│   ├── lang_unknown.txt
│   ├── classifier.txt
│   └── classifier_small.txt
├── config/
│   ├── deployment.yaml         # per-environment config
│   └── model.yaml              # model selection + params
├── docs/                       # deployment-specific docs for RAG
│   ├── floor_map.md
│   ├── tenant_directory.json
│   └── facilities.md
└── pipeline/
    ├── intent.py               # classifier call + parse
    ├── router.py               # routing logic
    ├── rag.py                  # retrieval
    ├── search.py               # web search integration
    ├── assembler.py            # prompt assembly
    └── postprocess.py          # voice cleaning
```

---

## 16. Edge Cases & Failure Handling

| Scenario | Handling |
|---|---|
| JSON parse failure from classifier | Default to `general` intent, log for debugging |
| RAG returns no relevant chunks | Fall back to general response + "詳しくはスタッフへ" |
| Web search fails / times out | Respond with "最新情報は確認できませんでした" and offer what's known |
| LLM returns empty or malformed output | Retry once; if fails, return a generic fallback message |
| Language detected as `unknown` | Use bilingual clarification block, respond JA-first |
| STT input is pure noise / empty | Skip pipeline entirely, play "もう一度おっしゃってください" audio clip |
| Model context overflow | Trim oldest history turns first, keep system prompt intact |

---

## 17. Optional / Future Extensions

These are **not** primary requirements but the architecture is designed to accommodate them:

- **Multi-turn intent persistence**: If user asks a follow-up without re-specifying intent ("その隣は？"), carry forward the previous intent slot.
- **Formal tone mode**: Add a `tone: formal` to deployment config, swap `lang_ja.txt` for `lang_ja_formal.txt` with keigo instructions.
- **User personalization**: Add optional `user_profile` block to deployment config (name, role, floor) for desktop deployments.
- **Action intents**: When ready to add actions (room booking, staff lookup), add `action` as an intent type and a corresponding action block — the pipeline already supports this via the router.
- **Hybrid RAG+Search**: Extend router to allow `environment+web_search` combined context injection when both are relevant — currently kept separate per spec.

---

*End of guide — hand this to the coding agent to implement the pipeline.*
