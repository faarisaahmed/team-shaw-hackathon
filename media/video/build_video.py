"""Assemble the pitch video: still scenes, crossfades, narration synced per scene."""
import subprocess, json, shlex
from pathlib import Path

N = 9
TRANS = 0.5      # crossfade length
TAIL = 0.8       # breathing room after each narration line
FPS = 30

def dur(p):
    out = subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration","-of","json",str(p)],
        capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])

audio = [Path(f"audio/s{i}.wav") for i in range(1, N+1)]
adur  = [dur(a) for a in audio]
cdur  = [a + TAIL for a in adur]          # clip length
# Scene i begins here on the master timeline (each xfade eats TRANS seconds).
starts, t = [], 0.0
for i in range(N):
    starts.append(t)
    t += cdur[i] - TRANS
total = sum(cdur) - TRANS * (N - 1)

inputs, filt = [], []
for i in range(N):
    inputs += ["-loop","1","-t",f"{cdur[i]:.3f}","-i",f"frames/s{i+1}.png"]
for i in range(N):
    inputs += ["-i", str(audio[i])]

# Video: normalise each still, then chain crossfades.
for i in range(N):
    filt.append(f"[{i}:v]scale=1920:1080,setsar=1,format=yuv420p,fps={FPS}[v{i}]")
prev, off = "v0", 0.0
for i in range(1, N):
    off += cdur[i-1] - TRANS
    out = f"x{i}"
    filt.append(f"[{prev}][v{i}]xfade=transition=fade:duration={TRANS}:offset={off:.3f}[{out}]")
    prev = out

# Audio: delay each line to its scene start, then mix onto one bed.
for i in range(N):
    ms = int(starts[i] * 1000)
    filt.append(f"[{N+i}:a]adelay={ms}|{ms},volume=1.0[a{i}]")
filt.append("".join(f"[a{i}]" for i in range(N)) +
            f"amix=inputs={N}:normalize=0:dropout_transition=0,"
            f"apad,atrim=0:{total:.3f},"
            # Bring narration up to the ~-16 LUFS people expect from web video;
            # raw `say` output lands around -19 dB mean and reads as quiet.
            f"loudnorm=I=-16:TP=-1.5:LRA=11,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]")

cmd = ["ffmpeg","-y","-loglevel","error", *inputs,
       "-filter_complex", ";".join(filt),
       "-map", f"[{prev}]", "-map","[aout]",
       "-c:v","libx264","-preset","medium","-crf","18","-pix_fmt","yuv420p",
       "-c:a","aac","-b:a","192k","-movflags","+faststart",
       "-t", f"{total:.3f}", "capitol-desk.mp4"]

print("scene timeline:")
for i in range(N):
    print(f"  {i+1}: start {starts[i]:6.1f}s  narration {adur[i]:5.1f}s  clip {cdur[i]:5.1f}s")
print(f"total: {total:.1f}s ({int(total//60)}m{int(total%60):02d}s)\n")
subprocess.run(cmd, check=True)
print("wrote capitol-desk.mp4")
