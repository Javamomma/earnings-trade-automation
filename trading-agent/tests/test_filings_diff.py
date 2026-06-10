"""Filings-diff pure-function tests. Network paths are not exercised
here; the parsing/diff logic is the testable surface."""

from __future__ import annotations

from src.filings_diff import (
    _accession_to_path,
    _split_paragraphs,
    _strip_html,
    diff_section,
    extract_section,
)


class TestAccessionToPath:
    def test_standard_form(self):
        # 0001628280-26-001234 -> cik 1628280, no-dashes accession
        assert _accession_to_path("0001628280-26-001234") == "1628280/000162828026001234"

    def test_strips_leading_zeros_on_cik(self):
        assert _accession_to_path("0000320193-24-000001") == "320193/000032019324000001"


class TestStripHtml:
    def test_removes_tags(self):
        assert "h1" not in _strip_html("<h1>Hello</h1>")
        assert "Hello" in _strip_html("<h1>Hello</h1>")

    def test_collapses_whitespace(self):
        assert _strip_html("a   b\n\nc") == "a b c"

    def test_decodes_common_entities(self):
        assert "&" in _strip_html("A &amp; B")


class TestExtractSection:
    def test_pulls_risk_factors_body(self):
        text = (
            "Table of contents Item 1A. Risk Factors Item 1A. Risk Factors "
            "The company faces substantial risks. New paragraph about competition. "
            "Item 2. Properties The company owns offices."
        )
        section = extract_section(text, "risk_factors")
        assert "substantial risks" in section
        assert "Properties" not in section

    def test_returns_empty_when_no_section(self):
        assert extract_section("nothing useful here", "risk_factors") == ""

    def test_mda_section(self):
        text = (
            "Item 7. Management's Discussion and Analysis "
            "Item 7. Management's Discussion and Analysis "
            "Revenue grew 12% year over year. "
            "Item 8. Financial Statements"
        )
        section = extract_section(text, "mda")
        assert "12%" in section
        assert "Financial Statements" not in section


class TestDiffSection:
    def test_no_change_summary(self):
        para = (
            "This is a fairly long paragraph about risk factors that the "
            "company has been disclosing for many quarters now."
        )
        d = diff_section(para, para)
        assert d.added == () and d.removed == ()
        assert "no material change" in d.summary

    def test_detects_added_paragraph(self):
        prior = (
            "The company faces risks from competition in its core markets. "
            "Operating in a global environment introduces currency risk."
        )
        latest = prior + (
            " A new regulatory inquiry has been initiated by the SEC "
            "regarding revenue recognition practices in the last fiscal year."
        )
        d = diff_section(prior, latest)
        assert any("regulatory inquiry" in p for p in d.added)
        assert d.added_word_count > 0


class TestSplitParagraphs:
    def test_filters_short_chunks(self):
        text = "Short. Another short. " + "Word " * 30
        chunks = _split_paragraphs(text)
        assert all(len(c.split()) >= 12 for c in chunks)
