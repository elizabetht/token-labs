"""
Magpie TTS API Server

Wraps nvidia/magpie_tts_multilingual_357m (NeMo) in an OpenAI-compatible
/v1/audio/speech endpoint. Runs on GPU (spark-01, shared with Llama 3.1 8B).

Speakers: John (0), Sofia (1), Aria (2), Jason (3), Leo (4)
Languages: en, es, de, fr, vi, it, zh
"""

import io
import logging
from contextlib import asynccontextmanager
from typing import Optional

import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

logger = logging.getLogger("magpie-tts")

# Global model reference
tts_model = None

SPEAKER_MAP = {
    "john": 0,
    "sofia": 1,
    "aria": 2,
    "jason": 3,
    "leo": 4,
}

SUPPORTED_LANGUAGES = {"en", "es", "de", "fr", "vi", "it", "zh"}

SAMPLE_RATE = 22050


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the TTS model on startup."""
    global tts_model
    logger.info("Loading MagpieTTS model...")
    from nemo.collections.tts.models import MagpieTTSModel

    tts_model = MagpieTTSModel.from_pretrained("nvidia/magpie_tts_multilingual_357m")
    if torch.cuda.is_available():
        tts_model = tts_model.cuda()
        logger.info("MagpieTTS model moved to GPU")
    tts_model.eval()
    logger.info("MagpieTTS model loaded successfully")
    yield
    tts_model = None


app = FastAPI(title="Magpie TTS Server", lifespan=lifespan)


class SpeechRequest(BaseModel):
    """OpenAI-compatible /v1/audio/speech request."""

    model: str = "nvidia/magpie_tts_multilingual_357m"
    input: str = Field(..., description="Text to synthesize")
    voice: str = Field(default="sofia", description="Speaker: john, sofia, aria, jason, leo")
    language: str = Field(default="en", description="Language: en, es, de, fr, vi, it, zh")
    response_format: str = Field(default="wav", description="Output format (only wav supported)")
    speed: Optional[float] = Field(default=1.0, description="Speed (not used, for API compat)")


@app.post("/v1/audio/speech")
async def create_speech(request: SpeechRequest):
    """Generate speech from text using MagpieTTS."""
    if tts_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    # Validate voice
    voice_lower = request.voice.lower()
    if voice_lower not in SPEAKER_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown voice '{request.voice}'. Available: {list(SPEAKER_MAP.keys())}",
        )

    # Validate language
    if request.language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown language '{request.language}'. Available: {sorted(SUPPORTED_LANGUAGES)}",
        )

    if not request.input.strip():
        raise HTTPException(status_code=400, detail="Input text is empty")

    try:
        with torch.no_grad():
            audio, audio_len = tts_model.do_tts(
                request.input,
                language=request.language,
                apply_TN=(request.language != "vi"),  # TN not supported for Vietnamese
                speaker_index=SPEAKER_MAP[voice_lower],
            )

        # Convert to WAV bytes
        audio_np = audio.cpu().numpy()
        buf = io.BytesIO()
        sf.write(buf, audio_np, SAMPLE_RATE, format="WAV")
        buf.seek(0)

        return Response(
            content=buf.read(),
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="speech.wav"'},
        )

    except Exception as e:
        logger.exception("TTS generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/models")
async def list_models():
    """List available TTS models."""
    return {
        "object": "list",
        "data": [
            {
                "id": "nvidia/magpie_tts_multilingual_357m",
                "object": "model",
                "owned_by": "nvidia",
            }
        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": tts_model is not None}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000)
