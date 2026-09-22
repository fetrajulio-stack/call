"""Construction du prompt système à partir de prompts/standard.md et des réglages .env."""

from __future__ import annotations

from agent.config import PROMPTS_DIR, Settings

PROMPT_FILE = PROMPTS_DIR / "standard.md"

# Message envoyé au LLM pour déclencher le décroché. Il est passé avec le rôle "user"
# (certains modèles, comme Qwen, refusent une conversation sans message utilisateur)
# et reconnu à son préfixe pour être exclu de la transcription.
KICKOFF_PREFIX = "[Début de l'appel]"
KICKOFF_MESSAGE = (
    f"{KICKOFF_PREFIX} L'appelant vient d'être mis en ligne. "
    "Décroche : salue-le, présente-toi et explique pourquoi tu réponds à la place de la personne appelée."
)


def build_system_prompt(settings: Settings) -> str:
    template = PROMPT_FILE.read_text(encoding="utf-8")
    business_line = f" chez {settings.business_name}" if settings.business_name else ""
    return template.format(
        assistant_name=settings.assistant_name,
        owner_name=settings.owner_name,
        business_line=business_line,
        unavailable_reason=settings.unavailable_reason,
    ).strip()
