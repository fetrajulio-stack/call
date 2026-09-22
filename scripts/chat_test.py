"""Conversation au clavier avec l'assistante — même cerveau, même fin d'appel, sans audio.

    uv run scripts/chat_test.py            # interactif : tape tes répliques, Ctrl+C pour quitter
    echo "..." | uv run scripts/chat_test.py   # scripté : une réplique par ligne

Sert à vérifier la prise de message (fichier JSON dans data/calls/) et le raccrochage ([[FIN]]),
ou à ajuster prompts/standard.md sans parler dans un micro.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from loguru import logger

from agent.config import ConfigError, get_settings
from agent.end_of_call import build_end_of_call_processor
from agent.extraction import extract_message
from agent.prompt import KICKOFF_MESSAGE, build_system_prompt
from agent.services import make_llm
from agent.storage import CallRecord


async def chat() -> None:
    from pipecat.frames.frames import (
        AggregatedTextFrame,
        EndFrame,
        LLMFullResponseEndFrame,
        LLMMessagesAppendFrame,
        LLMRunFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker, ProcessorUnusablePolicy
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.workers.runner import WorkerRunner

    settings = get_settings()
    record = CallRecord.start(transport="chat")
    turn_done = asyncio.Event()
    ended = asyncio.Event()
    last_activity = 0.0

    class Printer(FrameProcessor):
        """Affiche ce que l'assistante « dit » : le texte tel qu'il partirait vers la voix."""

        async def process_frame(self, frame, direction):
            nonlocal last_activity
            await super().process_frame(frame, direction)
            last_activity = asyncio.get_running_loop().time()
            if isinstance(frame, AggregatedTextFrame):
                print(frame.text, end=" ", flush=True)
            elif isinstance(frame, LLMFullResponseEndFrame):
                print(flush=True)
                turn_done.set()
            elif isinstance(frame, EndFrame):
                ended.set()
                turn_done.set()
            await self.push_frame(frame, direction)

    llm = make_llm(settings, build_system_prompt(settings))
    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    async def on_end() -> None:
        print("[[FIN]] -> l'assistante raccroche", flush=True)

    worker = PipelineWorker(
        Pipeline([user_aggregator, llm, build_end_of_call_processor(on_end), Printer(), assistant_aggregator]),
        idle_timeout_secs=None,
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())

    async def wait_turn() -> None:
        """Attend la fin de la réponse, puis un court silence (le LLM peut enchaîner un second tour)."""
        await turn_done.wait()
        while not ended.is_set() and asyncio.get_running_loop().time() - last_activity < 0.8:
            await asyncio.sleep(0.2)

    context.add_message({"role": "user", "content": KICKOFF_MESSAGE})
    print("Assistante : ", end="", flush=True)
    await worker.queue_frame(LLMRunFrame())
    await wait_turn()

    interactive = sys.stdin.isatty()
    try:
        while not ended.is_set():
            turn_done.clear()
            line = await asyncio.to_thread(input, "Vous : " if interactive else "")
            line = line.strip()
            if not line:
                continue
            if not interactive:
                print(f"Vous : {line}", flush=True)
            print("Assistante : ", end="", flush=True)
            await worker.queue_frame(LLMMessagesAppendFrame([{"role": "user", "content": line}], run_llm=True))
            await wait_turn()
    except (EOFError, KeyboardInterrupt):
        print("\n(fin de la conversation)")
    finally:
        if not ended.is_set():
            await runner.cancel()
        await run_task
        transcript = CallRecord.build_transcript(context.get_messages())
        print("\nExtraction du message...", flush=True)
        record.finish(transcript, await extract_message(transcript, settings))
        print(record.summary_text())


def main() -> int:
    try:
        get_settings()
    except ConfigError as exc:
        print(f"Configuration invalide : {exc}")
        return 1
    logger.remove()
    if "--verbose" in sys.argv:
        logger.add(sys.stderr, level="DEBUG")
    asyncio.run(chat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
