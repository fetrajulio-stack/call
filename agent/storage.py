"""Enregistrement des appels : message structuré + transcription, un fichier JSON par appel."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from agent.config import CALLS_DIR
from agent.prompt import KICKOFF_PREFIX

TRANSCRIPT_ROLES = {"user", "assistant"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _message_text(content: Any) -> str:
    """Extrait le texte d'un message LLM, que le contenu soit une chaîne ou une liste de blocs."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(p for p in parts if p)
    return ""


@dataclass
class CallRecord:
    id: str
    started_at: str
    transport: str
    caller_number: str | None = None
    ended_at: str | None = None
    message: dict[str, str] | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def start(cls, transport: str, caller_number: str | None = None) -> "CallRecord":
        record = cls(
            id=uuid.uuid4().hex[:8],
            started_at=_now(),
            transport=transport,
            caller_number=caller_number,
        )
        logger.info(f"Nouvel appel {record.id} via {transport} (appelant : {caller_number or 'inconnu'})")
        record.save()  # trace immédiate, complétée à la fin de l'appel
        return record

    @property
    def path(self) -> Path:
        stamp = datetime.fromisoformat(self.started_at).strftime("%Y%m%d-%H%M%S")
        return CALLS_DIR / f"{stamp}-{self.id}.json"

    @staticmethod
    def build_transcript(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Transcription lisible : tours user/assistant seulement, sans le message de décroché."""
        return [
            {"role": m["role"], "content": text}
            for m in messages
            if m.get("role") in TRANSCRIPT_ROLES
            and (text := _message_text(m.get("content")))
            and not text.startswith(KICKOFF_PREFIX)
        ]

    def finish(self, transcript: list[dict[str, str]], message: dict[str, Any] | None = None) -> Path:
        """Clôture l'appel avec sa transcription et, s'il y en a un, le message extrait."""
        self.ended_at = _now()
        self.transcript = transcript
        if message:
            self.message = {k: v for k, v in message.items() if v}
            self.message["enregistre_a"] = self.ended_at
            logger.info(f"Message extrait pour l'appel {self.id} : {self.message}")
        return self.save()

    def save(self) -> Path:
        CALLS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    def summary_text(self) -> str:
        lines = [f"Appel {self.id} ({self.transport}) : {self.started_at} -> {self.ended_at or 'en cours'}"]
        if self.caller_number:
            lines.append(f"  Appelant   : {self.caller_number}")
        if self.message:
            for key in ("nom_appelant", "telephone", "motif", "urgence", "message"):
                if self.message.get(key):
                    lines.append(f"  {key:<11}: {self.message[key]}")
        else:
            lines.append("  Aucun message enregistré.")
        lines.append(f"  Fichier    : {self.path}")
        return "\n".join(lines)
