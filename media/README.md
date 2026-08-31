# Submission media

| File | What it is |
|---|---|
| `capitol-desk-deck.pdf` | 10-slide pitch deck, 16:9 |
| `video/capitol-desk.mp4` | 3m21s pitch video, 1080p, narrated |
| `video/script.txt` | Narration, one line per scene (`scene\|text`) |
| `deck.html` | Source for the deck |
| `video/gen_scenes.py` | Generates the nine 1920×1080 scene frames |
| `video/build_video.py` | Assembles frames + narration into the MP4 |

Everything on screen is real: the filings are actual PTRs pulled from the House
Clerk, the dashboard is the live deployment, and every figure comes from the
committed journal and event study.

## Re-recording the narration in your own voice

The video ships with macOS text-to-speech so it is complete and submittable, but
**a human voice is noticeably better for a pitch.** Swapping it in is one command:

1. Record each scene separately using the lines in `video/script.txt`.
2. Save them as `video/audio/s1.wav` … `s9.wav`, overwriting the existing files
   (48 kHz WAV; any sample rate works, ffmpeg resamples).
3. Rebuild:

```bash
cd media/video && python build_video.py
```

Scene durations, crossfade offsets and the audio timeline are all derived from
the actual length of each file, so the video re-times itself around your
recording. No manual syncing.

## Regenerating the TTS narration

Audio files are not committed (45 MB, and fully reproducible). To rebuild the
stock narration from `script.txt`:

```bash
cd media/video
while IFS='|' read -r n text; do
  say -v Samantha -r 172 -o "audio/s${n}.aiff" "$text"
  ffmpeg -y -loglevel error -i "audio/s${n}.aiff" -ar 48000 -ac 2 "audio/s${n}.wav"
done < script.txt
python build_video.py
```

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
