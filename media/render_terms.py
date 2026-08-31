"""Render captured terminal output as styled window images for the deck."""
import html, subprocess
from pathlib import Path

TERM = Path("assets/term")
CARDS = [
    ("positions", "desk positions", "Live book, marked against Alpaca"),
    ("journal",   "desk journal",   "Every order, with its fill"),
    ("extract",   "desk extract 20035143", "One filing, fully structured"),
    ("backtest",  "desk backtest",  "Event study over 826 disclosed purchases"),
    ("tests",     "pytest tests/ -q", "Deterministic machinery under test"),
]

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
    font-size:13px;line-height:1.5;color:#c3c2b7;white-space:pre;overflow:hidden}
.g{color:#0ca30c}.r{color:#d03b3b}.b{color:#3987e5}.w{color:#fff}.y{color:#fab219}
"""

def colour(text: str) -> str:
    out = html.escape(text)
    import re
    out = re.sub(r"(\$-[\d,]+\.\d\d|-[\d.]+%)", r'<span class="r">\1</span>', out)
    out = re.sub(r"(\d+ passed)", r'<span class="g">\1</span>', out)
    out = re.sub(r"\b(filled)\b", r'<span class="g">\1</span>', out)
    out = re.sub(r"^(\$ .*)$", r'<span class="b">\1</span>', out, flags=re.M)
    return out

for name, cmd, cap in CARDS:
    body = (TERM / f"{name}.txt").read_text().rstrip("\n")
    page = (f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
            f"<div class='win'><div class='bar'>"
            f"<span class='dot d1'></span><span class='dot d2'></span><span class='dot d3'></span>"
            f"<span class='title'>{html.escape(cmd)}</span><span class='cap'>{html.escape(cap)}</span>"
            f"</div><pre>{colour(body)}</pre></div></body></html>")
    p = TERM / f"{name}.html"
    p.write_text(page)
    subprocess.run([
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--headless", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=2", "--virtual-time-budget=2500",
        f"--screenshot={TERM}/{name}.png", "--window-size=1180,1400", str(p),
    ], capture_output=True)
    # Trim the empty page below the window.
    subprocess.run(["magick", f"{TERM}/{name}.png", "-trim", "+repage",
                    f"{TERM}/{name}.png"], check=True)
    print(f"  {name}.png")
