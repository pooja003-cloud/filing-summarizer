# Decisions — filing-summarizer 

A log of design choices and the reasoning behind them. Each entry records what was decided, why, and what was rejected.

---

## Summary

| # | Topic | Decision |
|---|-------|----------|
| 1 | Section | Item 1A Risk Factors (not MD&A) |
| 2 | Companies | HP Inc. (HPQ) primary, Dell (DELL) second |
| 3 | Filing | Latest original 10-K (no 10-K/A), accession number pinned |
| 4 | Extraction | Last "Item 1A" match, end at next Item heading |
| 5 | LLM call | `claude -p --output-format json`, text via stdin, wrapped in `ask_llm()` |
| 6 | Chunking | One call per filing, no chunking |
| 7 | Output | JSON: 5 risks, each with title, why_it_matters, exact_quote |
| 8 | Normalization | One shared `normalize()` for filing and output, with tests |
| 9 | Quote matching | Exact, case-sensitive, after normalization; closest passage shown on failure |
| 10 | Number checking | Numbers from content fields only; three outcomes |
| 11 | Planted errors | Three: corrupted quote, changed digit, inserted number |
| 12 | Consistency | 3 runs × 2 companies, compared by source risk heading |

---

## 1. Section: Risk Factors, not MD&A

**Decision:** Summarize Item 1A Risk Factors.

**Why:** This project is mainly about quote verification, catching a model that paraphrases or invents text. Risk Factors is long, quotable prose, which tests that well. MD&A is number-heavy, and number checking gets proper treatment in Projects 3–4.

**Trade-off:** Risk Factors contains few numbers, so the number check may have little to work with. Handled by checking every number the *model writes* (Decision 10) and by planting number errors (Decision 11). The write-up reports the count honestly, e.g. "N numbers in AI output, N verified against filing." If there are none, it says so.

## 2. Companies: HPQ + DELL

**Decision:** HP Inc. (HPQ) as the primary company, Dell Technologies (DELL) as the second test filing.

**Why:**
- Both use a standard 10-K layout with a clear "Item 1A" heading.
- Same industry (PCs, printing/infrastructure), reusable as peers in Project 4.
- Different fiscal calendars (HP: Oct 31, Dell: around early February) and different HTML formatting, so two filings show the extractor isn't tuned to one document.

**Rejected:**
- **Acer:** current employer. Analyzing it in a public portfolio risks a policy conflict and the appearance of using inside knowledge. It also reports under IFRS in PDF. Not used in any public project.
- **Banks, GE:** cross-reference-index layouts break the "Item 1A" extraction.
- **Logitech:** fine, but two companies are enough for Project 0.
- **Super Micro (SMCI):** recent late filings and accounting issues. A good stress test later, a bad first target.

## 3. Filing selection and pinning

**Decision:** Use the latest *original* 10-K (form type `10-K`, not `10-K/A`). Pin its accession number in config and log it with every run.

**Why:** Amendments are often partial (e.g. Part III only) and can lack Item 1A. Pinning makes results reproducible: HP's FY2026 10-K should be filed around mid-December 2026, after which "latest" resolves to a different document.

**Pinned filings:**

| Ticker | Fiscal year | Accession number | Filed |
|--------|-------------|------------------|-------|
| HPQ | FY2025 (ended 2025-10-31) | 0000047217-25-000071 | 2025-12-10 |
| DELL | FY2026 (ended 2026-01-30) | 0001571996-26-000008 | 2026-03-16 |

## 4. Section extraction

**Decision:** Find the *last* match of "Item 1A" (case-insensitive, tolerant of spacing and punctuation) and end at the next Item heading (usually "Item 1B", otherwise "Item 1C" or "Item 2").

**Why:** The first match is usually the table of contents. Since 2023 many filings include Item 1C (Cybersecurity), so the end marker can't assume Item 2.

**Data quality checks logged on every run:**
- Section length (characters and words). A very short result means the TOC was caught.
- First and last lines of the extracted section.
- Form type confirmed as `10-K`.
- Page furniture removed: page numbers and "Table of Contents" links repeated at every page break (HPQ: 30 lines).
- Paragraphs split mid-sentence by a page break are rejoined (HPQ: 7). A block that starts in lowercase, after a block that doesn't end a sentence, continues that paragraph.
- HTML artifacts cleaned (see Decision 8).

## 5. How the LLM is called

**Decision:** Call Claude through Claude Code's non-interactive mode: `claude -p --output-format json`, with the prompt and filing text sent via **stdin**. All of this lives in one function, `ask_llm(prompt)`.

**Why:**
- Claude Pro doesn't include API credits. This uses the Pro login at no extra cost and shares Pro usage limits.
- **Stdin vs. command-line argument:** a Risk Factors section is tens of thousands of characters. Passing it as an argument risks hitting OS argument-length limits and shell-quoting bugs. Stdin has neither problem.
- Isolating the call in `ask_llm()` means switching to the paid API later changes only that function.

**Rules:**
- Never set `ANTHROPIC_API_KEY`. It would bill the API instead of using Pro.
- No `.env.example` is needed for Project 0.

## 6. One call, no chunking

**Decision:** Send the whole Risk Factors section in a single call.

**Why:** The section fits comfortably in the model's context window. Chunking would require merging "top 5" lists across chunks, which adds complexity and a new source of error. Revisit only if a filing's section turns out too long.

## 7. Output format and prompt rules

**Decision:** The model returns JSON:

```json
{
  "risks": [
    {
      "rank": 1,
      "title": "...",
      "why_it_matters": "...",
      "exact_quote": "..."
    }
  ]
}
```

**Prompt rules:**
- Exactly 5 risks.
- `exact_quote` must be copied word for word from the filing.
- No ellipses ("…" or "...") to cut words out of a quote.
- Keep the filing's original capitalization, even when a quote starts mid-sentence.
- Copy numbers exactly as written in the filing (same format and units).

**Structure check:** valid JSON, exactly 5 risks, all fields present and non-empty. Pass/fail.

## 8. Text normalization

**Decision:** One `normalize()` function applied to both the filing text and every quote, with a small unit test.

**What it does:**
- Non-breaking spaces (U+00A0) → regular spaces.
- Remove soft hyphens (U+00AD) and zero-width spaces (U+200B).
- Curly quotes ‘ ’ → `'` and “ ” → `"`.
- En and em dashes (– —) → `-`.
- Collapse all runs of whitespace to a single space.

**Why one function:** if the filing and the quotes are cleaned differently, mismatches look like model errors when they're really pipeline bugs. The test locks this down.

**Not normalized:** case. Changing capitalization changes the quote (Decision 9).

## 9. Quote matching

**Decision:** A quote is verified only if it appears **exactly** in the filing after normalization. Matching is **case-sensitive**.

**Why not fuzzy matching:** fuzzy matching accepts paraphrases, and paraphrases are exactly what this project catches. A 90% similarity threshold would let "revenue may decline" pass for "revenue could decline significantly."

**Diagnostic on failure:** for each failed quote, show the closest passage in the filing and its similarity score. The quote still fails, but the diagnostic shows *how* the model went wrong (small wording change vs. invented text).
- Method: split the filing into sentences, compare the quote against groups of 1–3 consecutive sentences with Python's built-in `difflib`. Fast, no extra dependency, readable output.

## 10. Number checking

**Decision:** Extract every number from the model's **content fields** (`title`, `why_it_matters`, `exact_quote`) and check each one against the filing. Never extract from `rank` or JSON keys.

**Why content fields only:** this avoids false alarms from our own structure ("Risk 1", "top 5") without a fragile ignore list. `why_it_matters` matters most, since it's the model's own wording and invented numbers are most likely there.

**Years are checked, not ignored.** A model that writes "since 2019" when the filing says 2020 must be caught. Only numbers our own pipeline adds (e.g. a fiscal-year label in a header) are excluded, and those never appear in content fields.

**Three outcomes:**

| Outcome | Meaning |
|---------|---------|
| ✅ Verified | Number appears in the filing as written |
| 🟡 Needs review | Same value likely written differently ("$1.2 billion" vs. "$1,200 million", "10%" vs. "10 percent") |
| ❌ Not in source | Number not found in the filing in any form |

**Not in Project 0:** parsing numbers into values to auto-verify unit differences. That belongs to Projects 3–4.

## 11. Planted errors

**Decision:** Plant three errors in a **copy** of a real output (`outputs/planted_hpq.json`) and run check.py on it:
1. A corrupted quote (one word changed).
2. A changed digit in an existing number.
3. An inserted number that doesn't appear in the filing.

**Why:** proves the checker catches each kind of mistake, even if the real run happens to contain none. Using a separate file keeps real results and test results apart.

**Target:** 3/3 caught.

## 12. Consistency testing

**Decision:** 3 runs per company × 2 companies = 6 LLM calls. Spread the runs out if Pro usage limits are hit.

**Metric:** how much the top 5 overlaps between runs, compared by **source location**, not by the model's titles (which are worded differently every run).
- Each verified quote has an exact position in the filing. Map it to the **individual risk heading** it falls under (not the category heading like "Risks Related to…", which would make almost any two runs look consistent).
- Report e.g. "runs 1 and 2 picked the same 4 of 5 risk factors."
- A quote that fails verification has no location, so its risk counts as **unmatched**.

**Heading detection:** headings become indistinguishable from body text once HTML is flattened, so detect them while parsing:
- A heading is a paragraph whose **entire** text is bold or italic (not partial emphasis).
- Check `<b>`, `<strong>`, `<i>`, `<em>` tags **and** inline styles (`font-weight: bold` / `700`, `font-style: italic`).
- Tell the two levels apart by **structure, not styling**: a category heading is followed directly by another heading; an individual risk heading is followed by body text. Styling alone failed on Dell, where every heading is plain bold (HP uses bold for categories, bold-italic for risks). The structural rule gives HPQ 3 categories / 31 risks and DELL 2 categories / 40 risks, both correct.
- Test on both HP and Dell, since their formatting differs.

**Fallback:** if heading detection is unreliable, map quotes to paragraph numbers. Less readable, but still objective, and valid because every run reads the same pinned filing.

---

## Operational rules

- **SEC EDGAR:** every request sends a `User-Agent` header with name and email. Maximum 10 requests per second.
- **Dependencies:** `requests`, `beautifulsoup4`. Everything else is standard library.
- **Usage:** 6 consistency calls plus development runs, all on Pro limits. Token counts are rough estimates since Pro limits aren't published in tokens.

## Report format (run_output.txt / README)

- Filing: ticker, fiscal year, accession number, section length, first/last lines.
- Structure: pass/fail.
- Quotes: N/5 verified, plus closest-passage diagnostics for failures.
- Numbers: N in AI output, with counts for verified / needs review / not in source.
- Planted errors: N/3 caught.
- Consistency: overlap of top 5 across runs, per company.
