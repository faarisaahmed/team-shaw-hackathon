"""Generate narration with ElevenLabs, replacing the macOS TTS track.

Reads ELEVENLABS_API_KEY from the environment (loaded from the repo's .env).
The key is never printed, logged, or written anywhere.

    python tts_elevenlabs.py --list              # show voices and models
    python tts_elevenlabs.py --voice <id|name>   # generate all nine scenes
    python tts_elevenlabs.py --voice <id> --only 5

Output lands in audio/sN.wav at 48 kHz stereo, exactly where build_video.py
expects it, so `python build_video.py` afterwards re-times the whole video
around the new durations.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
load_dotenv(ROOT / ".env")

API = "https://api.elevenlabs.io/v1"
# Quality-first default; --model can override once we see what the account has.
DEFAULT_MODEL = "eleven_multilingual_v2"


def key() -> str:
    k = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not k:
        sys.exit(
            "ELEVENLABS_API_KEY is not set.\n"
            "Add it to .env (which is gitignored):  ELEVENLABS_API_KEY=\"sk_...\""
        )
    return k


def headers() -> dict:
    return {"xi-api-key": key(), "Content-Type": "application/json"}


def masked() -> str:
    k = key()
    return f"{k[:6]}{'•' * 8}{k[-2:]}" if len(k) > 10 else "•" * 8


def list_assets() -> None:
    with httpx.Client(timeout=60) as c:
        v = c.get(f"{API}/voices", headers=headers())
        v.raise_for_status()
        print(f"key {masked()} — voices:\n")
        for voice in v.json().get("voices", []):
            labels = voice.get("labels") or {}
            desc = ", ".join(
                f"{k}={val}" for k, val in labels.items()
                if k in ("accent", "gender", "age", "use_case", "description")
            )
            print(f"  {voice['voice_id']}  {voice['name']:<22} {desc}")

        m = c.get(f"{API}/models", headers=headers())
        if m.status_code == 200:
            print("\nmodels:\n")
            for model in m.json():
                if model.get("can_do_text_to_speech"):
                    print(f"  {model['model_id']:<32} {model.get('name','')}")


def resolve_voice(name_or_id: str) -> str:
    """Accept either a voice_id or a human name."""
    with httpx.Client(timeout=60) as c:
        r = c.get(f"{API}/voices", headers=headers())
        r.raise_for_status()
        voices = r.json().get("voices", [])
    for v in voices:
        if v["voice_id"] == name_or_id:
            return v["voice_id"]
    q = name_or_id.lower().strip()
    # ElevenLabs names carry a description ("Daniel - Steady Broadcaster"),
    # so match on the leading name too rather than requiring the full string.
    for v in voices:
        if v["name"].lower() == q or v["name"].lower().split(" - ")[0] == q:
            return v["voice_id"]
    partial = [v for v in voices if q in v["name"].lower()]
    if len(partial) == 1:
        return partial[0]["voice_id"]
    if len(partial) > 1:
        names = ", ".join(v["name"] for v in partial)
        sys.exit(f"{name_or_id!r} is ambiguous: {names}")
    sys.exit(
        f"No voice matching {name_or_id!r}. Run --list to see what the account has."
    )


def scenes() -> dict[int, str]:
    out = {}
    for line in (HERE / "script.txt").read_text().splitlines():
        if "|" in line:
            n, text = line.split("|", 1)
            out[int(n)] = text.strip()
    return out


def synth(voice_id: str, model: str, n: int, text: str, stability: float,
          similarity: float, speed: float) -> None:
    audio_dir = HERE / "audio"
    audio_dir.mkdir(exist_ok=True)
    mp3, wav = audio_dir / f"s{n}.mp3", audio_dir / f"s{n}.wav"

    body = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": speed,
        },
    }
    with httpx.Client(timeout=180) as c:
        r = c.post(
            f"{API}/text-to-speech/{voice_id}",
            headers=headers(),
            params={"output_format": "mp3_44100_128"},
            json=body,
        )
    if r.status_code != 200:
        # Surface the API's own message, never the key.
        sys.exit(f"scene {n} failed ({r.status_code}): {r.text[:300]}")

    mp3.write_bytes(r.content)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
         "-ar", "48000", "-ac", "2", str(wav)],
        check=True,
    )
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(wav)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    mp3.unlink()
    print(f"  scene {n}: {float(dur):5.1f}s  {text[:58]}...")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="Show available voices and models")
    ap.add_argument("--voice", help="voice_id or voice name")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--only", type=int, help="Regenerate a single scene")
    ap.add_argument("--stability", type=float, default=0.45,
                    help="Lower = more expressive. 0.45 suits narration.")
    ap.add_argument("--similarity", type=float, default=0.80)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()

    if a.list or not a.voice:
        list_assets()
        if not a.voice:
            print("\nThen: python tts_elevenlabs.py --voice <id or name>")
        return

    vid = resolve_voice(a.voice)
    lines = scenes()
    todo = {a.only: lines[a.only]} if a.only else lines
    print(f"key {masked()} · voice {vid} · model {a.model}\n")
    for n in sorted(todo):
        synth(vid, a.model, n, todo[n], a.stability, a.similarity, a.speed)
    print("\nDone. Rebuild the video:  python build_video.py")


if __name__ == "__main__":
    main()
