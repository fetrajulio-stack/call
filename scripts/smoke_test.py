"""Test des briques de l'agent sans micro ni navigateur.

    uv run scripts/smoke_test.py

1. LLM Groq : une réponse courte (valide la clé et le modèle).
2. Voix française (Kokoro ou Piper selon .env) : génère data/test_tts.wav.
3. Groq Whisper : transcrit ce fichier en français.
"""

from __future__ import annotations

import asyncio
import sys
import time
import wave
from pathlib import Path

# Permet `uv run scripts/smoke_test.py` depuis la racine du projet.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Accents corrects dans la console Windows, quel que soit le codepage.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from loguru import logger

from agent.config import DATA_DIR, ConfigError, get_settings

TEST_SENTENCE = "Bonjour, vous êtes bien chez Léa. Je peux prendre un message pour vous rappeler au 06 12 34 56 78."
OUTPUT_WAV = DATA_DIR / "test_tts.wav"


def step(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def test_llm(settings) -> None:
    from groq import Groq

    step(f"1/3 LLM Groq ({settings.llm_model})")
    client = Groq(api_key=settings.groq_api_key)
    options = {"reasoning_effort": settings.reasoning_effort} if settings.reasoning_effort else {}
    t0 = time.perf_counter()
    completion = client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role": "user", "content": "Réponds uniquement par le mot OK, en français."}],
        max_tokens=300,  # les modèles raisonnants (gpt-oss) consomment des tokens avant de répondre
        temperature=0,
        **options,
    )
    answer = (completion.choices[0].message.content or "").strip()
    print(f"Réponse : {answer!r}  ({time.perf_counter() - t0:.2f} s)", flush=True)
    if "ok" not in answer.lower():
        raise RuntimeError(f"Réponse inattendue du LLM : {answer!r}")


async def test_tts(settings) -> None:
    from pipecat.frames.frames import EndFrame, TTSAudioRawFrame, TTSSpeakFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.workers.runner import WorkerRunner

    from agent.services import make_tts

    step(f"2/3 Voix française ({settings.tts_provider} / {settings.tts_voice})")

    class AudioCollector(FrameProcessor):
        """Récupère l'audio produit par le service TTS."""

        def __init__(self):
            super().__init__()
            self.chunks: list[bytes] = []
            self.sample_rate = 0

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSAudioRawFrame):
                self.chunks.append(frame.audio)
                self.sample_rate = frame.sample_rate
            await self.push_frame(frame, direction)

    collector = AudioCollector()
    tts = await asyncio.to_thread(make_tts, settings)  # téléchargement/chargement du modèle hors de la boucle asyncio
    worker = PipelineWorker(Pipeline([tts, collector]))
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await worker.queue_frames([TTSSpeakFrame(TEST_SENTENCE), EndFrame()])

    t0 = time.perf_counter()
    await runner.run()
    elapsed = time.perf_counter() - t0

    audio = b"".join(collector.chunks)
    if not audio:
        raise RuntimeError("Le service TTS n'a produit aucun audio.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUTPUT_WAV), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(collector.sample_rate)
        wav.writeframes(audio)
    duration = len(audio) / 2 / collector.sample_rate
    print(f"Fichier : {OUTPUT_WAV}  ({duration:.1f} s d'audio à {collector.sample_rate} Hz, synthèse en {elapsed:.2f} s)")
    print("Écoute-le pour vérifier la voix.", flush=True)


def test_stt(settings) -> None:
    from groq import Groq

    step(f"3/3 Transcription Groq Whisper ({settings.stt_model})")
    client = Groq(api_key=settings.groq_api_key)
    t0 = time.perf_counter()
    with OUTPUT_WAV.open("rb") as f:
        result = client.audio.transcriptions.create(
            file=(OUTPUT_WAV.name, f.read()),
            model=settings.stt_model,
            language="fr",
            response_format="text",
        )
    text = result if isinstance(result, str) else getattr(result, "text", str(result))
    print(f"Phrase envoyée : {TEST_SENTENCE}")
    print(f"Texte reconnu  : {text.strip()}  ({time.perf_counter() - t0:.2f} s)")


def main() -> int:
    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"Configuration invalide : {exc}")
        return 1

    logger.remove()  # garde la sortie lisible ; les logs Pipecat restent disponibles avec --verbose
    if "--verbose" in sys.argv:
        logger.add(sys.stderr, level="DEBUG")

    try:
        test_llm(settings)
        asyncio.run(test_tts(settings))
        test_stt(settings)
    except Exception as exc:  # noqa: BLE001 — on veut un message clair, pas une trace
        print(f"\nÉCHEC : {type(exc).__name__}: {exc}")
        if "--verbose" not in sys.argv:
            print("Relance avec --verbose pour le détail.")
        return 1

    print("\nTout fonctionne : lance `uv run bot.py -t webrtc` puis ouvre http://localhost:7860")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
