"""
FastAPI server entry point for the Lightweight Voice Demo.

Responsibilities:
- Define the lifespan context manager for startup/shutdown
- Register WebSocket endpoints: /ws (AudioClient) and /ws/ui (BrowserUI)
- Mount static files from ui/dist/ at the HTTP root
- Maintain a set of connected BrowserUI WebSocket clients for broadcasting
- Instantiate and hold all component instances in app.state
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Set

import groq
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from server.config import Config
from server.lang.detector import LanguageDetector
from server.llm.assembler import DeploymentConfig, PromptAssembler, detect_model_tier
from server.llm.chain import LLMChain
from server.llm.gemini_llm import GeminiLLMBackend
from server.llm.groq_llm import GroqLLMBackend
from server.llm.intent import IntentClassifier
from server.llm.postprocess import PostProcessor
from server.llm.router import Router
from server.log import pipeline_event, pipeline_warn
from server.models import TranscriptionResult
from server.pipeline import PipelineState, VoicePipeline
from server.search.tavily_search import TavilySearchClient
from server.stt.groq_stt import GroqSTTBackend
from server.tts.kokoro_tts import KokoroJapaneseTTS, KokoroTTS
from server.tts.tts_router import TTSRouter
from server.config import UI_DIST_DIR, PROMPTS_DIR, DEPLOYMENT_YAML

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state: connected BrowserUI clients and active pipelines
# ---------------------------------------------------------------------------

# Set of connected BrowserUI WebSocket clients
ui_clients: Set[WebSocket] = set()

# Active AudioClient pipelines keyed by session_id, ordered by insertion
# (dict preserves insertion order in Python 3.7+)
active_pipelines: Dict[str, VoicePipeline] = {}


# ---------------------------------------------------------------------------
# Broadcast helper
# ---------------------------------------------------------------------------


async def broadcast_to_ui(message: dict) -> None:
    """Send JSON message to all connected BrowserUI clients."""
    if not ui_clients:
        return
    text = json.dumps(message)
    disconnected: Set[WebSocket] = set()
    for ws in list(ui_clients):
        try:
            await ws.send_text(text)
        except Exception:
            disconnected.add(ws)
    # Use difference_update (in-place) instead of -= to avoid Python treating
    # ui_clients as a local variable due to the assignment, which would raise
    # UnboundLocalError: cannot access local variable 'ui_clients'.
    ui_clients.difference_update(disconnected)


# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: startup → yield → shutdown."""

    # 1. Load configuration — exit immediately if GROQ_API_KEY is missing
    try:
        config = Config.from_env()
    except KeyError:
        sys.exit(
            "ERROR: GROQ_API_KEY environment variable is not set. "
            "Please copy .env.example to .env and add your Groq API key."
        )

    # 2. Configure logging
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger.info("Log level set to %s", config.log_level.upper())

    # 3. Instantiate all components

    # Groq async client
    groq_client = groq.AsyncGroq(api_key=config.groq_api_key)

    # STT backend
    stt_backend = GroqSTTBackend(client=groq_client, model=config.groq_stt_model)

    # LLM backends and chain
    groq_llm = GroqLLMBackend(client=groq_client, model=config.groq_llm_model)

    gemini_llm: GeminiLLMBackend | None = None
    if config.gemini_api_key:
        gemini_llm = GeminiLLMBackend(api_key=config.gemini_api_key)
        llm_chain = LLMChain(primary=groq_llm, fallback=gemini_llm)
        logger.info("GeminiLLMBackend enabled as LLM fallback.")
    else:
        # No Gemini key — use Groq as both primary and fallback (chain still works)
        llm_chain = LLMChain(primary=groq_llm, fallback=groq_llm)
        logger.info("GEMINI_API_KEY not set — LLM fallback disabled (Groq only).")

    # Task 14.1 — Load DeploymentConfig at startup
    try:
        deployment_config = DeploymentConfig.from_yaml(DEPLOYMENT_YAML)
        logger.info(
            "DeploymentConfig loaded from %s "
            "(deployment_id=%s, type=%s)",
            DEPLOYMENT_YAML,
            deployment_config.deployment_id,
            deployment_config.deployment_type,
        )
    except FileNotFoundError:
        deployment_config = DeploymentConfig.default()
        logger.info(
            "deployment.yaml not found at %s — using default DeploymentConfig "
            "(deployment_type=desktop, language_primary=en, web_search_enabled=False)",
            DEPLOYMENT_YAML,
        )

    # Task 14.2 — Detect model tier at startup
    model_tier = detect_model_tier(config.groq_llm_model)
    logger.info("Model tier detected: %s (model=%s)", model_tier, config.groq_llm_model)

    # Language detector
    lang_detector = LanguageDetector()

    # Optional Tavily search client
    tavily_client: TavilySearchClient | None = None
    if config.tavily_api_key:
        tavily_client = TavilySearchClient(api_key=config.tavily_api_key)
        logger.info("TavilySearchClient enabled for web search.")
    else:
        logger.info("TAVILY_API_KEY not set — web search disabled.")

    # Task 14.3 — Instantiate new pipeline components
    prompt_assembler = PromptAssembler(
        prompts_dir=PROMPTS_DIR,
        deployment_config=deployment_config,
        model_tier=model_tier,
    )
    logger.info("PromptAssembler instantiated (model_tier=%s)", model_tier)

    intent_classifier = IntentClassifier(
        llm_chain=llm_chain,
        model_tier=model_tier,
    )
    logger.info("IntentClassifier instantiated (model_tier=%s)", model_tier)

    router = Router(
        deployment_config=deployment_config,
        tavily_client=tavily_client,
    )
    logger.info(
        "Router instantiated (web_search_enabled=%s)",
        deployment_config.web_search_enabled,
    )

    post_processor = PostProcessor()
    logger.info("PostProcessor instantiated.")

    # TTS engines and router
    kokoro_tts = KokoroTTS()
    kokoro_ja_tts = KokoroJapaneseTTS()
    tts_router = TTSRouter(en_tts=kokoro_tts, ja_tts=kokoro_ja_tts)

    # 4. Pre-warm both Kokoro TTS engines concurrently at startup
    # Requirements: 3.1, 3.2, 3.3, 3.6
    logger.info("Pre-warming KokoroTTS and KokoroJapaneseTTS...")
    try:
        loop = asyncio.get_event_loop()
        await asyncio.gather(
            loop.run_in_executor(None, kokoro_tts.warm_up),
            loop.run_in_executor(None, kokoro_ja_tts.warm_up),
        )
        logger.info("Both Kokoro TTS engines pre-warmed successfully.")
    except Exception as exc:
        logger.warning("Kokoro TTS pre-warm failed (non-fatal): %s", exc)

    # 5. Test Groq API connectivity
    logger.info("Testing Groq API connectivity...")
    try:
        await groq_client.chat.completions.create(
            model=config.groq_llm_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        logger.info("Groq API connectivity: OK")
    except Exception as exc:
        logger.warning(
            "Groq API connectivity test failed (server will still start): %s", exc
        )

    # Store all components in app.state for access in endpoints
    app.state.config = config
    app.state.groq_client = groq_client
    app.state.stt_backend = stt_backend
    app.state.llm_chain = llm_chain
    app.state.tts_router = tts_router
    app.state.lang_detector = lang_detector
    app.state.deployment_config = deployment_config
    app.state.model_tier = model_tier
    app.state.prompt_assembler = prompt_assembler
    app.state.intent_classifier = intent_classifier
    app.state.router = router
    app.state.post_processor = post_processor
    app.state.tavily_client = tavily_client

    # 6. Signal readiness
    logger.info("✅ Server ready. Accepting connections.")
    pipeline_event("SERVER", "ready",
                   port=config.server_port,
                   stt_model=config.groq_stt_model,
                   llm_model=config.groq_llm_model)

    yield

    # Shutdown: cancel all active pipelines
    logger.info("Server shutting down — cancelling %d active pipeline(s).", len(active_pipelines))
    pipeline_event("SERVER", "shutdown", active_sessions=len(active_pipelines))
    for pipeline in list(active_pipelines.values()):
        pipeline.stop()
    active_pipelines.clear()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title="Lightweight Voice Demo", lifespan=lifespan)




# ---------------------------------------------------------------------------
# WebSocket endpoint: /ws  (AudioClient)
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def ws_audio_client(websocket: WebSocket):
    """WebSocket endpoint for the AudioClient.

    Accepts binary PCM16 audio frames and JSON control messages.
    Creates a fresh PipelineState per connection and runs the VoicePipeline.
    Cleans up on disconnect.

    Requirements: 1.1, 3.4, 9.5, 12.1
    """
    await websocket.accept()

    session_id = str(uuid.uuid4())
    logger.info("AudioClient connected — session_id=%s", session_id)
    pipeline_event("WS", "audio_client_connected", session=session_id[:8])

    # Create a fresh PipelineState for this connection (Requirement 3.4, 9.5)
    state = PipelineState(
        session_id=session_id,
        history=[],
        state="listening",
        interrupt=False,
        detected_language="en",
        audio_queue=asyncio.Queue(),
        transcript_queue=asyncio.Queue(),
        token_queue=asyncio.Queue(),
        audio_out_queue=asyncio.Queue(),
        deployment_config=app.state.deployment_config,
        model_tier=app.state.model_tier,
    )

    # Build the broadcast function bound to the current ui_clients set
    async def broadcast_fn(message: dict) -> None:
        await broadcast_to_ui(message)

    # Task 14.4 — Instantiate the pipeline with new components from app.state
    pipeline = VoicePipeline(
        audio_client_ws=websocket,
        state=state,
        stt_backend=app.state.stt_backend,
        llm_chain=app.state.llm_chain,
        tts_router=app.state.tts_router,
        lang_detector=app.state.lang_detector,
        intent_classifier=app.state.intent_classifier,
        prompt_assembler=app.state.prompt_assembler,
        router=app.state.router,
        post_processor=app.state.post_processor,
        broadcast_fn=broadcast_fn,
    )

    # Register the pipeline so /ws/ui can route text_input to it
    active_pipelines[session_id] = pipeline

    try:
        await pipeline.run()
    except WebSocketDisconnect:
        logger.info("AudioClient disconnected — session_id=%s", session_id)
    except Exception as exc:
        logger.error(
            "Unhandled exception in /ws handler (session=%s): %s",
            session_id,
            exc,
            exc_info=True,
        )
    finally:
        pipeline.stop()
        active_pipelines.pop(session_id, None)
        logger.info("AudioClient session cleaned up — session_id=%s", session_id)


# ---------------------------------------------------------------------------
# WebSocket endpoint: /ws/ui  (BrowserUI)
# ---------------------------------------------------------------------------


@app.websocket("/ws/ui")
async def ws_browser_ui(websocket: WebSocket):
    """WebSocket endpoint for BrowserUI clients.

    Sends JSON event messages only (never binary audio).
    Accepts JSON text_input messages and routes them into the active pipeline.

    Requirements: 6.5, 6.6, 6.7, 12.2, 12.3, 12.5
    """
    await websocket.accept()

    # Register this client for broadcast (Requirement 12.2)
    ui_clients.add(websocket)

    # Generate a UI session identifier
    ui_session_id = str(uuid.uuid4())
    logger.info("BrowserUI connected — ui_session_id=%s", ui_session_id)

    # Send session_start message (Requirement 12.5)
    try:
        await websocket.send_text(
            json.dumps({"type": "session_start", "session_id": ui_session_id})
        )
    except Exception as exc:
        logger.warning("Failed to send session_start to BrowserUI: %s", exc)

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as exc:
                logger.warning("BrowserUI receive error: %s", exc)
                break

            # Parse the incoming JSON message
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning("BrowserUI sent invalid JSON: %s", exc)
                continue

            msg_type = msg.get("type")

            if msg_type == "text_input":
                # Route text_input to the most recently active AudioClient pipeline
                # (Requirement 6.7)
                text = msg.get("text", "").strip()
                if not text:
                    continue

                logger.debug("BrowserUI text_input: %r", text)
                pipeline_event("WS", "text_input_received", text=text[:80])

                if active_pipelines:
                    # Get the most recently added pipeline (last key in insertion-ordered dict)
                    most_recent_session_id = next(reversed(active_pipelines))
                    target_pipeline = active_pipelines[most_recent_session_id]

                    # Inject as a TranscriptionResult into the pipeline's transcript_queue
                    transcript = TranscriptionResult(text=text, language="en", duration=0.0)
                    try:
                        # If TTS is playing or LLM is generating, treat this as a barge-in:
                        # use InterruptController so TTS synthesis tasks are properly cancelled
                        # and the audio output queue is flushed.
                        if target_pipeline._state.state in ("speaking", "thinking"):
                            pipeline_event("WS", "text_input_barge_in",
                                           session=most_recent_session_id[:8], text=text[:80])
                            target_pipeline._ic.request_interrupt(source="text_input")
                            target_pipeline._state.interrupt = True

                        await target_pipeline._state.transcript_queue.put(transcript)
                        # Also broadcast the transcript to all BrowserUI clients
                        await broadcast_to_ui(
                            {"type": "transcript", "text": text, "language": "en"}
                        )
                        logger.debug(
                            "text_input routed to pipeline session=%s",
                            most_recent_session_id,
                        )
                    except Exception as exc:
                        logger.warning("Failed to route text_input to pipeline: %s", exc)
                else:
                    # No active AudioClient pipeline — broadcast the transcript anyway
                    # so the BrowserUI can display it
                    logger.debug(
                        "text_input received but no active pipeline — broadcasting only"
                    )
                    await broadcast_to_ui(
                        {"type": "transcript", "text": text, "language": "en"}
                    )

            elif msg_type == "set_active":
                # UI button toggled — activate or deactivate Robo for the active pipeline
                active = bool(msg.get("active", False))
                pipeline_event("WS", "set_active_received", active=active)
                if active_pipelines:
                    most_recent_session_id = next(reversed(active_pipelines))
                    target_pipeline = active_pipelines[most_recent_session_id]
                    target_pipeline.set_robo_active(active)
                else:
                    logger.debug("set_active received but no active pipeline")

            else:
                logger.debug("BrowserUI sent unrecognised message type: %r", msg_type)

    except Exception as exc:
        logger.error("Unhandled exception in /ws/ui handler: %s", exc, exc_info=True)
    finally:
        ui_clients.discard(websocket)
        logger.info("BrowserUI disconnected — ui_session_id=%s", ui_session_id)

# Mount pre-built React UI static files at the HTTP root.
# Wrapped in try/except so the server starts even when ui/dist/ doesn't exist yet.
try:
    app.mount("/", StaticFiles(directory=UI_DIST_DIR, html=True), name="static")
except RuntimeError:
    logger.warning("ui/dist/ not found at %s — static file serving disabled", UI_DIST_DIR)