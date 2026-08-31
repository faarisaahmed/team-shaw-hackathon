"""Render the git history and the risk-limit source as deck cards."""
import html, re, subprocess
from pathlib import Path

TERM = Path("assets/term")
CSS = """
body{margin:0;background:#0d0d0d;font-family:ui-sans-serif,-apple-system,sans-serif}
.win{width:1180px;background:#111110;border:1px solid rgba(255,255,255,.11);border-radius:12px;overflow:hidden}
.bar{display:flex;align-items:center;gap:9px;padding:12px 16px;background:#1a1a19;
     border-bottom:1px solid rgba(255,255,255,.08)}
.dot{width:11px;height:11px;border-radius:50%}
.d1{background:#e34948}.d2{background:#eda100}.d3{background:#1baf7a}
.title{margin-left:8px;font-size:13px;color:#898781;font-family:ui-monospace,Menlo,monospace}
.cap{margin-left:auto;font-size:12px;color:#5f5e5a}
pre{margin:0;padding:20px 22px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
    font-size:13px;line-height:1.55;color:#c3c2b7;white-space:pre;overflow:hidden}
.h{color:#5f5e5a}.k{color:#3987e5}.n{color:#eda100}.s{color:#1baf7a}.c{color:#5f5e5a;font-style:italic}
"""

def git_html(t):
    out = []
    for line in html.escape(t).split("\n"):
        m = re.match(r"^([0-9a-f]{7})(\s+)(\w{3} \d+ [\d:]+)(\s+)(.*)$", line)
        if m:
            out.append(f'<span class="n">{m[1]}</span>{m[2]}<span class="h">{m[3]}</span>{m[4]}{m[5]}')
        elif "commits ·" in line:
            out.append(f'<span class="s">{line}</span>')
        else:
            out.append(line)
    return "\n".join(out)

def py_html(t):
    o = html.escape(t)
    o = re.sub(r"(#.*)$", r'<span class="c">\1</span>', o, flags=re.M)
    o = re.sub(r"\b(class|int|float|bool|str)\b", r'<span class="k">\1</span>', o)
    o = re.sub(r"(?<![\w.])(\d[\d_]*\.?\d*)\b", r'<span class="n">\1</span>', o)
    o = re.sub(r"\b(True|False)\b", r'<span class="s">\1</span>', o)
    return o

for name, cmd, cap, fn in [
    ("gitlog", "git log --oneline", "Built over two days", git_html),
    ("risklimits", "src/capitoldesk/config.py", "Every limit is a number in a file, not a prompt", py_html),
]:
    body = (TERM / f"{name}.txt").read_text().rstrip("\n")
    page = (f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
            f"<div class='win'><div class='bar'>"
            f"<span class='dot d1'></span><span class='dot d2'></span><span class='dot d3'></span>"
            f"<span class='title'>{html.escape(cmd)}</span><span class='cap'>{html.escape(cap)}</span>"
            f"</div><pre>{fn(body)}</pre></div></body></html>")
    p = TERM / f"{name}.html"; p.write_text(page)
    subprocess.run(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--headless","--disable-gpu","--hide-scrollbars","--force-device-scale-factor=2",
        "--virtual-time-budget=2500", f"--screenshot={TERM}/{name}.png",
        "--window-size=1180,1500", str(p)], capture_output=True)
    subprocess.run(["magick", f"{TERM}/{name}.png", "-trim", "+repage", f"{TERM}/{name}.png"], check=True)
    print(f"  {name}.png")
