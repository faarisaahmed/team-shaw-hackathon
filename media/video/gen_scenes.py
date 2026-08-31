"""Generate 1920x1080 scene HTML for the pitch video."""
from pathlib import Path

CSS = """
:root{--plane:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
      --line:#2c2c2a;--border:rgba(255,255,255,.10);--accent:#3987e5;--good:#0ca30c;
      --crit:#d03b3b;--warn:#fab219}
*{box-sizing:border-box;margin:0;padding:0}
body{width:1920px;height:1080px;background:var(--plane);color:var(--ink);overflow:hidden;
     font-family:ui-sans-serif,-apple-system,"Helvetica Neue",Inter,sans-serif;-webkit-font-smoothing:antialiased}
.s{width:1920px;height:1080px;padding:96px 116px;display:flex;flex-direction:column;position:relative}
.eyebrow{font-size:20px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:22px}
h1{font-size:88px;font-weight:660;letter-spacing:-.03em;line-height:1.03}
h2{font-size:60px;font-weight:640;letter-spacing:-.024em;line-height:1.1;margin-bottom:20px}
.lede{font-size:30px;line-height:1.48;color:var(--ink2);max-width:1340px}
.sm{font-size:24px;line-height:1.55;color:var(--ink2)}
.xs{font-size:20px;line-height:1.5;color:var(--muted)}
.grow{flex:1}.rule{height:1px;background:var(--line);margin:30px 0}
.accent{color:var(--accent)}.good{color:var(--good)}.crit{color:var(--crit)}.warn{color:var(--warn)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.cols{display:grid;gap:28px}.c2{grid-template-columns:1fr 1fr}.c3{grid-template-columns:repeat(3,1fr)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:32px 34px}
.card h3{font-size:20px;font-weight:620;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);margin-bottom:16px}
.big{font-size:96px;font-weight:660;letter-spacing:-.035em;line-height:1}
.lbl{font-size:21px;color:var(--muted);margin-top:12px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th{font-size:19px;font-weight:560;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
   text-align:left;padding:0 20px 14px 0;border-bottom:1px solid var(--line)}
td{font-size:30px;padding:20px 20px 20px 0;border-bottom:1px solid var(--line)}
td.n,th.n{text-align:right}
.flow{display:flex;gap:13px;margin-top:14px}
.step{flex:1;background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:22px 20px}
.step .k{font-size:19px;font-weight:640;letter-spacing:.05em;text-transform:uppercase;margin-bottom:11px}
.step .d{font-size:18px;line-height:1.45;color:var(--ink2)}
.tag{display:inline-block;font-size:16px;letter-spacing:.05em;text-transform:uppercase;padding:5px 11px;
     border-radius:7px;border:1px solid var(--border);color:var(--muted);margin-top:14px}
.tag.llm{color:var(--accent);border-color:rgba(57,135,229,.5)}
.tag.code{color:var(--good);border-color:rgba(12,163,12,.5)}
ul{list-style:none}li{font-size:26px;line-height:1.5;color:var(--ink2);padding-left:32px;position:relative;margin-bottom:20px}
li::before{content:"";position:absolute;left:0;top:14px;width:10px;height:10px;border-radius:3px;background:var(--accent)}
li b{color:var(--ink);font-weight:620}
img{display:block;border-radius:14px;border:1px solid var(--border)}
.quote{font-size:42px;line-height:1.32;font-weight:600;letter-spacing:-.018em;color:var(--ink)}
"""

SCENES = {
1: """<div class="s" style="justify-content:center">
  <div class="eyebrow">Alpaca AI Trading Agents Hackathon</div>
  <h1>Capitol&nbsp;Desk</h1>
  <div class="lede" style="margin-top:34px;font-size:34px;max-width:1500px">
    An autonomous options desk that reads congressional financial disclosures,
    decides whether the signal is still alive, and trades it.</div>
  <div class="rule" style="margin-top:52px"></div>
  <div class="xs mono accent" style="font-size:24px">capitol-desk.onrender.com</div>
</div>""",

2: """<div class="s">
  <div class="eyebrow">The premise</div>
  <h2>Congress trades.<br>The law says they must tell us.</h2>
  <div class="lede">The STOCK Act requires disclosure of every transaction over $1,000 — within 45 days.
    Free, public, and almost unreadable.</div>
  <div class="grow"></div>
  <div class="cols c3">
    <div class="card"><div class="big">368</div><div class="lbl">Reports filed in 2026</div></div>
    <div class="card"><div class="big">31.5<span style="font-size:44px"> days</span></div><div class="lbl">Median lag, trade to disclosure</div></div>
    <div class="card"><div class="big">12<span style="font-size:44px">%</span></div><div class="lbl">Scanned paper — no machine-readable text</div></div>
  </div>
  <div class="grow"></div>
</div>""",

3: """<div class="s">
  <div class="eyebrow">Why nobody does this well</div>
  <h2>The data actively fights you</h2>
  <div class="cols c2" style="grid-template-columns:1fr .92fr;margin-top:14px">
    <div>
      <ul style="margin-top:10px">
        <li><b>12% are scanned paper.</b> One 18-page filing holds <b>274 transactions</b> and extracts zero characters.</li>
        <li><b>Paper filings print no ticker</b> — the form says "provide full name not ticker symbol."</li>
        <li><b>Every blank form carries a pre-printed example row.</b></li>
      </ul>
      <div class="card" style="padding:26px 30px;border-color:rgba(57,135,229,.35)">
        <div class="sm">Claude reads the text layer where one exists, and reads the
          <b style="color:var(--ink)">page visually</b> where it doesn't — and correctly ignores
          <span class="mono warn">Example Mega Corp</span>.</div>
      </div>
    </div>
    <img src="../../assets/scanned_ptr.png" style="width:100%;height:660px;object-fit:cover;object-position:top center">
  </div>
</div>""",

4: """<div class="s">
  <div class="eyebrow">Architecture</div>
  <h2>Six stages — the model touches two</h2>
  <div class="flow" style="margin-top:34px">
    <div class="step"><div class="k">Ingest</div><div class="d">Bulk archive → XML index → PTR PDFs</div><span class="tag code">code</span></div>
    <div class="step"><div class="k">Extract</div><div class="d">Text layer, or the page read visually</div><span class="tag llm">Claude</span></div>
    <div class="step"><div class="k">Resolve</div><div class="d">Name → ticker, verified at the broker</div><span class="tag llm">Claude + code</span></div>
    <div class="step"><div class="k">Decide</div><div class="d">Live chains, greeks, signal decay</div><span class="tag llm">Claude</span></div>
    <div class="step"><div class="k">Size</div><div class="d">Conviction → contracts, hard caps</div><span class="tag code">code</span></div>
    <div class="step"><div class="k">Execute</div><div class="d">Limit orders and spreads via Alpaca</div><span class="tag code">code</span></div>
  </div>
  <div class="grow"></div>
  <div class="rule"></div>
  <div class="quote">Claude decides <span class="accent">what</span> to trade.
    Code decides <span class="good">how much</span>.</div>
  <div class="sm" style="margin-top:22px;max-width:1500px">There is no prompt a model can write that gets it a bigger position.</div>
  <div class="grow"></div>
</div>""",

5: """<div class="s">
  <div class="eyebrow">Worked example · Filing 20035143</div>
  <h2>A real filing, a real order</h2>
  <div class="cols c2" style="grid-template-columns:.78fr 1.22fr;margin-top:10px">
    <img src="../../assets/pelosi_ptr.png" style="width:100%;height:640px;object-fit:cover;object-position:top">
    <div>
      <div class="card" style="padding:26px 32px">
        <h3>The filing</h3>
        <div class="sm">Pelosi, 21 Aug. Bloom Energy calls, <b style="color:var(--ink)">$100 strike, June 2027</b>
          — a deep ITM LEAP used as stock replacement.</div>
      </div>
      <div class="card" style="padding:26px 32px;margin-top:20px">
        <h3>The problem</h3>
        <div class="sm">BE already ran <b class="warn">+13.9%</b>. The outright now costs
          <b class="crit">$8,116</b> against a <b>$5,000</b> cap, and every affordable strike was
          100% time value at 88% IV — <b>volatility the filer never took</b>.</div>
      </div>
      <div class="card" style="padding:26px 32px;margin-top:20px;border-color:rgba(57,135,229,.4)">
        <h3 style="color:var(--accent)">The trade</h3>
        <div class="sm">Vertical debit spread: long the <span class="mono">175</span>,
          short the <span class="mono">230</span>. Same expiry, same thesis,
          <b class="good">$2,071 of risk instead of $8,116</b>.</div>
      </div>
    </div>
  </div>
</div>""",

6: """<div class="s">
  <div class="eyebrow">The part that matters</div>
  <h2>It declines far more than it takes</h2>
  <div class="cols c3" style="margin-top:34px">
    <div class="card"><div class="big">650</div><div class="lbl">Transactions parsed</div></div>
    <div class="card"><div class="big crit">635</div><div class="lbl">Passed over</div></div>
    <div class="card"><div class="big good">3</div><div class="lbl">Trades placed</div></div>
  </div>
  <div class="card" style="margin-top:34px;padding:30px 34px">
    <h3>Declined on the merits — verbatim</h3>
    <div class="sm"><b style="color:var(--ink)">PG</b> — "a routine retirement-account allocation into a
      mega-cap consumer staple, not an informed, concentrated bet … there's no thesis to express."</div>
    <div class="sm" style="margin-top:20px"><b style="color:var(--ink)">AMAT</b> — "the quote set is internally
      incoherent … no way to size or price an honest replication."
      <span class="muted">It had caught a bug in our own price feed.</span></div>
  </div>
  <div class="grow"></div>
</div>""",

7: """<div class="s">
  <div class="eyebrow">We measured it</div>
  <h2>Copying Congress indiscriminately does not work</h2>
  <div class="lede" style="font-size:26px">826 disclosed purchases. Entry on the <b style="color:var(--ink)">filing</b>
    date, not the trade date — anything earlier is lookahead bias. Excess over SPY.</div>
  <table style="margin-top:38px">
    <thead><tr><th>Horizon</th><th class="n">Events</th><th class="n">Mean excess</th><th class="n">Median</th><th class="n">Hit rate</th></tr></thead>
    <tbody>
      <tr><td>5 trading days</td><td class="n">799</td><td class="n">+0.09%</td><td class="n crit">−0.40%</td><td class="n">45.2%</td></tr>
      <tr><td>21 trading days</td><td class="n">669</td><td class="n">+0.74%</td><td class="n crit">−0.37%</td><td class="n">49.0%</td></tr>
      <tr><td>63 trading days</td><td class="n">668</td><td class="n">+0.42%</td><td class="n">+0.08%</td><td class="n">50.1%</td></tr>
    </tbody>
  </table>
  <div class="grow"></div>
  <div class="quote" style="font-size:34px">If the average disclosure is noise,<br>
    the value has to come from <span class="accent">selection</span>.</div>
  <div class="grow"></div>
</div>""",

8: """<div class="s" style="padding-bottom:0">
  <div class="eyebrow">Running now</div>
  <h2>Live, autonomous, auditable</h2>
  <div class="sm" style="max-width:1560px;font-size:26px">Real Alpaca paper account. An unattended loop watches for
    filings and holds them when the market is closed. A review agent inspects the book over
    <b style="color:var(--ink)">Alpaca's MCP server</b>.</div>
  <img src="../../assets/dash_hero.png" style="width:100%;flex:1;object-fit:cover;object-position:top;
       margin-top:34px;border-bottom:none;border-radius:16px 16px 0 0">
</div>""",

9: """<div class="s" style="justify-content:center">
  <h1 style="font-size:68px">Anyone can build an agent<br>that copies Nancy Pelosi.</h1>
  <h1 style="font-size:68px;color:var(--accent);margin-top:24px">The interesting one declines.</h1>
  <div class="rule" style="margin-top:56px"></div>
  <div class="cols c3">
    <div><div class="xs">Live demo</div><div class="sm mono accent">capitol-desk.onrender.com</div></div>
    <div><div class="xs">Source</div><div class="sm mono">github.com/faarisaahmed/team-shaw-hackathon</div></div>
    <div><div class="xs">Built with</div><div class="sm">Claude Opus 5 · Alpaca MCP Server</div></div>
  </div>
</div>""",
}

out = Path("frames")
out.mkdir(exist_ok=True)
for n, body in SCENES.items():
    Path(out / f"s{n}.html").write_text(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{CSS}</style></head>"
        f"<body>{body}</body></html>"
    )
print(f"wrote {len(SCENES)} scene files")
