# filing-summarizer

An LLM-powered pipeline that reads the Risk Factors section of a company's 10-K, asks Claude for the top 5 risks with a supporting quote for each, and then **verifies every quote and number against the filing with code**.

## The problem

Equity and credit analysts spend hours reading 10-K risk factors. An LLM can summarize them in seconds, but it can also paraphrase a quote or invent a number. When the output feeds an investment or credit decision, that is unacceptable. This project treats the LLM's output as untrusted until a checker confirms it against the source.

**Success criteria:** every quote and number verified against the filing, and deliberately planted errors caught.

## Results

Two companies (HP Inc. and Dell Technologies), three isolated runs each on `claude-opus-5-5`. Reproduce everything with `python run_all.py`; the full log is in `outputs/run_output.txt`.

| | HPQ | DELL | Total |
|---|---|---|---|
| Quotes verified word for word | 15/15 | 15/15 | **30/30** |
| Numbers in AI output, found in the section | 4/4 | 20/20 | **24/24** |
| ...of which the model wrote itself (outside quotes) | 1 | 5 | **6/6** |
| Planted errors caught | 3/3 | 3/3 | **6/6** |
| Distinct risk factors per run (out of 5 slots) | 5, 5, 5 | 5, 5, 5 | |
| Risk factors shared between run pairs | 4, 5, 4 | 4, 4, 4 | |
| Distinct risk factors across all 3 runs | 6 | 6 | |

A perfect hit rate only means something if the checker can fail. The planted-error test shows it does: one corrupted quote, one changed digit and one invented number per company, all caught.

## Data quality

| Check | HPQ | DELL |
|---|---|---|
| Filing (original 10-K, pinned accession) | FY2025, 0000047217-25-000071 | FY2026, 0001571996-26-000008 |
| "Item 1A" matches (first is the table of contents; last one used) | 2 | 2 |
| Section ends at | Item 1B | Item 1B |
| Section length | 14,559 words | 10,714 words |
| Page numbers / "Table of Contents" lines removed | 30 | 30 |
| Paragraphs split by page breaks, rejoined | 7 | 0 |
| Headings detected (categories + individual risks) | 3 + 31 | 2 + 40 |
| Input tokens per LLM call | ~30,750 | ~23,570 |

The first and last lines of every extracted section are logged on each run, so a wrong extraction is visible before any LLM call is made (`--extract-only`).

## How it works

```
SEC EDGAR ──► summarize.py ──────────────────────────────► check.py
              1. find the pinned 10-K (never a 10-K/A)       1. structure: valid JSON, 5 risks, ranks 1-5
              2. extract Item 1A Risk Factors                2. quotes: exact match after normalization
              3. clean HTML, detect risk headings            3. numbers: every number the model wrote
              4. ask Claude (isolated) for top 5 as JSON     4. map each quote to its risk factor
              5. save summary, section, reply envelope       5. compare runs for consistency
```

**summarize.py**
- Uses the *last* "Item 1A" match and ends at the next Item (1B, 1C or 2).
- Removes page furniture and rejoins paragraphs that a page break split mid-sentence.
- Detects risk headings while parsing the HTML, before formatting is lost.
- Calls Claude through one function, `ask_llm(prompt)`, so swapping the LLM provider changes nothing else.
- **Each call is isolated:** no tools (`--tools ""`), no MCP servers (`--strict-mcp-config`), no session persistence, an empty temporary working directory, and a pinned model. The full reply envelope is saved per run as evidence (model used, 1 turn, token usage).

**check.py**
- **Quotes** must appear exactly, case-sensitive, after both sides go through the same `normalize()` (non-breaking spaces, soft hyphens, curly quotes, dashes, whitespace). On failure it shows the closest passage and the exact word differences.
- **Numbers** are extracted from every content field, including the model's own explanation, where invented numbers are most likely. Outcomes: *verified*, *needs review* (same value written differently, e.g. "65%" vs. "65 percent") or *not in source*.
- **Consistency** compares runs by the source risk factor each verified quote falls under, with each run's number of distinct risk factors as the denominator.
- **Planted errors** (`--plant`) are written to a separate copy so test results never mix with real ones.

## Key findings

**Exact matching is the right rule.** Deleting one word from a 35-word HP quote gave a similarity of **0.99**; swapping "may" for "could" in a Dell quote gave **0.98**. Any fuzzy threshold, even 95%, would have marked both as verified. Paraphrase is exactly what this project exists to catch.

**The top 3 is stable; variation sits at ranks 4-5.** For both companies, every run picked the same three risk factors for ranks 1-3. HP: print supplies decline, tariffs, memory and component costs. Dell: AI demand concentration, vendor dependence, debt. All variation between runs is in the last two slots (for Dell, macroeconomic conditions vs. PC business execution vs. the multi-class share structure).

**Isolation changed the inputs.** A first version called Claude Code without isolation: its tools were active (in principle the model could have read earlier runs' output files) and Claude Code's own setup inflated each call to ~47k input tokens. After the fix, HP calls use ~31k. All results above come from the isolated version; earlier runs were discarded.

**Heading levels need structure, not styling.** HP styles category headings bold and individual risks bold-italic; Dell bolds everything the same way. The rule that works for both: a category heading is followed directly by another heading, and an individual risk heading is followed by body text.

**The model's own numbers were right in context.** check.py confirms only that a number appears in the section. I checked the 6 numbers the model wrote in its own words by hand: HP's 65% and Dell's 45% (revenue from outside the US) and Dell's $31.5 billion debt, $5.0 billion commercial paper and $5.9 billion revolver capacity are all used with the meaning the filing gives them.

## Limitations

- **Verified against the extracted section, not the whole filing.** A number or quote from another part of the 10-K would be reported as "not in source".
- **Numbers are checked without context.** The checker cannot tell whether a number is attached to the right metric; the context check above was manual.
- **Reasoning is not verified.** Interpretations in `why_it_matters` (e.g. Dell's "loose covenants") are the model's judgment. Only quotes and numbers are checked.
- **Numbers written as words** ("five") are not extracted.
- **Small sample:** two filings, six runs, one model. Enough to show the method works, not to measure an error rate or compare models.
- **Ranking is not evaluated.** Whether these are truly the 5 most important risks is a judgment call the checker cannot make.

## What AI did vs. what I did

<!-- EDIT THIS SECTION IN YOUR OWN WORDS BEFORE PUBLISHING. Draft based on how the project was built. -->

**I** defined the project: the problem, the target users, the success criteria, the data quality checks and the list of decisions to make. I chose the companies, made or approved every design decision recorded in [DECISIONS.md](DECISIONS.md), ran the pipeline on my machine, and reviewed each stage critically. That review caught real problems, most importantly that the first LLM calls weren't isolated and the consistency metric used the wrong denominator, which led to rerunning all results.

**Claude (Anthropic's AI assistant)** wrote the code and tests to my specifications, proposed technical approaches for me to accept or reject, and helped draft this documentation.

## Run it

Requirements: Python 3.10+, and [Claude Code](https://claude.com/claude-code) installed and logged in.

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v       # 19 tests, no network needed

export SEC_USER_AGENT="Your Name your.email@example.com"   # required by SEC EDGAR
python summarize.py HPQ --extract-only        # check the extraction first (no LLM call)
python run_all.py --pause 20                  # everything: 6 LLM runs, all checks, planted errors
```

Or step by step: `python summarize.py HPQ --run 1`, then `python check.py HPQ --run 1` and `python check.py HPQ --plant`.

**No API key.** The LLM call goes through Claude Code's non-interactive mode (`claude -p --output-format json`, prompt sent via stdin) using a Claude Pro login. The script refuses to run if `ANTHROPIC_API_KEY` is set, since that would bill the API instead. Filings are pinned by accession number and the model by ID, so reruns read the same document with the same model.

## Project structure

```
filing-summarizer/
├── summarize.py          fetch → extract Item 1A → ask the LLM (isolated) → save
├── check.py              verify quotes and numbers, planted errors, consistency
├── run_all.py            reproduce all results into one fresh log
├── textnorm.py           the shared normalize() used by both scripts
├── tests/                unit tests and a fake 10-K containing the known traps
├── outputs/              sections, summaries, reply envelopes, check results, run_output.txt
├── DECISIONS.md          every design decision and the reasoning behind it
└── requirements.txt      requests==2.34.2, beautifulsoup4==4.15.0
```

## Skills demonstrated

LLM API integration · structured JSON output · prompt constraints · isolating LLM calls for reproducibility · keeping secrets out of code · verifying AI output with code · test-driven data cleaning (SEC EDGAR HTML)
