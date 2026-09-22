"""Extraction du message laissé par l'appelant, à partir de la transcription (mode JSON)."""

from __future__ import annotations

import json
from typing import Any

from groq import AsyncGroq
from loguru import logger

from agent.config import Settings

FIELDS = ("nom_appelant", "telephone", "motif", "urgence", "message")

EXTRACTION_PROMPT = """Tu lis la transcription d'un appel téléphonique entre une assistante vocale et un appelant.
Extrais le message laissé par l'appelant et réponds uniquement avec un objet JSON ayant exactement ces clés :
- "nom_appelant" : nom de l'appelant, ou null
- "telephone" : numéro de rappel tel que confirmé en dernier par l'appelant, chiffres groupés par deux séparés par des espaces, ou null
- "motif" : raison de l'appel en une phrase, ou null
- "urgence" : "urgente" si l'appelant a indiqué une urgence, sinon "normale"
- "message" : message complémentaire ou information utile à transmettre (par exemple "rappellera demain matin"), ou null
N'invente rien : si une information n'a pas été donnée par l'appelant, mets null."""


async def extract_message(transcript: list[dict[str, str]], settings: Settings) -> dict[str, Any] | None:
    """Retourne les champs du message (clés de FIELDS), ou None si l'appelant n'a rien dit."""
    if not any(m["role"] == "user" for m in transcript):
        return None

    dialogue = "\n".join(f"{'Appelant' if m['role'] == 'user' else 'Assistante'} : {m['content']}" for m in transcript)
    client = AsyncGroq(api_key=settings.groq_api_key)
    try:
        completion = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": f"Transcription :\n{dialogue}\n\nRéponds en JSON."},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=500,
        )
        raw = completion.choices[0].message.content or "{}"
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — l'extraction ne doit jamais faire perdre la transcription
        logger.error(f"Extraction du message impossible : {type(exc).__name__}: {exc}")
        return None

    message = {key: data.get(key) for key in FIELDS}
    message["urgence"] = "urgente" if str(message.get("urgence") or "").lower().startswith("urg") else "normale"
    if not any(message[k] for k in ("nom_appelant", "telephone", "motif", "message")):
        return None
    return message
