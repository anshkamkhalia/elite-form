import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """You are an experienced tennis coach reviewing a player's \
stroke against a professional's reference clip. You are given biomechanical \
measurements: wrist velocity (mph), average joint angles in degrees for six \
joints (differences are player minus pro), and for serves, toss drift in feet.

Respond with ONLY a JSON object (no markdown fences) in this exact shape:
{"tldr": "...", "tips": "..."}

- "tldr": one short sentence (max ~20 words) that a busy player can skim — \
the single most important takeaway.
- "tips": the full coaching notes as plain text. Conversational, encouraging, \
and specific — sound like a real coach talking to their player, not a report. \
Lead with the one or two differences that matter most (largest deviations). \
For each, explain what it means for their shot and give one concrete cue or \
drill. Mention a strength too. Do not recite raw numbers except where a \
number genuinely helps. 3 short paragraphs maximum, separated by blank lines. \
No headings, no bullet lists, no markdown inside the tips string."""


def _summarize_metrics(results: dict, shot_type: str, comparison_pro: str) -> str:

    lines = [f"Shot type: {shot_type}. Compared against: {comparison_pro}."]

    velocity = results.get("velocity") or {}
    if velocity:
        lines.append(
            "Wrist velocity (mph): "
            f"player avg {velocity.get('player', {}).get('average')}, "
            f"pro avg {velocity.get('pro', {}).get('average')}, "
            f"avg diff {velocity.get('average_difference')}; "
            f"player peak {velocity.get('player', {}).get('peak')}, "
            f"pro peak {velocity.get('pro', {}).get('peak')}, "
            f"peak diff {velocity.get('peak_difference')}."
        )

    angles = results.get("joint_angles") or {}
    for joint, a in angles.items():
        lines.append(
            f"{joint.replace('_', ' ')}: player {a.get('player_average')}°, "
            f"pro {a.get('pro_average')}°, diff {a.get('difference')}°."
        )

    toss = results.get("toss")
    if toss:
        lines.append(
            f"Serve toss drift (ft): player {toss.get('player', {}).get('toss_drift_ft')}, "
            f"pro {toss.get('pro', {}).get('toss_drift_ft')}, "
            f"diff {toss.get('drift_difference_ft')}."
        )

    return "\n".join(lines)


def _parse_coaching_response(raw: str) -> dict:
    """Parse Gemini's JSON reply into ``{tldr, tips}``. Falls back to treating
    the whole reply as tips if the model didn't return valid JSON."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        text = text.strip()

    try:
        parsed = json.loads(text)
        tldr = (parsed.get("tldr") or "").strip()
        tips = (parsed.get("tips") or "").strip()
        if tips:
            return {"tldr": tldr, "tips": tips}
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    return {"tldr": "", "tips": raw.strip()}


def generate_coaching_tips(results: dict, shot_type: str, comparison_pro: str) -> dict:
    """Return ``{"tldr": str, "tips": str}`` coaching feedback from Gemini."""

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_URL = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.1-flash-lite:generateContent"
    )

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set in the repo-root .env")

    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": _summarize_metrics(results, shot_type, comparison_pro)}
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 600,
            "responseMimeType": "application/json",
        },
    }

    req = urllib.request.Request(
        GEMINI_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError(
                "The AI service is temporarily rate-limited — wait a minute and retry."
            )
        print("STATUS:", e.code)
        print(e.read().decode("utf-8"))
        raise

    raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    return _parse_coaching_response(raw)
