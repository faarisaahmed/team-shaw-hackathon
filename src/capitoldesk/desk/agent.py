"""The portfolio-manager agent.

Division of responsibility across the system:

  deterministic pipeline  opens positions  (filing -> candidates -> caps -> order)
  this agent              reviews the book (open-ended: exits, concentration, decay)

Entries are mechanical and must be reproducible, so they stay in code. Reviewing
a live book is genuinely open-ended - it needs to look things up, compare, and
change its mind - which is what an agent with tools is actually for.

Market access is via Alpaca's MCP server, so the agent reads real positions and
real quotes rather than a snapshot we pre-digested for it.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from ..config import ROOT, SETTINGS
from ..execute import ledger

log = logging.getLogger(__name__)

UVX = str(Path.home() / ".local" / "bin" / "uvx")

SYSTEM = """\
You are the risk manager for Capitol Desk, a paper-trading options book that \
mirrors publicly disclosed congressional stock trades (STOCK Act filings).

You have live Alpaca tools. Use them to inspect the real account and real quotes; \
do not rely on anything you were told without checking it.

Your job on each review:
1. Pull the account and every open option position.
2. For each position, assess: P&L, days to expiry, and whether the original \
thesis still holds. The thesis was always "a member of Congress disclosed buying \
this name" - so ask whether time or price action has already spent that edge.
3. Flag anything that needs action: positions nearing expiry, large unrealised \
losses, concentration in one underlying or one sector, or contracts that have \
become illiquid.
4. Recommend concrete exits where warranted, with a reason.

Be concise and specific. Quote real numbers from the tools. Do NOT place or \
close orders - you are advisory here; entries and exits are executed by the desk \
pipeline after a human or the risk gate signs off. If the book is empty or \
healthy, say so plainly and briefly rather than manufacturing concerns."""


def _mcp_config() -> dict:
    return {
        "alpaca": {
            "type": "stdio",
            "command": UVX,
            "args": ["alpaca-mcp-server"],
            "env": {
                **os.environ,
                "ALPACA_API_KEY": SETTINGS.alpaca_key,
                "ALPACA_SECRET_KEY": SETTINGS.alpaca_secret,
                "ALPACA_PAPER_TRADE": "true",
            },
        }
    }


def _briefing() -> str:
    trades = ledger.recent_trades(40)
    if not trades:
        return "The desk journal is empty - no positions have been opened yet."
    lines = ["Positions this desk opened, and why:"]
    for t in trades:
        lines.append(
            f"- {t['contract_symbol']} ({t['ticker']}) x{t['contracts']} "
            f"@ ${t['limit_price']:.2f}, ${t['notional']:,.0f} notional, "
            f"conviction {t['conviction']:.2f}, status {t['status']}\n"
            f"    mirroring {t['member']}; rationale: {t['rationale']}"
        )
    return "\n".join(lines)


async def review(extra: str = "") -> str:
    """Run one portfolio review. Returns the agent's written note."""
    SETTINGS.require()

    options = ClaudeAgentOptions(
        model=SETTINGS.model,
        system_prompt=SYSTEM,
        mcp_servers=_mcp_config(),
        # Read-only surface. The agent advises; it cannot place orders.
        allowed_tools=[
            "mcp__alpaca__get_account_info",
            "mcp__alpaca__get_all_positions",
            "mcp__alpaca__get_open_position",
            "mcp__alpaca__get_option_snapshot",
            "mcp__alpaca__get_option_contract",
            "mcp__alpaca__get_option_contracts",
            "mcp__alpaca__get_stock_latest_quote",
            "mcp__alpaca__get_orders",
        ],
        permission_mode="dontAsk",
        cwd=str(ROOT),
    )

    prompt = f"""{_briefing()}

{extra}

Review the book now. Start by pulling the account and all open positions with your \
Alpaca tools, then give me the desk note."""

    out: list[str] = []
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    out.append(block.text)
    return "\n".join(out).strip()
