"""Tests for check.py's number and quote logic. Run: python -m unittest discover -s tests -v"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check import check_number, closest_passage, number_tokens  # noqa: E402

SRC = ("In fiscal 2025, approximately 65 percent of net revenue came from Personal Systems. "
       "We had $1.2 billion of purchase commitments and 11,200 employees. See Item 1A and Form 10-K.")


def outcome(text):
    tok = number_tokens(text)[0]
    values = {(t["value"], t["kind"]) for t in number_tokens(SRC)}
    return check_number(tok, SRC, values)


class TestNumbers(unittest.TestCase):
    def test_tokens_skip_labels(self):
        self.assertEqual([t["text"] for t in number_tokens(SRC)],
                         ["2025", "65 percent", "$1.2 billion", "11,200"])

    def test_verified_exact(self):
        self.assertEqual(outcome("$1.2 billion"), "verified")
        self.assertEqual(outcome("fiscal 2025"), "verified")

    def test_needs_review_format_or_units(self):
        self.assertEqual(outcome("65%"), "needs review")            # 65 percent
        self.assertEqual(outcome("$1,200 million"), "needs review")  # same value as $1.2 billion

    def test_not_in_source(self):
        self.assertEqual(outcome("2019"), "not in source")
        self.assertEqual(outcome("1,200"), "not in source")  # must not match inside 11,200
        self.assertEqual(outcome("1.25 billion"), "not in source")


class TestClosestPassage(unittest.TestCase):
    def test_reports_the_changed_word(self):
        sentences = ["Supply disruptions could adversely affect our results.", "Other text here."]
        score, passage, diff = closest_passage("Supply disruptions may adversely affect our results.", sentences)
        self.assertGreater(score, 0.8)
        self.assertEqual(diff, ["quote 'may' vs filing 'could'"])


if __name__ == "__main__":
    unittest.main()
