"""Détection de la fin d'appel décidée par l'assistante.

Le LLM termine sa phrase d'au revoir par le marqueur ``[[FIN]]``. Ce processeur l'intercepte
dans le flux de texte (avant la synthèse vocale), le supprime pour qu'il ne soit pas
prononcé, puis demande l'arrêt du pipeline : l'au revoir déjà en file d'attente est
prononcé, ensuite la ligne est coupée.

Ce mécanisme remplace le function calling, que certains modèles Groq gèrent mal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import EndTaskFrame, Frame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.processors.aggregators.llm_text_processor import LLMTextProcessor
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.text.pattern_pair_aggregator import MatchAction, PatternMatch, PatternPairAggregator

END_MARKER = "[[FIN]]"
_START, _END = "[[", "]]"


class EndOfCallProcessor(LLMTextProcessor):
    """À placer entre le LLM et la TTS. Découpe le texte en phrases et surveille ``[[FIN]]``."""

    def __init__(self, on_end: Callable[[], Awaitable[None]] | None = None, **kwargs):
        aggregator = PatternPairAggregator()
        aggregator.add_pattern(type="fin", start_pattern=_START, end_pattern=_END, action=MatchAction.REMOVE)
        super().__init__(text_aggregator=aggregator, **kwargs)
        aggregator.on_pattern_match("fin", self._on_marker)
        self._on_end = on_end
        self._response_text = ""  # texte de la réponse en cours, pour vérifier le contexte du marqueur

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, LLMFullResponseStartFrame):
            self._response_text = ""
        elif isinstance(frame, LLMTextFrame):
            self._response_text += frame.text
        await super().process_frame(frame, direction)

    async def _on_marker(self, match: PatternMatch) -> None:
        if match.text.strip().upper() != "FIN":
            logger.warning(f"Marqueur inconnu ignoré : [[{match.text}]]")
            return
        # Garde-fou : on ne raccroche jamais sur une réponse qui pose une question à l'appelant.
        if "?" in self._response_text.split(_START, 1)[0]:
            logger.warning("[[FIN]] ignoré : la réponse pose une question, l'appel continue")
            return
        logger.info("Marqueur de fin d'appel reçu : l'assistante raccroche après son au revoir")
        if self._on_end:
            await self._on_end()
        # Remonte jusqu'à la source du pipeline, qui renvoie un EndFrame vers l'aval :
        # le texte déjà envoyé à la voix est prononcé avant la coupure.
        await self.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)


def build_end_of_call_processor(on_end: Callable[[], Awaitable[None]] | None = None) -> EndOfCallProcessor:
    return EndOfCallProcessor(on_end=on_end)
