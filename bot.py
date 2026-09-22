"""Agent IA vocal qui répond aux appels — point d'entrée.

Lancement :
    uv run bot.py -t webrtc                        # test au micro : http://localhost:7860
    uv run bot.py -t twilio --proxy <hote.public>  # vrai numéro de téléphone (phase 2)

Le pipeline est identique dans les deux cas ; seul le transport change.
"""

from __future__ import annotations

import asyncio
import os
import sys

from loguru import logger

# Accents corrects dans la console Windows (PowerShell/cmd), quel que soit le codepage.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from agent.config import ConfigError, get_settings
from agent.end_of_call import build_end_of_call_processor
from agent.extraction import extract_message
from agent.prompt import KICKOFF_MESSAGE, build_system_prompt
from agent.services import make_llm, make_stt, make_tts, make_vad
from agent.storage import CallRecord

try:
    SETTINGS = get_settings()
except ConfigError as exc:
    raise SystemExit(f"Configuration invalide : {exc}") from exc

if SETTINGS.owner_name in ("", "Prénom Nom"):
    logger.warning("OWNER_NAME n'est pas renseigné dans .env : l'assistante dira littéralement « Prénom Nom ».")

# Le runner Pipecat lit ces variables pour Twilio (raccrochage automatique).
os.environ.setdefault("TWILIO_ACCOUNT_SID", SETTINGS.twilio_account_sid)
os.environ.setdefault("TWILIO_AUTH_TOKEN", SETTINGS.twilio_auth_token)

logger.info("Chargement de Pipecat (quelques secondes)...")

from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments, WebSocketRunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

# Paramètres par transport ; le runner choisit selon l'option -t.
# Pour Twilio, le sérialiseur (µ-law 8 kHz) est ajouté automatiquement par le runner.
transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    "twilio": lambda: FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True),
}

SYSTEM_PROMPT = build_system_prompt(SETTINGS)


def warm_up_tts() -> None:
    """Télécharge et charge le modèle de voix avant d'accepter des appels.

    Les services TTS locaux font ce travail dans leur constructeur : le faire ici évite
    qu'un premier appel n'attende (et ne tombe en timeout) pendant un téléchargement.
    """
    logger.info(f"Préparation de la voix {SETTINGS.tts_provider}/{SETTINGS.tts_voice} (téléchargement au premier lancement)...")
    make_tts(SETTINGS)
    logger.info("Voix prête.")


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    is_phone = isinstance(runner_args, WebSocketRunnerArguments)
    record = CallRecord.start(
        transport=(getattr(runner_args, "transport_type", None) or "twilio") if is_phone else "webrtc",
        caller_number=_caller_number(runner_args) if is_phone else None,
    )

    stt = make_stt(SETTINGS)
    llm = make_llm(SETTINGS, SYSTEM_PROMPT)
    # Le chargement du modèle de voix est bloquant : on le sort de la boucle asyncio.
    tts = await asyncio.to_thread(make_tts, SETTINGS)

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=make_vad(SETTINGS)),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            build_end_of_call_processor(),  # détecte [[FIN]] et raccroche après l'au revoir
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    params = {"enable_metrics": True, "enable_usage_metrics": True}
    if is_phone:
        # Twilio Media Streams : audio µ-law mono 8 kHz dans les deux sens.
        params.update(audio_in_sample_rate=8000, audio_out_sample_rate=8000)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(**params),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Appel {record.id} : appelant en ligne, l'assistante décroche")
        context.add_message({"role": "user", "content": KICKOFF_MESSAGE})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Appel {record.id} : appelant déconnecté")
        await runner.cancel()

    try:
        await runner.run()
    finally:
        transcript = CallRecord.build_transcript(context.get_messages())
        message = await extract_message(transcript, SETTINGS)
        record.finish(transcript, message)
        logger.info("Résumé de l'appel :\n" + record.summary_text())


def _caller_number(runner_args: WebSocketRunnerArguments) -> str | None:
    """Numéro de l'appelant transmis par Twilio (`runner_args.call_data`, rempli par create_transport)."""
    call_data = getattr(runner_args, "call_data", None)
    if call_data is None:
        return None
    number = getattr(call_data, "from_number", None) or call_data.get("from")
    return str(number) if number else None


async def bot(runner_args: RunnerArguments) -> None:
    """Point d'entrée appelé par le runner Pipecat à chaque connexion."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    warm_up_tts()
    main()
