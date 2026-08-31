# Submission media

| File | What it is |
|---|---|
| `capitol-desk-deck.pdf` | 10-slide pitch deck, 16:9 |
| `video/capitol-desk.mp4` | 4m18s pitch video, 1080p, narrated (ElevenLabs) |
| `video/script.txt` | Narration, one line per scene (`scene\|text`) |
| `deck.html` | Source for the deck |
| `video/gen_scenes.py` | Generates the nine 1920×1080 scene frames |
| `video/build_video.py` | Assembles frames + narration into the MP4 |
| `video/tts_elevenlabs.py` | Generates the narration track via ElevenLabs |

Everything on screen is real: the filings are actual PTRs pulled from the House
Clerk, the dashboard is the live deployment, and every figure comes from the
committed journal and event study.

## Narration

Generated with ElevenLabs (voice: *Daniel — Steady Broadcaster*,
`onwK4e9ZLuTAKqWW03F9`, model `eleven_multilingual_v2`).

`ELEVENLABS_API_KEY` is read from the repo's gitignored `.env` and never
printed or logged. To regenerate:

```bash
cd media/video
python tts_elevenlabs.py --list                    # voices and models (free)
python tts_elevenlabs.py --voice onwK4e9ZLuTAKqWW03F9
python tts_elevenlabs.py --voice <id> --only 5     # redo a single scene
python build_video.py
```

Full script is ~3,350 characters, so one pass is inexpensive.
`--stability` (default 0.45) trades consistency for expressiveness; narration
goes flat above ~0.6.

### Using a human voice instead

Record the lines in `script.txt`, save as `audio/s1.wav` … `s9.wav`, and run
`python build_video.py`. Scene durations, crossfade offsets and the audio
timeline are all derived from the actual file lengths, so the video re-times
itself around any recording — no manual syncing.

## Regenerating the visuals

```bash
# scene frames (after editing gen_scenes.py)
python gen_scenes.py
for n in 1 2 3 4 5 6 7 8 9; do
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
    --hide-scrollbars --virtual-time-budget=5000 \
    --screenshot="frames/s${n}.png" --window-size=1920,1080 "frames/s${n}.html"
done

# deck
cd media && "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --disable-gpu --no-pdf-header-footer --print-to-pdf=capitol-desk-deck.pdf \
  --virtual-time-budget=6000 deck.html
```

Audio is normalised to −16 LUFS (web standard) by `loudnorm` during assembly.
