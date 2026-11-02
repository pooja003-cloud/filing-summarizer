# filing-summarizer: details

The detail behind the [README](../README.md). Every design decision and its reasoning is in [DECISIONS.md](../DECISIONS.md).

## What check.py verifies
| Check | Rule |
|---|---|
| Structure | Valid JSON, exactly 5 risks, all fields present, ranks 1-5 |
| Quotes | **Exact, case-sensitive** match after normalization. On failure: the closest passage and the exact word differences |
| Numbers | Every number in every field, including the model's own explanation. Outcomes: *verified*, *needs review* (same value written differently), *not in source* |
| Location | Each verified quote mapped to its individual risk factor in the filing |
| Consistency | Runs compared by source risk factor, with each run's distinct risk factors as the denominator |
| Planted errors | A copy of the output gets a corrupted quote, a changed digit and an invented number; all three must be caught |

## Full results

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

## Data quality

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

## Related work

- **Why summaries help:** Kim, Muhn and Nikolaev, [*Bloated Disclosures*](https://arxiv.org/html/2306.10224v1) (2023). GPT summaries of MD&As and earnings calls were less than half the original length, and their sentiment explained stock price reactions better than the full documents. The summaries' accuracy was not verified.
- **Why they can't be trusted unchecked:** Islam et al., [*FinanceBench*](https://www.alphaxiv.org/abs/2311.11944v1) (2023). GPT-4-Turbo with retrieval answered incorrectly or refused 81% of sampled questions about public-company filings. Grieser et al., [*LLMs and Systematic Measurement Error*](https://www.cxoadvisory.com/investing-expertise/distortion-bias-in-llm-summaries-of-text/) (2026), study what LLMs omit from and inject into summaries of 10-K Item 1A risk disclosures, the same section used here.
- **Closest pipeline:** Dolphin et al., [*Taxonomy-Aligned Risk Extraction from 10-K Filings*](https://arxiv.org/pdf/2601.15247) (2026), extract Item 1A risks with a supporting quote each, using an LLM judge to rate whether a quote fits its category. They don't check that quotes appear word for word in the filing, which is the gap this project addresses at small scale.
- **Detecting hallucinations with models instead of code:** Kumar, Sarmah and Pasquali, [*FinReflectKG HalluBench*](https://arxiv.org/abs/2603.20252) (2026). The best detectors for 10-K question answering reach F1 of 0.82-0.86 but degrade sharply on noisy input. Exact string matching has no such failure mode for quotes, though it can't judge meaning.
