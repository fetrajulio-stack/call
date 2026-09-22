"""Fabriques des services Pipecat (STT, LLM, TTS, VAD) configurés depuis .env."""

from __future__ import annotations

import zipfile
from pathlib import Path

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.groq.stt import GroqSTTService
from pipecat.transcriptions.language import Language

from agent.config import Settings


def make_stt(settings: Settings) -> GroqSTTService:
    """Transcription Groq Whisper, forcée en français.

    Le `prompt` oriente l'orthographe des noms propres que Whisper entend souvent.
    """
    hints = [settings.assistant_name, settings.owner_name, settings.business_name]
    return GroqSTTService(
        api_key=settings.groq_api_key,
        settings=GroqSTTService.Settings(
            model=settings.stt_model,
            language=Language.FR,
            prompt="Conversation téléphonique en français. " + ", ".join(h for h in hints if h) + ".",
        ),
    )


def make_llm(settings: Settings, system_prompt: str) -> GroqLLMService:
    options = {}
    if settings.reasoning_effort and settings.llm_model.startswith("openai/gpt-oss"):
        # Modèles "raisonnants" (gpt-oss) : low = réponses rapides, adaptées à la voix.
        # Les autres modèles n'acceptent pas ce paramètre.
        options["reasoning_effort"] = settings.reasoning_effort
    return GroqLLMService(
        api_key=settings.groq_api_key,
        settings=GroqLLMService.Settings(
            model=settings.llm_model,
            temperature=0.3,
            system_instruction=system_prompt,
            **options,
        ),
    )


def make_tts(settings: Settings):
    """Synthèse vocale française locale : Kokoro (défaut) ou Piper, selon TTS_PROVIDER.

    Le modèle est téléchargé puis chargé dans le constructeur du service (bloquant).
    Un téléchargement interrompu laisse un fichier tronqué que le service croit valide :
    dans ce cas on efface le cache et on retélécharge une fois.
    """
    try:
        return _build_tts(settings)
    except Exception as exc:  # noqa: BLE001 — filtré juste en dessous
        if not _looks_like_corrupt_model(exc):
            raise
        removed = _purge_tts_cache(settings)
        if not removed:
            raise
        logger.warning(f"Modèle de voix illisible ({exc}) : cache supprimé, nouveau téléchargement...")
        return _build_tts(settings)


def _looks_like_corrupt_model(exc: BaseException) -> bool:
    return type(exc).__module__.startswith("onnxruntime") or isinstance(exc, (zipfile.BadZipFile, EOFError))


def _purge_tts_cache(settings: Settings) -> list[Path]:
    if settings.tts_provider == "kokoro":
        from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR

        candidates = [KOKORO_CACHE_DIR / "kokoro-v1.0.onnx", KOKORO_CACHE_DIR / "voices-v1.0.bin"]
    else:
        from pipecat.services.piper.tts import PIPER_CACHE_DIR

        candidates = [PIPER_CACHE_DIR / f"{settings.tts_voice}.onnx", PIPER_CACHE_DIR / f"{settings.tts_voice}.onnx.json"]
    removed = [p for p in candidates if p.exists()]
    for path in removed:
        path.unlink()
    return removed


def _build_tts(settings: Settings):
    if settings.tts_provider == "kokoro":
        from pipecat.services.kokoro.tts import KokoroTTSService

        return KokoroTTSService(
            settings=KokoroTTSService.Settings(
                voice=settings.tts_voice,
                language=Language.FR,
                speed=settings.tts_speed,
            ),
        )

    from pipecat.services.piper.tts import PiperTTSService

    return PiperTTSService(
        settings=PiperTTSService.Settings(
            voice=settings.tts_voice,
            language=Language.FR,
        ),
    )


def make_vad(settings: Settings) -> SileroVADAnalyzer:
    """Détection de voix : `stop_secs` = silence avant de considérer la phrase terminée."""
    return SileroVADAnalyzer(params=VADParams(stop_secs=settings.vad_stop_secs))
