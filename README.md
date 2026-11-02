# filing-summarizer

An LLM-powered pipeline that summarizes the top 5 risks in a company's 10-K, then **verifies every quote and number against the SEC filing with code**, treating the AI's output as untrusted until it is proven.

---

## Situation

Equity and credit analysts spend hours reading the Risk Factors section of 10-K filings, often 10,000-15,000 words per company. An LLM can summarize that in seconds, but it can also paraphrase a quote or invent a number. When the summary feeds an investment or credit decision, a single fabricated figure makes the whole output unusable, and a quote that *looks* right is the hardest error to spot by eye.

## Task

Build a small pipeline that pulls Item 1A (Risk Factors) from a real 10-K on SEC EDGAR, asks an LLM for the 5 most important risks as structured JSON with a supporting quote for each, and **proves** the output is faithful to the filing.

Success criteria, set before building:
- Every quote appears **word for word** in the filing.
- Every number the AI writes, including in its own explanations, exists in the filing.
- Deliberately planted errors are caught, so a perfect score means something.
- Results are reproducible: pinned filings, pinned model, one command to rerun everything.

## Action

### 1. Extracted the right text, and checked it
- Fetched each company's latest **original** 10-K from SEC EDGAR (never a 10-K/A amendment) and pinned it by accession number.
- Avoided the table-of-contents trap by using the *last* "Item 1A" match, ending at the next Item (1B, 1C or 2).
- Cleaned EDGAR's HTML: removed page numbers and "Table of Contents" links repeated at every page break, rejoined paragraphs split mid-sentence, and normalized invisible characters, curly quotes and dashes with one shared `normalize()` function.
- Detected risk headings while parsing the HTML, before formatting is lost, so each quote can be traced to the risk factor it came from.

### 2. Called the LLM in isolation
- One function, `ask_llm(prompt)`, calls `claude-opus-5-5` through Claude Code's non-interactive mode (`claude -p`, prompt via stdin) on a Pro login, with no API key and no extra cost. Swapping providers later changes only that function.
- Each call is isolated: no tools, no MCP servers, no session persistence, an empty working directory. The full reply envelope (model used, 1 turn, token usage) is saved as evidence.
- The prompt requires quotes copied word for word, no ellipses, original capitalization, and numbers copied exactly as written.

### 3. Verified the output with code
| Check | Rule |
|---|---|
| Structure | Valid JSON, exactly 5 risks, all fields present, ranks 1-5 |
| Quotes | **Exact, case-sensitive** match after normalization. On failure: the closest passage and the exact word differences |
| Numbers | Every number in every field, including the model's own explanation. Outcomes: *verified*, *needs review* (same value written differently), *not in source* |
| Location | Each verified quote mapped to its individual risk factor in the filing |
| Consistency | Runs compared by source risk factor, with each run's distinct risk factors as the denominator |
| Planted errors | A copy of the output gets a corrupted quote, a changed digit and an invented number; all three must be caught |

### 4. Key decisions
Every decision and its reasoning is logged in [DECISIONS.md](DECISIONS.md). The ones that shaped the project:
- **Exact over fuzzy matching.** Fuzzy matching accepts paraphrase, which is exactly what this project exists to catch.
- **Risk Factors over MD&A.** The project tests quote fidelity; Risk Factors is long, quotable prose.
- **Two companies.** HP and Dell format their filings differently, which proves the extractor isn't tuned to one document.
- **Heading levels by structure, not styling.** HP styles category headings differently from individual risks; Dell bolds everything identically. The rule that works for both: a category heading is followed directly by another heading.
- **Opus over Sonnet.** Copying quotes is easy for either model; the hard part is judging which 5 of 31-40 risk factors matter most.

### 5. Corrected course when the method was wrong
A review of the first results found two flaws: the first LLM calls weren't isolated (Claude Code's tools were active, so in principle a run could have read earlier outputs), and the consistency metric used 5 as the denominator even when a run used two slots on one risk factor. Both were fixed, and **every result was rerun** rather than kept.

## Result

Two companies, three isolated runs each, 6 LLM calls. Reproduce with `python run_all.py`; full log in `outputs/run_output.txt`.

| | HPQ | DELL | Total |
|---|---|---|---|
| Quotes verified word for word | 15/15 | 15/15 | **30/30** |
| Numbers in AI output, found in the section | 4/4 | 20/20 | **24/24** |
| ...of which the model wrote itself (outside quotes) | 1 | 5 | **6/6** |
| Planted errors caught | 3/3 | 3/3 | **6/6** |
| Distinct risk factors per run (out of 5 slots) | 5, 5, 5 | 5, 5, 5 | |
| Risk factors shared between run pairs | 4, 5, 4 | 4, 4, 4 | |
| Distinct risk factors across all 3 runs | 6 | 6 | |

**What the results show**
- **Exact matching was necessary.** Deleting one word from a 35-word quote scored **0.99** similarity; swapping "may" for "could" scored **0.98**. Any fuzzy threshold, even 95%, would have passed both planted errors.
- **The top 3 is stable.** For both companies, every run picked the same three risk factors for ranks 1-3. HP: print supplies decline, tariffs, memory and component costs. Dell: AI demand concentration, vendor dependence, debt. All variation between runs sits at ranks 4-5.
- **The model's own numbers were right in context.** check.py only confirms a number appears in the section, so I checked the 6 numbers the model wrote in its own words by hand: HP's 65% and Dell's 45% (revenue from outside the US) and Dell's $31.5 billion debt, $5.0 billion commercial paper and $5.9 billion revolver capacity all carry the meaning the filing gives them.
- **Isolation mattered.** Before the fix, Claude Code's own setup inflated each HP call to ~47k input tokens; isolated calls use ~31k.

**Data quality**

| Check | HPQ | DELL |
|---|---|---|
| Filing (original 10-K, pinned accession) | FY2025, 0000047217-25-000071 | FY2026, 0001571996-26-000008 |
| "Item 1A" matches (first = table of contents; last used) | 2 | 2 |
| Section ends at | Item 1B | Item 1B |
| Section length | 14,559 words | 10,714 words |
| Page numbers / "Table of Contents" lines removed | 30 | 30 |
| Paragraphs split by page breaks, rejoined | 7 | 0 |
| Headings (categories + individual risks) | 3 + 31 | 2 + 40 |
| Input tokens per LLM call | ~30,750 | ~23,570 |

## Limitations

- **Verified against the extracted section, not the whole filing.** A number or quote from elsewhere in the 10-K would show as "not in source".
- **Numbers are checked without context.** The checker can't tell whether a number is attached to the right metric; the context check above was manual.
- **Reasoning is not verified.** Interpretations in `why_it_matters` (e.g. Dell's "loose covenants") are the model's judgment. Only quotes and numbers are checked.
- **Numbers written as words** ("five") are not extracted.
- **Small sample:** two filings, six runs, one model. Enough to show the method works, not to measure an error rate or compare models.
- **Ranking is not evaluated.** Whether these are truly the 5 most important risks is a judgment the checker cannot make.

## What AI did vs. what I did

**What I did**
- **Set the requirement:** an AI risk summary is unusable for an investment or credit decision unless every quote and number can be traced back to the SEC filing.
- **Made the scope decisions:** chose HP and Dell as the test cases, picked Item 1A over MD&A, required exact rather than fuzzy matching, and chose Claude Opus over Sonnet for the hard part, ranking which risks matter most.
- **Challenged the plan before building:** I brought in outside feedback and kept what held up, like testing a second company and extending my planted-error test to number errors, while rejecting suggestions that didn't fit.
- **Checked the results instead of trusting them:** I ran the pipeline, read the outputs and audited the methodology. I caught that my early LLM calls weren't isolated and that the consistency metric used the wrong denominator. I fixed both and reran every result rather than keep numbers I couldn't defend.

**What Claude did**
- Proposed much of the technical design, including the verification approach and the normalization rules, which I then vetted, changed or rejected.
- Wrote the code and 19 unit tests, including a synthetic 10-K packed with known traps to see whether the pipeline would break.
- Reviewed the outputs and drafted the initial documentation, which I then edited into its final version.

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

The script refuses to run if `ANTHROPIC_API_KEY` is set, since that would bill the API instead of the Pro plan.

## Project structure

```
filing-summarizer/
├── summarize.py          fetch → extract Item 1A → ask the LLM (isolated) → save
├── check.py              verify quotes and numbers, planted errors, consistency
├── run_all.py            reproduce all results into one fresh log
├── textnorm.py           the shared normalize() used by both scripts
├── tests/                19 unit tests and a fake 10-K containing the known traps
├── outputs/              sections, summaries, reply envelopes, check results, run_output.txt
├── DECISIONS.md          every design decision and the reasoning behind it
└── requirements.txt      requests==2.34.2, beautifulsoup4==4.15.0
```

**Skills:** LLM integration · structured JSON output · prompt constraints · isolating LLM calls for reproducibility · keeping secrets out of code · verifying AI output with code · test-driven data cleaning (SEC EDGAR HTML)
