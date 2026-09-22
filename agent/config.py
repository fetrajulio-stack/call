"""Chargement de la configuration depuis le fichier .env.

Tous les secrets (clé Groq, identifiants Twilio) et les données personnelles
(noms, entreprise) vivent dans .env ; ce module est le seul endroit qui les lit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
CALLS_DIR = DATA_DIR / "calls"
PROMPTS_DIR = ROOT_DIR / "prompts"

# override=False : une variable déjà définie dans le shell prime sur .env (pratique pour un essai ponctuel,
# ex. `TTS_PROVIDER=piper uv run scripts/smoke_test.py`).
load_dotenv(ROOT_DIR / ".env", override=False)

TTS_PROVIDERS = ("kokoro", "piper")
DEFAULT_TTS_VOICE = {"kokoro": "ff_siwis", "piper": "fr_FR-siwis-medium"}


class ConfigError(RuntimeError):
    """Configuration manquante ou invalide dans .env."""


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError as exc:
        raise ConfigError(f"{name} doit être un nombre (valeur reçue : {raw!r})") from exc


@dataclass(frozen=True)
class Settings:
    # Clés / modèles Groq
    groq_api_key: str
    llm_model: str
    reasoning_effort: str
    stt_model: str
    # Voix
    tts_provider: str
    tts_voice: str
    tts_speed: float
    vad_stop_secs: float
    # Identité
    assistant_name: str
    owner_name: str
    business_name: str
    unavailable_reason: str
    # Twilio (phase 2)
    twilio_account_sid: str
    twilio_auth_token: str

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = _env("GROQ_API_KEY")
        if not api_key or api_key.startswith("gsk_xxx"):
            raise ConfigError(
                "GROQ_API_KEY manquante : ouvre le fichier .env et colle ta clé Groq "
                "(https://console.groq.com/keys)."
            )

        provider = _env("TTS_PROVIDER", "piper").lower()
        if provider not in TTS_PROVIDERS:
            raise ConfigError(
                f"TTS_PROVIDER={provider!r} inconnu ; valeurs possibles : {', '.join(TTS_PROVIDERS)}"
            )

        return cls(
            groq_api_key=api_key,
            llm_model=_env("GROQ_LLM_MODEL", "openai/gpt-oss-120b"),
            reasoning_effort=_env("GROQ_REASONING_EFFORT", "low"),
            stt_model=_env("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
            tts_provider=provider,
            tts_voice=_env("TTS_VOICE") or DEFAULT_TTS_VOICE[provider],
            tts_speed=_env_float("TTS_SPEED", 1.0),
            vad_stop_secs=_env_float("VAD_STOP_SECS", 0.7),
            assistant_name=_env("ASSISTANT_NAME", "Léa"),
            owner_name=_env("OWNER_NAME", "le propriétaire de ce numéro"),
            business_name=_env("BUSINESS_NAME"),
            unavailable_reason=_env("OWNER_UNAVAILABLE_REASON", "n'est pas disponible pour le moment"),
            twilio_account_sid=_env("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_env("TWILIO_AUTH_TOKEN"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Charge la configuration une seule fois par processus."""
    return Settings.from_env()
