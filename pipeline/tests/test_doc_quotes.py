"""Generated sentences quoted verbatim in the docs must equal the generator's current output (no drift, no typing)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def blockquote_after(doc: Path, marker: str) -> str:
    """The first Markdown block quote after the line containing `marker`, joined into one sentence."""
    lines = doc.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    quote = []
    for line in lines[start + 1:]:
        if line.startswith(">"):
            quote.append(line[1:].strip())
        elif quote:
            break
    assert quote, f"{doc.name}: no block quote after {marker!r}"
    return " ".join(quote)


def test_footprint_quotes_the_current_stake_sentence():
    card = json.loads((ROOT / "data" / "derived" / "backtest.json").read_text(encoding="utf-8"))["card"]
    quoted = blockquote_after(ROOT / "docs" / "FOOTPRINT.md", "card.stakeSentence")
    assert quoted == card["stakeSentence"], "docs/FOOTPRINT.md must quote backtest.json card.stakeSentence verbatim"
