# filing-summarizer

An LLM-powered pipeline that reads the Risk Factors section of a company's 10-K, asks Claude for the top 5 risks with a supporting quote for each, and then **verifies every quote and number against the filing with code**.

## The problem

Equity and credit analysts spend hours reading 10-K risk factors. An LLM can summarize them in seconds, but it can also paraphrase a quote or invent a number. When the output feeds an investment or credit decision, that is unacceptable. This project treats the LLM's output as untrusted until a checker confirms it against the source.

**Success criteria:** every quote and number verified against the filing, and deliberately planted errors caught.

## Results

Two companies (HP Inc. and Dell Technologies), three runs each, six LLM calls in total.

| | HPQ | DELL | Total |
|---|---|---|---|
| Quotes verified word for word | 15/15 | 15/15 | **30/30** |
| Numbers in AI output, found in filing | 4/4 | 20/20 | **24/24** |
| ...of which written by the model itself (outside quotes) | 1 | 4 | **5/5** |
| Planted errors caught | 3/3 | 3/3 | **6/6** |
| Same risk factors picked between runs | 4, 4, 5 of 5 | 5, 4, 4 of 5 | |

A perfect hit rate only means something if the checker can fail. The planted-error test shows it does: one corrupted quote, one changed digit and one invented number per company, all caught.

## How it works

```
SEC EDGAR ──► summarize.py ──────────────────────────────► check.py
              1. find the pinned 10-K (never a 10-K/A)       1. structure: valid JSON, 5 risks, ranks 1-5
              2. extract Item 1A Risk Factors                2. quotes: exact match after normalization
              3. clean HTML, detect risk headings            3. numbers: every number the model wrote
              4. ask Claude for top 5 risks as JSON          4. map each quote to its risk factor
              5. save summary + section text                 5. compare runs for consistency
```

**summarize.py**
- Uses the *last* "Item 1A" match, since the first is usually the table of contents, and ends at the next Item (1B, 1C or 2).
- Removes page furniture (page numbers and "Table of Contents" links repeated at every page break) and rejoins paragraphs that a page break split mid-sentence.
- Detects risk headings while parsing the HTML, before formatting is lost.
- Calls Claude through one function, `ask_llm(prompt)`, so swapping the LLM provider changes nothing else.

**check.py**
- **Quotes** must appear exactly, case-sensitive, after both sides go through the same `normalize()` (non-breaking spaces, soft hyphens, curly quotes, dashes, whitespace). On failure it shows the closest passage in the filing and the exact word differences.
- **Numbers** are extracted from every content field, including the model's own explanation, where invented numbers are most likely. Each gets one of three outcomes: *verified*, *needs review* (same value written differently, e.g. "65%" vs. "65 percent") or *not in source*.
- **Planted errors** (`--plant`) are written to a separate copy so test results never mix with real ones.

## Key findings

**Exact matching is the right rule, and the numbers prove it.** Deleting one word from a 35-word HP quote gave a similarity of **0.99**. Swapping "may" for "could" in a Dell quote gave **0.98**. Any fuzzy threshold, even 95%, would have marked both as verified. Paraphrase is exactly what this project exists to catch.

**Heading levels need structure, not styling.** HP styles category headings bold and individual risks bold-italic. Dell bolds everything the same way. The rule that works for both: a category heading is followed directly by another heading, and an individual risk heading is followed by body text. (HP: 3 categories, 31 risks. Dell: 2 categories, 40 risks.)

**Consistency is high, and the variation is between near-duplicates.** Four risk factors were picked in all three runs for each company. The fifth slot moved between closely related factors; for Dell, between "multi-class stock structure" and "controlled by the MD stockholders", two adjacent factors on the same governance risk.

**The model sometimes spends two slots on one risk.** In HP run 1, the tariffs risk and the Taiwan supply-chain risk quote the same risk factor, so the top 5 covers only 4. check.py flags this automatically.

## Limitations

- **Numbers are checked without context.** "Verified" means the number appears in the section, not that it is attached to the right metric.
- **Reasoning is not verified.** Claims in `why_it_matters` (e.g. effects on "debt-servicing capacity") are the model's interpretation. Only quotes and numbers are checked.
- **Numbers written as words** ("five") are not extracted.
- **Two filings, six runs.** Enough to show the method works, not enough to measure an error rate.
- **Ranking is not evaluated.** Whether these are truly the 5 most important risks is a judgment call the checker cannot make.

## Run it

Requirements: Python 3.10+, and [Claude Code](https://claude.com/claude-code) installed and logged in.

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v       # 19 tests, no network needed

export SEC_USER_AGENT="Your Name your.email@example.com"   # required by SEC EDGAR
python summarize.py HPQ --extract-only        # check the extraction first (no LLM call)
python summarize.py HPQ                       # run 1; add --run 2, --run 3 for consistency
python check.py HPQ                           # verify run 1
python check.py HPQ --run 3                   # also prints consistency across all runs
python check.py HPQ --plant                   # planted-error test
```

**No API key.** The LLM call goes through Claude Code's non-interactive mode (`claude -p --output-format json`, prompt sent via stdin) using a Claude Pro login. The script refuses to run if `ANTHROPIC_API_KEY` is set, since that would bill the API instead. Filings are pinned by accession number, so reruns read the same document.

## Project structure

```
filing-summarizer/
├── summarize.py          fetch → extract Item 1A → ask the LLM → save
├── check.py              verify quotes and numbers, planted errors, consistency
├── textnorm.py           the shared normalize() used by both scripts
├── tests/                unit tests and a fake 10-K containing the known traps
├── outputs/              sections, summaries, check results, run_output.txt
├── DECISIONS.md          every design decision and the reasoning behind it
└── requirements.txt      requests, beautifulsoup4
```

## Skills demonstrated

LLM API integration · structured JSON output · prompt constraints · keeping secrets out of code · verifying AI output with code · test-driven data cleaning (SEC EDGAR HTML)
