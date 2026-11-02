"""Fetch a 10-K's Item 1A Risk Factors from SEC EDGAR, ask an LLM for the top 5
risks as JSON, and save the results for check.py.

Usage:
    export SEC_USER_AGENT="Your Name your.email@example.com"
    python summarize.py HPQ --extract-only     # fetch + extract, no LLM call
    python summarize.py HPQ                    # full run (run 1)
    python summarize.py HPQ --run 2            # consistency run 2
    python summarize.py HPQ --html-file x.htm  # use a local filing, no EDGAR

Design choices are logged in DECISIONS.md; decision numbers are cited below.
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import Comment, Declaration, Doctype, ProcessingInstruction

from textnorm import normalize

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "outputs"
CACHE_DIR = ROOT / "cache"  # raw filings; gitignored

# Decision 3: pin the exact filing so reruns read the same document.
# Leave as None to resolve the latest original 10-K, then paste the accession
# number that the run prints into this table and into DECISIONS.md.
PINNED_ACCESSIONS = {
    "HPQ": "0000047217-25-000071",  # FY ended 2025-10-31, filed 2025-12-10
    "DELL": "0001571996-26-000008",  # FY ended 2026-01-30, filed 2026-03-16
}

# Pin the model so a rerun next month uses the same one. The model actually used
# is also read back from the reply and logged with every call.
# Opus: the hard part is judging which risks matter, and 6 calls make the usage cost negligible.
MODEL = "claude-opus-5-5"

SEC_MIN_INTERVAL = 0.15  # seconds between requests; SEC allows max 10/second
_last_request = 0.0


# ---------------------------------------------------------------- logging

_log_lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    _log_lines.append(msg)


def save_log(path: Path) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n\n")


# ---------------------------------------------------------------- EDGAR

def sec_get(url: str, user_agent: str) -> requests.Response:
    """GET from SEC with the required User-Agent and a simple rate limit."""
    global _last_request
    wait = SEC_MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
    _last_request = time.monotonic()
    resp.raise_for_status()
    return resp


def ticker_to_cik(ticker: str, user_agent: str) -> tuple[int, str]:
    data = sec_get("https://www.sec.gov/files/company_tickers.json", user_agent).json()
    for row in data.values():
        if row["ticker"].upper() == ticker.upper():
            return int(row["cik_str"]), row["title"]
    sys.exit(f"Ticker {ticker} not found in SEC's company list.")


def find_filing(cik: int, accession: str | None, user_agent: str) -> dict:
    """Return metadata for the pinned filing, or the latest original 10-K.

    Decision 3: form type must be exactly "10-K"; 10-K/A amendments are skipped.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
    recent = sec_get(url, user_agent).json()["filings"]["recent"]
    rows = [
        {
            "form": recent["form"][i],
            "accession": recent["accessionNumber"][i],
            "filing_date": recent["filingDate"][i],
            "report_date": recent["reportDate"][i],
            "primary_document": recent["primaryDocument"][i],
        }
        for i in range(len(recent["form"]))
    ]
    if accession:
        for row in rows:
            if row["accession"] == accession:
                if row["form"] != "10-K":
                    sys.exit(f"Pinned accession {accession} is a {row['form']}, not a 10-K.")
                return row
        sys.exit(f"Pinned accession {accession} not found in recent filings.")
    for row in rows:  # newest first
        if row["form"] == "10-K":
            return row
    sys.exit("No original 10-K found in recent filings.")


def fetch_filing_html(cik: int, filing: dict, user_agent: str) -> str:
    """Download the filing's main document, caching it locally."""
    CACHE_DIR.mkdir(exist_ok=True)
    cached = CACHE_DIR / f"{cik}_{filing['accession']}.htm"
    if cached.exists():
        log(f"Using cached filing: {cached.name}")
        return cached.read_text(encoding="utf-8")
    acc_path = filing["accession"].replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_path}/"
           f"{filing['primary_document']}")
    log(f"Downloading {url}")
    resp = sec_get(url, user_agent)
    resp.encoding = resp.encoding or "utf-8"
    cached.write_text(resp.text, encoding="utf-8")
    return resp.text


# ---------------------------------------------------------------- HTML -> blocks

BLOCK_TAGS = {"p", "div", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
              "tr", "table", "body", "center", "blockquote"}
BOLD_STYLE = re.compile(r"font-weight\s*:\s*(bold|bolder|[6-9]00)", re.I)
ITALIC_STYLE = re.compile(r"font-style\s*:\s*italic", re.I)
HIDDEN_STYLE = re.compile(r"display\s*:\s*none", re.I)


def _is_hidden(tag: Tag) -> bool:
    return tag.name == "ix:header" or bool(HIDDEN_STYLE.search(tag.get("style", "")))


def _emphasis(node: NavigableString) -> tuple[bool, bool]:
    """(bold, italic) for a text node, from tags or inline styles above it."""
    bold = italic = False
    for parent in node.parents:
        if not isinstance(parent, Tag):
            continue
        style = parent.get("style", "")
        if parent.name in ("b", "strong") or BOLD_STYLE.search(style):
            bold = True
        if parent.name in ("i", "em") or ITALIC_STYLE.search(style):
            italic = True
    return bold, italic


def html_to_blocks(html: str) -> list[dict]:
    """Flatten HTML into ordered text blocks, detecting headings before the
    formatting is lost (Decision 12).

    A block is the run of text whose nearest block-level ancestor is the same
    element. A block is a heading when ALL of its text is bold and/or italic.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "head", "title"]):
        tag.decompose()
    for tag in soup.find_all(_is_hidden):  # e.g. inline XBRL header data
        tag.decompose()

    groups: list[dict] = []
    current_key = None
    for node in soup.descendants:
        if isinstance(node, Tag) and node.name == "br":
            if groups:
                groups[-1]["parts"].append((" ", False, False))
            continue
        if not isinstance(node, NavigableString) or isinstance(
                node, (Comment, Declaration, Doctype, ProcessingInstruction)):
            continue
        block = next((p for p in node.parents if isinstance(p, Tag) and p.name in BLOCK_TAGS), None)
        key = id(block)
        if key != current_key:
            groups.append({"parts": []})
            current_key = key
        groups[-1]["parts"].append((str(node), *_emphasis(node)))

    blocks = []
    for g in groups:
        text = normalize("".join(p[0] for p in g["parts"]))
        if not text:
            continue
        visible = [(t, b, i) for t, b, i in g["parts"] if t.strip()]
        all_bold = all(b for _, b, _ in visible)
        all_italic = all(i for _, _, i in visible)
        style = None
        if len(text) <= 500 and (all_bold or all_italic):
            style = "+".join(s for s, on in (("bold", all_bold), ("italic", all_italic)) if on)
        blocks.append({"text": text, "heading": style})
    return blocks


# ---------------------------------------------------------------- section extraction

ITEM_1A = re.compile(r"^item\s*1a(?:[\s.:\-]|$)", re.I)
NEXT_ITEM = re.compile(r"^item\s*(?:1b|1c|2)(?:[\s.:\-]|$)", re.I)
# Page furniture repeated at every page break inside the section.
PAGE_NOISE = re.compile(r"^(?:\d{1,3}|[ivxlc]{1,6}|table of contents|index)$", re.I)


SENTENCE_END = re.compile(r"[.!?:;\"')\]]$")


def merge_page_splits(blocks: list[dict]) -> tuple[list[dict], int]:
    """Rejoin paragraphs that a page break split mid-sentence.

    A body block that starts in lowercase, after a body block that does not end
    a sentence, is the continuation of that paragraph.
    """
    merged, count = [], 0
    for b in blocks:
        prev = merged[-1] if merged else None
        if (prev and not prev["heading"] and not b["heading"]
                and not SENTENCE_END.search(prev["text"]) and b["text"][:1].islower()):
            prev["text"] = prev["text"] + " " + b["text"]
            count += 1
        else:
            merged.append(dict(b))
    return merged, count


def extract_risk_factors(blocks: list[dict]) -> tuple[list[dict], dict]:
    """Return Item 1A's blocks and data-quality info (Decision 4).

    Uses the LAST "Item 1A" heading, because earlier ones are usually the
    table of contents, and ends at the next Item (1B, 1C or 2).
    """
    starts = [i for i, b in enumerate(blocks) if ITEM_1A.match(b["text"])]
    if not starts:
        sys.exit("No 'Item 1A' heading found. Check the filing layout.")
    start = starts[-1]
    end = next((j for j in range(start + 1, len(blocks)) if NEXT_ITEM.match(blocks[j]["text"])), None)
    if end is None:
        sys.exit("Found 'Item 1A' but no following Item 1B/1C/2 heading.")

    body, dropped = [], 0
    for b in blocks[start + 1:end]:
        # Skip a bare "Risk Factors" title split from "Item 1A." into its own cell.
        if not body and b["text"].lower().rstrip(".") == "risk factors":
            continue
        if PAGE_NOISE.match(b["text"]):
            dropped += 1
            continue
        body.append(b)
    body, merged = merge_page_splits(body)

    # Character offsets into the joined, normalized section text. check.py
    # uses these to map a verified quote back to its risk heading.
    pos = 0
    for b in body:
        b["start"] = pos
        pos += len(b["text"]) + 1

    info = {
        "item_1a_matches": len(starts),
        "end_heading": blocks[end]["text"][:60],
        "page_noise_blocks_dropped": dropped,
        "page_splits_merged": merged,
        "blocks": len(body),
        "headings": sum(1 for b in body if b["heading"]),
    }
    return body, info


# ---------------------------------------------------------------- LLM

PROMPT_TEMPLATE = """You are helping an equity or credit analyst review the "Item 1A. Risk Factors" \
section of {company}'s annual report on Form 10-K (fiscal year ended {report_date}).

Identify the 5 risks that matter most to an analyst deciding whether to invest in or lend to \
this company. Rank them 1 (most important) to 5.

For each risk return:
- "rank": an integer from 1 to 5
- "title": a short name for the risk (at most 12 words)
- "why_it_matters": 1-2 sentences on why it matters to an investor or lender
- "exact_quote": ONE contiguous passage of 10-50 words from the section that supports the risk

Rules for exact_quote:
- Copy it word for word from the section. Do not paraphrase, shorten, reorder or fix anything.
- Do not use ellipses ("..." or the "…" character) to cut words out.
- Keep the original capitalization, even if the passage starts mid-sentence.

Rules for numbers (in every field):
- Use a number only if it appears in the section, and copy it exactly as written, \
with the same format and units (for example "$1.2 billion" stays "$1.2 billion").
- Do not calculate, round, convert or estimate numbers. Do not add dates or years \
that are not in the section.

Answer with JSON only, no markdown fences and no other text, in exactly this shape:
{{"risks": [{{"rank": 1, "title": "...", "why_it_matters": "...", "exact_quote": "..."}}]}}

<risk_factors_section>
{section}
</risk_factors_section>
"""


def ask_llm(prompt: str) -> tuple[str, dict]:
    """Send a prompt to Claude; return (reply text, full reply envelope). Decision 5.

    Uses Claude Code's non-interactive mode with the Pro login. The prompt goes
    via stdin, so long sections avoid argument-length limits and shell quoting.
    Each call is isolated so runs can't influence each other:
      --tools ""                 no built-in tools (it can't read outputs/)
      --strict-mcp-config        no MCP servers (no --mcp-config is given)
      --no-session-persistence   nothing saved for later sessions
      cwd = empty temp folder    no project files or CLAUDE.md in reach
    Switching to the paid API later means rewriting only this function.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is set, which would bill the API instead of "
                 "using your Pro plan. Unset it and run again.")
    if shutil.which("claude") is None:
        sys.exit("The 'claude' command was not found. Install Claude Code and log in first.")

    cmd = ["claude", "-p", "--output-format", "json", "--model", MODEL,
           "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="filing-summarizer-") as empty_dir:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", timeout=900, cwd=empty_dir)
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        sys.exit(f"claude exited with code {proc.returncode}:\n{proc.stderr.strip()}")

    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        sys.exit(f"claude reported an error: {envelope.get('result')}")

    usage = envelope.get("usage") or {}
    # Claude Code reports most input under cache fields, so add them up.
    input_total = sum(usage.get(k) or 0 for k in
                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    models_used = sorted((envelope.get("modelUsage") or {}).keys()) or ["not reported"]
    log(f"LLM call: {elapsed:.0f}s, model requested {MODEL}, used {', '.join(models_used)}, "
        f"turns {envelope.get('num_turns', '?')}")
    log(f"          input tokens {input_total:,} (cache read {usage.get('cache_read_input_tokens') or 0:,}), "
        f"output tokens {usage.get('output_tokens') or 0:,}")
    if envelope.get("num_turns", 1) != 1:
        log("WARNING: more than one turn. The model should answer directly with tools off.")
    return envelope["result"], envelope


def parse_model_json(reply: str) -> dict:
    """Parse the model's JSON, tolerating stray markdown fences."""
    text = reply.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    return json.loads(text)


# ---------------------------------------------------------------- outputs

def write_markdown(path: Path, meta: dict, result: dict) -> None:
    lines = [
        f"# Top 5 risks: {meta['company']} ({meta['ticker']})",
        "",
        f"Source: Form 10-K, fiscal year ended {meta['report_date']}, "
        f"accession {meta['accession']}, Item 1A Risk Factors. Run {meta['run']}.",
        "",
        "Quotes are unverified until check.py has run.",
        "",
    ]
    for r in result.get("risks", []):
        lines += [
            f"## {r.get('rank')}. {r.get('title')}",
            "",
            r.get("why_it_matters", ""),
            "",
            f"> {r.get('exact_quote', '')}",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", help="e.g. HPQ or DELL")
    ap.add_argument("--accession", help="override the pinned accession number")
    ap.add_argument("--run", type=int, default=1, help="run number for consistency testing")
    ap.add_argument("--extract-only", action="store_true", help="stop before calling the LLM")
    ap.add_argument("--html-file", type=Path, help="use a local 10-K HTML file instead of EDGAR")
    args = ap.parse_args()

    ticker = args.ticker.upper()
    OUT_DIR.mkdir(exist_ok=True)
    log(f"=== summarize.py {ticker} run {args.run} - {dt.datetime.now().astimezone():%Y-%m-%d %H:%M %Z} ===")

    if args.html_file:
        html = args.html_file.read_text(encoding="utf-8")
        meta = {"ticker": ticker, "company": ticker, "form": "10-K (local file)",
                "accession": args.html_file.name, "filing_date": "n/a", "report_date": "n/a"}
    else:
        user_agent = os.environ.get("SEC_USER_AGENT")
        if not user_agent or "@" not in user_agent or len(user_agent.split()) < 2:
            sys.exit('Set SEC_USER_AGENT to your name and email, e.g.\n'
                     '  export SEC_USER_AGENT="Jane Doe jane@example.com"\n'
                     "SEC EDGAR rejects requests without one.")
        cik, company = ticker_to_cik(ticker, user_agent)
        accession = args.accession or PINNED_ACCESSIONS.get(ticker)
        filing = find_filing(cik, accession, user_agent)
        meta = {"ticker": ticker, "company": company, **filing}
        if not accession:
            log(f"No accession pinned for {ticker}. Resolved latest 10-K: {filing['accession']}")
            log("  -> Pin it in PINNED_ACCESSIONS and DECISIONS.md for reproducible runs.")
        html = fetch_filing_html(cik, filing, user_agent)

    meta["run"] = args.run
    log(f"Filing: {meta['company']} | form {meta['form']} | accession {meta['accession']} | "
        f"filed {meta['filing_date']} | period {meta['report_date']}")

    # ---- extraction + data-quality checks (Decision 4)
    blocks = html_to_blocks(html)
    section, info = extract_risk_factors(blocks)
    section_text = "\n\n".join(b["text"] for b in section)
    words = len(section_text.split())
    log(f"Item 1A matches in document: {info['item_1a_matches']} (used the last one)")
    log(f"Section ends at: {info['end_heading']!r}")
    log(f"Section length: {len(section_text):,} characters, {words:,} words, "
        f"{info['blocks']} paragraphs, {info['headings']} headings detected")
    log(f"Page numbers / 'Table of Contents' lines removed: {info['page_noise_blocks_dropped']}")
    log(f"Paragraphs split by page breaks, rejoined: {info['page_splits_merged']}")
    log(f"First line: {section[0]['text'][:120]!r}")
    tail = section[-1]["text"]
    if len(tail) > 120:
        tail = "..." + tail[-120:].split(" ", 1)[-1]
    log(f"Last line:  {tail!r}")
    if words < 2000:
        log("WARNING: section is short. The table of contents may have been extracted. Check it.")

    (OUT_DIR / f"section_{ticker}.txt").write_text(section_text, encoding="utf-8")
    (OUT_DIR / f"filing_{ticker}.json").write_text(
        json.dumps({"meta": meta, "extraction": info, "blocks": section}, indent=1), encoding="utf-8")
    log(f"Saved outputs/section_{ticker}.txt and outputs/filing_{ticker}.json")

    if args.extract_only:
        log("--extract-only: stopping before the LLM call.")
        save_log(OUT_DIR / "run_output.txt")
        return

    # ---- LLM call (Decisions 5-7)
    prompt = PROMPT_TEMPLATE.format(company=meta["company"], report_date=meta["report_date"],
                                    section=section_text)
    reply, envelope = ask_llm(prompt)
    raw_path = OUT_DIR / f"raw_reply_{ticker}_run{args.run}.txt"
    raw_path.write_text(reply, encoding="utf-8")
    # Full envelope: model, turns, usage, session id. Evidence the call was isolated.
    (OUT_DIR / f"llm_envelope_{ticker}_run{args.run}.json").write_text(
        json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        result = parse_model_json(reply)
    except json.JSONDecodeError as e:
        log(f"Model reply is not valid JSON ({e}). Raw reply saved to {raw_path.name}.")
        save_log(OUT_DIR / "run_output.txt")
        sys.exit(1)

    result["_meta"] = {**meta, "model_requested": MODEL,
                       "models_used": sorted((envelope.get("modelUsage") or {}).keys())}
    (OUT_DIR / f"summary_{ticker}_run{args.run}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(OUT_DIR / f"summary_{ticker}_run{args.run}.md", meta, result)
    log(f"Model returned {len(result.get('risks', []))} risks. "
        f"Saved outputs/summary_{ticker}_run{args.run}.json. Next: python check.py {ticker}")
    save_log(OUT_DIR / "run_output.txt")


if __name__ == "__main__":
    main()
