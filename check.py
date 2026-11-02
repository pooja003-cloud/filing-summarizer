"""Verify an LLM summary against the 10-K section it came from.

Usage:
    python check.py HPQ             # check run 1
    python check.py HPQ --run 2     # check another run
    python check.py HPQ --plant     # plant 3 errors in a copy, confirm all are caught
    python check.py HPQ --file outputs/some_summary.json

Checks (DECISIONS.md decision numbers in brackets):
  structure [7]   valid JSON, 5 risks, all fields present, ranks 1-5
  quotes [9]      exact, case-sensitive match after normalize(); closest passage on failure
  numbers [10]    every number in title / why_it_matters / exact_quote, three outcomes
  location [12]   each verified quote mapped to the individual risk heading it falls under
  consistency     when 2+ runs exist, overlap of chosen risk headings between runs
"""

import argparse
import datetime as dt
import difflib
import json
import re
import sys
from itertools import combinations
from pathlib import Path

from textnorm import normalize

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "outputs"
CONTENT_FIELDS = ("title", "why_it_matters", "exact_quote")

_log: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    _log.append(msg)


# ---------------------------------------------------------------- source

def load_source(ticker: str) -> tuple[str, list[dict], list[str]]:
    section = (OUT_DIR / f"section_{ticker}.txt").read_text(encoding="utf-8")
    filing = json.loads((OUT_DIR / f"filing_{ticker}.json").read_text(encoding="utf-8"))
    src = normalize(section)
    blocks = classify_headings(filing["blocks"])
    sentences = SENT_SPLIT.split(src)
    return src, blocks, sentences


def classify_headings(blocks: list[dict]) -> list[dict]:
    """A heading followed directly by another heading is a category; one
    followed by body text is an individual risk (Decision 12)."""
    for i, b in enumerate(blocks):
        if not b["heading"]:
            b["level"] = None
            continue
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        b["level"] = "category" if nxt and nxt["heading"] else "risk"
    return blocks


def locate(pos: int, blocks: list[dict]) -> tuple[int | None, str | None]:
    """(paragraph index, individual risk heading) for a character offset."""
    para = risk = None
    for i, b in enumerate(blocks):
        if b["start"] > pos:
            break
        para = i
        if b["level"] == "category":
            risk = None
        elif b["level"] == "risk":
            risk = b["text"]
    return para, risk


# ---------------------------------------------------------------- structure

def check_structure(data: dict) -> list[str]:
    risks = data.get("risks")
    if not isinstance(risks, list):
        return ["'risks' is missing or not a list"]
    problems = []
    if len(risks) != 5:
        problems.append(f"expected 5 risks, got {len(risks)}")
    for i, r in enumerate(risks, 1):
        for field in ("rank",) + CONTENT_FIELDS:
            if r.get(field) in (None, ""):
                problems.append(f"risk {i}: '{field}' missing or empty")
    ranks = sorted(r["rank"] for r in risks if isinstance(r.get("rank"), int))
    if ranks != list(range(1, len(risks) + 1)):
        problems.append(f"ranks are {ranks}, expected 1-{len(risks)}")
    return problems


# ---------------------------------------------------------------- quotes

SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=["(A-Z])')


def closest_passage(quote: str, sentences: list[str]) -> tuple[float, str, list[str]]:
    """Best-matching passage (1-3 consecutive sentences, trimmed to the matched
    span), its similarity score and the word differences. Diagnostic only."""
    qw = quote.split()
    qset = set(qw)
    best = (0.0, "", [])
    for n in (1, 2, 3):
        for i in range(len(sentences) - n + 1):
            ww = " ".join(sentences[i:i + n]).split()
            if len(qset.intersection(ww)) < 0.3 * len(qset):
                continue  # cheap filter: too little word overlap
            sm = difflib.SequenceMatcher(None, qw, ww, autojunk=False)
            blocks = [b for b in sm.get_matching_blocks() if b.size]
            if not blocks:
                continue
            span = ww[blocks[0].b:blocks[-1].b + blocks[-1].size]
            matched = sum(b.size for b in blocks)
            score = 2 * matched / (len(qw) + len(span))
            if score > best[0]:
                best = (score, " ".join(span), word_diff(qw, span))
    return best


def word_diff(quote_words: list[str], filing_words: list[str]) -> list[str]:
    out = []
    sm = difflib.SequenceMatcher(None, quote_words, filing_words, autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        q, f = " ".join(quote_words[a1:a2]), " ".join(filing_words[b1:b2])
        if op == "replace":
            out.append(f"quote '{q}' vs filing '{f}'")
        elif op == "delete":
            out.append(f"quote adds '{q}'")
        elif op == "insert":
            out.append(f"quote drops '{f}'")
    return out


def check_quote(quote: str, src: str, blocks: list[dict], sentences: list[str]) -> dict:
    q = normalize(quote)
    result = {"verified": False, "ellipsis": "..." in q or "…" in q}
    pos = src.find(q)
    if pos >= 0:
        para, heading = locate(pos, blocks)
        result.update(verified=True, paragraph=para, risk_heading=heading)
    else:
        score, passage, diff = closest_passage(q, sentences)
        result.update(closest_score=round(score, 3), closest_passage=passage, differences=diff)
    return result


# ---------------------------------------------------------------- numbers

NUM = re.compile(
    r"(?<![\w.])(\$\s?)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?"
    r"(\s?%|\s(?:percent|thousand|million|billion|trillion)\b)?", re.I)
SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}


def number_tokens(text: str) -> list[dict]:
    tokens = []
    for m in NUM.finditer(text):
        dollar, whole, dec, unit = m.groups()
        if unit is None and re.match(r"-?[A-Za-z]", text[m.end():m.end() + 2]):
            continue  # part of a label such as "1A", "10-K" or "8-K"
        unit = (unit or "").strip().lower()
        core = whole + (dec or "")
        value = float(core.replace(",", "")) * SCALE.get(unit, 1)
        kind = "percent" if unit in ("%", "percent") else "amount"
        tokens.append({"text": m.group(0).strip(), "core": core, "value": value, "kind": kind})
    return tokens


def _appears(token: str, src: str) -> bool:
    return re.search(r"(?<![\d.,])" + re.escape(token) + r"(?![\d])", src) is not None


def check_number(tok: dict, src: str, src_values: set) -> str:
    if _appears(tok["text"], src):
        return "verified"
    if _appears(tok["core"], src) or (tok["value"], tok["kind"]) in src_values:
        return "needs review"  # same number or value, written differently
    return "not in source"


# ---------------------------------------------------------------- one summary

def run_checks(data: dict, src: str, blocks: list[dict], sentences: list[str]) -> dict:
    structure = check_structure(data)
    src_values = {(t["value"], t["kind"]) for t in number_tokens(src)}
    risks_out = []
    for r in data.get("risks", []):
        quote = check_quote(str(r.get("exact_quote", "")), src, blocks, sentences)
        numbers = []
        for field in CONTENT_FIELDS:
            seen = set()
            for tok in number_tokens(normalize(str(r.get(field, "")))):
                if tok["text"] in seen:
                    continue
                seen.add(tok["text"])
                numbers.append({"field": field, "number": tok["text"],
                                "outcome": check_number(tok, src, src_values)})
        risks_out.append({"rank": r.get("rank"), "title": r.get("title"),
                          "quote": quote, "numbers": numbers})
    return {"structure": structure, "risks": risks_out}


ICON = {"verified": "OK  ", "needs review": "REVIEW", "not in source": "FAIL"}


def report(results: dict) -> dict:
    s = results["structure"]
    log(f"Structure: {'pass' if not s else 'FAIL - ' + '; '.join(s)}")
    for r in results["risks"]:
        q = r["quote"]
        log(f"\nRisk {r['rank']}: {r['title']}")
        if q["verified"]:
            log(f"  Quote: VERIFIED, paragraph {q['paragraph']}, under risk heading:")
            log(f"         \"{(q['risk_heading'] or '(none found)')[:110]}\"")
        else:
            log(f"  Quote: NOT FOUND{' (contains an ellipsis)' if q['ellipsis'] else ''}")
            log(f"         closest passage (similarity {q['closest_score']:.2f}): \"{q['closest_passage'][:200]}\"")
            for d in q["differences"][:6]:
                log(f"         - {d}")
        for n in r["numbers"]:
            log(f"  Number {n['number']!r} in {n['field']}: {n['outcome']}")

    quotes_ok = sum(r["quote"]["verified"] for r in results["risks"])
    distinct = {r["quote"].get("risk_heading") for r in results["risks"] if r["quote"]["verified"]}
    distinct.discard(None)
    if quotes_ok and len(distinct) < quotes_ok:
        log(f"\nNote: the {quotes_ok} verified quotes come from only {len(distinct)} distinct "
            f"risk factors in the filing (two or more risks share a source heading).")
    nums = [n for r in results["risks"] for n in r["numbers"]]
    counts = {k: sum(n["outcome"] == k for n in nums) for k in ICON}
    summary = {"structure_pass": not s, "quotes_verified": quotes_ok,
               "distinct_risk_factors": len(distinct),
               "quotes_total": len(results["risks"]), "numbers_total": len(nums), **counts}
    log(f"\nRESULT: {quotes_ok}/{len(results['risks'])} quotes verified | "
        f"{len(nums)} numbers in AI output: {counts['verified']} verified, "
        f"{counts['needs review']} needs review, {counts['not in source']} not in source | "
        f"structure {'pass' if not s else 'FAIL'}")
    return summary


# ---------------------------------------------------------------- planted errors

SWAPS = [("could", "may"), ("may", "could"), ("will", "would"), ("would", "will"),
         ("adversely", "negatively"), ("significant", "substantial")]


def corrupt_quote(quote: str, src: str) -> tuple[str, str]:
    words = quote.split(" ")
    for a, b in SWAPS:
        for i, w in enumerate(words):
            if w == a:
                new = words[:i] + [b] + words[i + 1:]
                if normalize(" ".join(new)) not in src:
                    return " ".join(new), f"changed '{a}' to '{b}'"
    i = len(words) // 2
    return " ".join(words[:i] + words[i + 1:]), f"removed the word '{words[i]}'"


def change_digit(token: str, src: str) -> str:
    digits = [i for i, c in enumerate(token) if c.isdigit()]
    for pos in reversed(digits):
        for step in range(1, 10):
            new = token[:pos] + str((int(token[pos]) + step) % 10) + token[pos + 1:]
            core = re.sub(r"[^\d.,]", "", new).strip(".,")
            if not _appears(new, src) and not _appears(core, src):
                return new
    raise RuntimeError(f"could not alter {token} into a number absent from the source")


def plant_errors(data: dict, src: str) -> tuple[dict, list[dict]]:
    """Copy the summary and plant one quote error, one changed digit and one
    inserted number (Decision 11)."""
    planted = json.loads(json.dumps(data))
    planted.pop("_meta", None)
    risks = planted["risks"]
    notes = []

    # 1. corrupted quote
    r1 = risks[0]
    r1["exact_quote"], how = corrupt_quote(r1["exact_quote"], src)
    notes.append({"kind": "corrupted quote", "risk": r1["rank"], "detail": how})

    # 2. changed digit: a real number from the output, altered, placed in why_it_matters
    r2 = risks[1 % len(risks)]
    candidates = [t["text"] for r in risks for f in CONTENT_FIELDS
                  for t in number_tokens(normalize(str(r[f]))) if _appears(t["text"], src)]
    if not candidates:  # output has no numbers: borrow one from the filing
        candidates = [t["text"] for t in number_tokens(src) if t["kind"] == "percent" or "$" in t["text"]]
    original = candidates[0]
    changed = change_digit(original, src)
    r2["why_it_matters"] += f" The filing cites {changed} in this context."
    notes.append({"kind": "changed digit", "risk": r2["rank"], "detail": f"{original} -> {changed}",
                  "number": changed})

    # 3. inserted number that is not in the filing
    r3 = risks[2 % len(risks)]
    invented = next(f"{n}%" for n in (37, 43, 58, 71, 89, 94) if not _appears(str(n), src))
    r3["why_it_matters"] += f" This exposure affects roughly {invented} of net revenue."
    notes.append({"kind": "inserted number", "risk": r3["rank"], "detail": invented, "number": invented})
    return planted, notes


def planted_caught(results: dict, notes: list[dict]) -> list[bool]:
    by_rank = {r["rank"]: r for r in results["risks"]}
    caught = []
    for n in notes:
        r = by_rank[n["risk"]]
        if n["kind"] == "corrupted quote":
            caught.append(not r["quote"]["verified"])
        else:
            outcomes = [x["outcome"] for x in r["numbers"] if x["number"] == n["number"]]
            caught.append(bool(outcomes) and outcomes[0] != "verified")
    return caught


# ---------------------------------------------------------------- consistency

def consistency(ticker: str, src: str, blocks: list[dict], sentences: list[str]) -> None:
    """Compare runs by the source risk factors their verified quotes come from.

    A run can spend two slots on one risk factor, so each run's denominator is
    its number of DISTINCT risk factors, not 5. Failed quotes count as unmatched.
    """
    runs = sorted(OUT_DIR.glob(f"summary_{ticker}_run*.json"))
    if len(runs) < 2:
        return
    chosen = {}
    for path in runs:
        data = json.loads(path.read_text(encoding="utf-8"))
        headings = set()
        for r in data.get("risks", []):
            q = check_quote(str(r.get("exact_quote", "")), src, blocks, sentences)
            if q["verified"] and q.get("risk_heading"):
                headings.add(q["risk_heading"])
        chosen[path.stem.split("_")[-1]] = headings

    log(f"\nCONSISTENCY across {len(runs)} runs (compared by source risk factor):")
    for run, hs in chosen.items():
        log(f"  {run}: {len(hs)} distinct risk factors")
    for a, b in combinations(chosen, 2):
        shared = len(chosen[a] & chosen[b])
        log(f"  {a} vs {b}: {shared} shared "
            f"({shared}/{len(chosen[a])} of {a}'s, {shared}/{len(chosen[b])} of {b}'s)")
    union = set().union(*chosen.values())
    log(f"  All runs together drew on {len(union)} distinct risk factors:")
    tally = {h: sum(h in hs for hs in chosen.values()) for h in union}
    for h, n in sorted(tally.items(), key=lambda x: -x[1]):
        log(f"    picked in {n}/{len(runs)} runs: {h[:90]}")


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker")
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--file", type=Path, help="check this summary JSON instead")
    ap.add_argument("--plant", action="store_true", help="plant 3 errors in a copy and check it")
    args = ap.parse_args()

    ticker = args.ticker.upper()
    path = args.file or OUT_DIR / f"summary_{ticker}_run{args.run}.json"
    src, blocks, sentences = load_source(ticker)
    data = json.loads(path.read_text(encoding="utf-8"))
    log(f"=== check.py {ticker} - {dt.datetime.now().astimezone():%Y-%m-%d %H:%M %Z} ===")

    if args.plant:
        planted, notes = plant_errors(data, src)
        planted_path = OUT_DIR / f"planted_{ticker}.json"
        planted_path.write_text(json.dumps(planted, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"Planted 3 errors in a copy of {path.name} -> {planted_path.name}")
        for n in notes:
            log(f"  risk {n['risk']}: {n['kind']} ({n['detail']})")
        log("")
        results = run_checks(planted, src, blocks, sentences)
        report(results)
        caught = planted_caught(results, notes)
        log(f"\nPLANTED ERRORS CAUGHT: {sum(caught)}/{len(caught)}")
        for n, c in zip(notes, caught):
            log(f"  {'caught' if c else 'MISSED'}: {n['kind']} ({n['detail']})")
        ok = all(caught)
    else:
        log(f"Checking {path.name} against section_{ticker}.txt")
        results = run_checks(data, src, blocks, sentences)
        summary = report(results)
        out = OUT_DIR / f"check_{path.stem}.json"
        out.write_text(json.dumps({"summary": summary, **results}, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        consistency(ticker, src, blocks, sentences)
        ok = (summary["structure_pass"] and summary["quotes_verified"] == summary["quotes_total"]
              and summary["not in source"] == 0)

    with (OUT_DIR / "run_output.txt").open("a", encoding="utf-8") as f:
        f.write("\n".join(_log) + "\n\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
