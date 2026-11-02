"""Tests for normalization and section extraction. Run: python -m unittest -v"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from summarize import extract_risk_factors, html_to_blocks, merge_page_splits, parse_model_json  # noqa: E402
from textnorm import normalize  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_invisible_characters_removed(self):
        self.assertEqual(normalize("per­sonal zero​width"), "personal zerowidth")

    def test_spaces_and_whitespace(self):
        self.assertEqual(normalize("$1.2 billion\n\n  and\tmore "), "$1.2 billion and more")

    def test_quotes_and_dashes(self):
        self.assertEqual(normalize("“it’s” ‘x’ a—b c–d"),
                         "\"it's\" 'x' a-b c-d")

    def test_case_is_kept(self):
        self.assertEqual(normalize("Could Adversely"), "Could Adversely")

    def test_same_result_for_filing_and_quote(self):
        # The whole point of one shared function: both sides clean the same way.
        filing = "results—including our “ability”"
        quote = 'results-including our "ability"'
        self.assertIn(normalize(quote), normalize(filing))

    def test_idempotent(self):
        s = "a “b”—c"
        self.assertEqual(normalize(normalize(s)), normalize(s))


class TestExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = (HERE / "fixture_10k.htm").read_text(encoding="utf-8")
        cls.blocks = html_to_blocks(html)
        cls.section, cls.info = extract_risk_factors(cls.blocks)
        cls.texts = [b["text"] for b in cls.section]

    def test_skips_table_of_contents_and_hidden_xbrl(self):
        self.assertEqual(self.info["item_1a_matches"], 2)  # TOC + real heading
        self.assertNotIn("HIDDEN", " ".join(b["text"] for b in self.blocks))

    def test_section_boundaries(self):
        self.assertEqual(self.texts[0], "Risks Related to Our Business Operations")
        self.assertEqual(self.texts[-1], "Our company's systems are attacked regularly.")
        self.assertTrue(self.info["end_heading"].startswith("ITEM 1B"))

    def test_page_furniture_dropped(self):
        self.assertNotIn("14", self.texts)
        self.assertNotIn("Table of Contents", self.texts)
        self.assertEqual(self.info["page_noise_blocks_dropped"], 2)

    def test_text_cleaned(self):
        self.assertIn("Our revenue depends on the personal computer market.", self.texts)
        self.assertIn('Supply disruptions could "materially and adversely" affect our '
                      "results-including our ability to ship products on time. "
                      "We had $1.2 billion of purchase commitments.", self.texts)
        self.assertIn("A second line after a break, with a zerowidth space.", self.texts)

    def test_heading_detection(self):
        styles = {b["text"]: b["heading"] for b in self.section}
        self.assertEqual(styles["Risks Related to Our Business Operations"], "bold+italic")
        self.assertEqual(styles["Our revenue depends on the personal computer market."], "bold")
        self.assertEqual(styles["We rely on third-party suppliers, some of whom are sole-source."], "bold")
        # Partial emphasis ("cyclical" in bold) is body text, not a heading.
        body = next(t for t in styles if t.startswith("Demand for PCs"))
        self.assertIsNone(styles[body])

    def test_offsets_match_normalized_text(self):
        joined = normalize("\n\n".join(self.texts))
        for b in self.section:
            self.assertEqual(joined[b["start"]:b["start"] + len(b["text"])], b["text"])


class TestMergePageSplits(unittest.TestCase):
    def test_rejoins_only_mid_sentence_splits(self):
        blocks = [
            {"text": "Heading.", "heading": "bold+italic"},
            {"text": "The loss of our relationship", "heading": None},
            {"text": "with a supplier could hurt us.", "heading": None},
            {"text": "a new paragraph after a full stop.", "heading": None},
        ]
        merged, n = merge_page_splits(blocks)
        self.assertEqual(n, 1)
        self.assertEqual(merged[1]["text"], "The loss of our relationship with a supplier could hurt us.")
        self.assertEqual(len(merged), 3)  # full stop before: not merged


class TestParseModelJson(unittest.TestCase):
    def test_plain_and_fenced(self):
        self.assertEqual(parse_model_json('{"risks": []}'), {"risks": []})
        self.assertEqual(parse_model_json('```json\n{"risks": []}\n```'), {"risks": []})


if __name__ == "__main__":
    unittest.main()
