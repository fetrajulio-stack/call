# Agent IA vocal qui répond aux appels

Assistante téléphonique en **français**, en **temps réel** : elle décroche, se présente,
prend un message (nom, motif, numéro de rappel confirmé), dit au revoir, raccroche et
enregistre un résumé de l'appel.

- **Cerveau** : LLM Groq (gratuit) — `openai/gpt-oss-120b` par défaut
- **Oreilles** : Groq Whisper (gratuit) — `whisper-large-v3-turbo`, français
- **Voix** : synthèse vocale locale et gratuite — Piper (`fr_FR-siwis-medium`, léger) ou Kokoro (`ff_siwis`, meilleure qualité)
- **Moteur temps réel** : [Pipecat](https://docs.pipecat.ai) (VAD Silero, interruptions, transports WebRTC et Twilio)

Tous les secrets et données personnelles sont dans `.env` (jamais dans le code).

## 1. Installation (Windows)

Prérequis : Python 3.12 et [uv](https://docs.astral.sh/uv/) (déjà installés sur cette machine
via `winget install Python.Python.3.12` et `winget install astral-sh.uv`).

```bash
uv sync
```

`uv sync` crée `.venv` et installe Pipecat + ses extras (~1 Go).

## 2. Configuration

Ouvre `.env` (créé à partir de `.env.example`) et renseigne au minimum :

| Variable | Rôle |
|---|---|
| `GROQ_API_KEY` | ta clé Groq (https://console.groq.com/keys) |
| `ASSISTANT_NAME` | prénom de l'assistante (« Léa ») |
| `OWNER_NAME` | la personne pour qui elle répond |
| `BUSINESS_NAME` | entreprise (optionnel) |
| `OWNER_UNAVAILABLE_REASON` | ce qu'elle dit à l'appelant (« est en réunion », …) |

Réglages utiles : `GROQ_LLM_MODEL`, `GROQ_REASONING_EFFORT` (modèles gpt-oss seulement),
`TTS_PROVIDER` (`kokoro` ou `piper`), `TTS_VOICE`, `TTS_SPEED`, `VAD_STOP_SECS` (silence
avant de considérer la phrase finie ; augmente-le si l'assistante coupe l'appelant quand
il dicte un numéro).

Le texte du prompt système est dans `prompts/standard.md` : modifie-le librement, en
gardant la règle du marqueur `[[FIN]]` (voir « Comment ça marche »).

## 3. Tests sans micro

```bash
uv run scripts/smoke_test.py
```

Vérifie en une commande : la clé Groq et le modèle LLM, la voix française (génère
`data/test_tts.wav`, écoute-le) et la transcription Whisper de ce fichier. `--verbose`
affiche les logs Pipecat.

```bash
uv run scripts/chat_test.py
```

Conversation **au clavier** avec l'assistante : même cerveau, même prompt, même fin d'appel
et même extraction du message, sans audio. Idéal pour ajuster `prompts/standard.md`.
En mode scripté, une réplique par ligne : `echo "Bonjour, je suis Paul" | uv run scripts/chat_test.py`.

Le premier lancement télécharge le modèle de voix dans `~/.cache/pipecat/` (Piper ≈ 60 Mo,
déjà téléchargé sur cette machine). Pour passer à la voix Kokoro, de meilleure qualité
(≈ 330 Mo, long sur une connexion lente), mets `TTS_PROVIDER=kokoro` et `TTS_VOICE=ff_siwis`
dans `.env`, ou pour un essai ponctuel :

```powershell
$env:TTS_PROVIDER="kokoro"; $env:TTS_VOICE="ff_siwis"; uv run scripts/smoke_test.py
```

(en Git Bash : `TTS_PROVIDER=kokoro TTS_VOICE=ff_siwis uv run scripts/smoke_test.py` ;
une variable définie dans le shell prime sur `.env`).

## 4. Parler avec l'agent depuis le PC (micro + haut-parleur)

```bash
uv run bot.py -t webrtc
```

Puis ouvre http://localhost:7860 dans ton navigateur, autorise le micro et clique sur
*Connect*. L'assistante décroche et se présente ; tu peux lui couper la parole. La page
permet aussi d'écrire au clavier (champ *Message*) si tu n'as pas de micro.

Chaque appel produit un fichier `data/calls/AAAAMMJJ-HHMMSS-<id>.json` avec le message
extrait (`message` : nom, téléphone, motif, urgence, complément) et la transcription
complète (`transcript`). Un résumé s'affiche aussi dans la console à la fin de l'appel.

## 5. Phase 2 : brancher un vrai numéro (Twilio)

Le même `bot.py` gère le téléphone ; seul le transport change.

1. Crée un compte Twilio (essai gratuit), récupère un numéro, puis copie `Account SID` et
   `Auth Token` dans `.env` (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`) — nécessaires pour
   que l'assistante puisse raccrocher.
2. Terminal 1 — expose le port 7860 sur Internet (cloudflared est déjà installé) :
   ```bash
   cloudflared tunnel --url http://localhost:7860
   ```
   Note l'URL affichée, du type `https://xxxx.trycloudflare.com`.
3. Console Twilio → *Phone Numbers* → ton numéro → *Voice Configuration* →
   « A call comes in » : **Webhook**, URL `https://xxxx.trycloudflare.com/`, méthode **HTTP POST**.
4. Terminal 2 — lance le bot en mode Twilio :
   ```bash
   uv run bot.py -t twilio --proxy xxxx.trycloudflare.com
   ```
5. Appelle ton numéro Twilio.

L'URL cloudflare change à chaque redémarrage du tunnel : pense à la remettre dans Twilio.

## Comment ça marche

```
micro / téléphone ─► Whisper (Groq) ─► LLM (Groq) ─► détecteur [[FIN]] ─► voix (Kokoro/Piper) ─► haut-parleur / téléphone
                                                                 │
                                        fin d'appel ─► extraction JSON du message (Groq) ─► data/calls/*.json
```

- **Fin d'appel** : le prompt demande à l'assistante de terminer sa phrase d'au revoir par
  `[[FIN]]`. `agent/end_of_call.py` repère ce marqueur dans le flux de texte, le retire
  avant la synthèse vocale et coupe la ligne une fois l'au revoir prononcé. Garde-fou : un
  `[[FIN]]` placé dans une réponse qui pose une question est ignoré.
- **Prise de message** : à la fin de l'appel (au revoir ou appelant qui raccroche),
  `agent/extraction.py` envoie la transcription au LLM en mode JSON pour en extraire nom,
  téléphone, motif, urgence et complément.
- Pourquoi pas de *function calling* ? Sur Groq, `gpt-oss` se dérègle dès que des outils
  sont déclarés (tours hallucinés, appels prématurés). Le marqueur + l'extraction marchent
  avec n'importe quel modèle.

## Limites à connaître

- **Modèles Groq** : la liste change (les Llama ont disparu en 2026). Ceux accessibles avec
  ta clé : https://console.groq.com/docs/models. Testés ici : `openai/gpt-oss-120b`
  (recommandé), `qwen/qwen3.8-27b` (bon mais *preview*).
- **Free tier Groq** : 8 000 tokens/minute pour le LLM et environ 20 requêtes/minute pour
  Whisper (limites exactes : https://console.groq.com/settings/limits). Chaque prise de
  parole coûte une requête Whisper + une requête LLM d'environ 700 à 1 200 tokens : un appel
  à la fois passe sans problème, mais un enchaînement très rapide de tours (ou plusieurs
  appels simultanés) fait ralentir ou refuser les réponses.
- **Latence** : environ 2 secondes entre la fin de ta phrase et le début de la réponse, avec
  Piper — `VAD_STOP_SECS` (0,7 s) + Whisper (~0,9 s) + LLM (~0,7 s) + voix (~0,3 s).
  Baisse `VAD_STOP_SECS` pour gagner du temps, au risque de couper l'appelant qui hésite.
- **Voix** : mesuré sur cette machine, pour une phrase d'accueil de 6,5 secondes —
  Piper commence à parler au bout de **0,3 s**, Kokoro au bout de **2,5 s** (elle synthétise
  la phrase entière avant d'émettre). Kokoro est plus naturelle, mais ces 2 secondes
  s'ajoutent à chaque réponse : au téléphone, Piper reste le meilleur compromis.
  Pour comparer toi-même : `uv run scripts/smoke_test.py` avec l'une puis l'autre.

## Structure

```
bot.py                 point d'entrée (runner Pipecat) : -t webrtc | -t twilio
agent/config.py        lecture de .env
agent/prompt.py        prompt système (prompts/standard.md + .env) et message de décroché
agent/services.py      fabriques STT / LLM / TTS / VAD
agent/end_of_call.py   détection du marqueur [[FIN]] et raccrochage
agent/extraction.py    extraction JSON du message à partir de la transcription
agent/storage.py       enregistrement JSON de chaque appel (data/calls/)
scripts/smoke_test.py  test des briques sans micro
scripts/chat_test.py   conversation au clavier avec l'assistante
```
