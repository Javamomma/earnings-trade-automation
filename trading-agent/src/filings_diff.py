"""Filings delta detection — read deltas, not documents.

For each holding's most recent 10-Q/10-K, pull the primary document
text, locate the Risk Factors and MD&A sections, and diff against the
prior filing of the same form type. We surface only new or substantially
changed paragraphs — typically a few short blocks instead of 100 pages.

This is the biggest single research time-saver in the system. SEC
EDGAR full filings are 100k+ words; the meaningful change quarter to
quarter is usually a handful of paragraphs.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Iterable

import requests

from src.config import SETTINGS
from src.sec_filings import _headers, _ticker_to_cik_map

log = logging.getLogger(__name__)

# Section headers we care about. EDGAR text is messy — flexible matching.
SECTION_PATTERNS = {
    "risk_factors": re.compile(
        r"(item\s*1a\.?\s*risk\s*factors|risk\s*factors)",
        re.IGNORECASE,
    ),
    "mda": re.compile(
        r"(item\s*7\.?\s*management'?s?\s*discussion|management'?s?\s*discussion\s*and\s*analysis)",
        re.IGNORECASE,
    ),
}

# Generic next-section sentinels so we know when to stop reading.
SECTION_TERMINATORS = re.compile(
    r"(item\s*[1-9][a-z]?\.|signatures|table\s*of\s*contents)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FilingRef:
    accession: str
    form: str
    filed: str           # YYYY-MM-DD
    primary_doc_url: str


@dataclass(frozen=True)
class SectionDelta:
    section: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    added_word_count: int
    removed_word_count: int
    summary: str


@dataclass(frozen=True)
class FilingDigest:
    ticker: str
    latest: FilingRef
    prior: FilingRef | None
    sections: tuple[SectionDelta, ...]


def _accession_to_path(accession: str) -> str:
    """0001628280-26-001234 -> 1628280/000162828026001234"""
    a = accession.replace("-", "")
    cik = a[:10].lstrip("0")
    return f"{cik}/{a}"


def list_filings_with_docs(
    ticker: str, *, forms: Iterable[str] = ("10-Q", "10-K"), limit: int = 6
) -> list[FilingRef]:
    cik = _ticker_to_cik_map().get(ticker.upper())
    if not cik:
        return []
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=_headers(), timeout=SETTINGS.http_timeout,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("EDGAR submissions fetch failed for %s: %s", ticker, e)
        return []
    recent = (data.get("filings") or {}).get("recent") or {}
    forms_list = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    forms_set = {f.upper() for f in forms}
    out: list[FilingRef] = []
    for f, d, a, doc in zip(forms_list, dates, accs, docs):
        if f.upper() not in forms_set:
            continue
        path = _accession_to_path(a)
        url = f"https://www.sec.gov/Archives/edgar/data/{path}/{doc}"
        out.append(FilingRef(accession=a, form=f, filed=d, primary_doc_url=url))
        if len(out) >= limit:
            break
    return out


def _strip_html(text: str) -> str:
    """Best-effort HTML/XBRL strip without pulling bs4."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _fetch_filing_text(url: str) -> str:
    try:
        r = requests.get(url, headers=_headers(), timeout=SETTINGS.http_timeout * 2)
        r.raise_for_status()
    except Exception as e:
        log.warning("filing fetch failed (%s): %s", url, e)
        return ""
    return _strip_html(r.text)


def extract_section(text: str, section_key: str) -> str:
    """Locate a section by name and return its text, terminated at the
    next ``Item X.`` heading. Best-effort — EDGAR HTML is wildly
    inconsistent across registrants."""
    pat = SECTION_PATTERNS.get(section_key)
    if not pat:
        return ""
    matches = list(pat.finditer(text))
    if not matches:
        return ""
    # Use the second match if available — the first is typically the
    # table-of-contents reference rather than the section body.
    start_match = matches[1] if len(matches) > 1 else matches[0]
    start = start_match.end()
    # Find the next section terminator after `start`.
    tail = text[start:]
    terminator = SECTION_TERMINATORS.search(tail, pos=1)
    end = terminator.start() if terminator else len(tail)
    return tail[:end].strip()


def _split_paragraphs(s: str, min_words: int = 12) -> list[str]:
    """Split into sentence-ish chunks of meaningful length. We split on
    period-followed-by-capital to mimic paragraph breaks that the HTML
    strip ate."""
    raw = re.split(r"(?<=[.!?])\s+(?=[A-Z])", s)
    return [chunk.strip() for chunk in raw if len(chunk.split()) >= min_words]


def diff_section(prior_text: str, latest_text: str) -> SectionDelta:
    section_name = "section"  # caller will override
    prior_paras = _split_paragraphs(prior_text)
    latest_paras = _split_paragraphs(latest_text)
    prior_set = set(prior_paras)
    latest_set = set(latest_paras)
    # Treat near-duplicates as the same paragraph (90% similarity).
    added = []
    for p in latest_paras:
        if p in prior_set:
            continue
        if any(
            difflib.SequenceMatcher(None, p, q).ratio() > 0.9 for q in prior_paras
        ):
            continue
        added.append(p)
    removed = []
    for p in prior_paras:
        if p in latest_set:
            continue
        if any(
            difflib.SequenceMatcher(None, p, q).ratio() > 0.9 for q in latest_paras
        ):
            continue
        removed.append(p)
    awc = sum(len(p.split()) for p in added)
    rwc = sum(len(p.split()) for p in removed)
    summary = (
        f"+{len(added)} new ({awc}w), -{len(removed)} removed ({rwc}w)"
        if (added or removed) else "no material change"
    )
    return SectionDelta(
        section=section_name, added=tuple(added), removed=tuple(removed),
        added_word_count=awc, removed_word_count=rwc, summary=summary,
    )


def digest_for(ticker: str, *, sections: Iterable[str] = ("risk_factors", "mda")) -> FilingDigest | None:
    """Pull the two most recent quarterly/annual filings for a ticker
    and diff the requested sections. Returns None when there's nothing
    to diff (first filing, or both fetches failed)."""
    refs = list_filings_with_docs(ticker, forms=("10-Q", "10-K"), limit=4)
    if not refs:
        return None
    latest = refs[0]
    prior = next((r for r in refs[1:] if r.form == latest.form), None) or (refs[1] if len(refs) > 1 else None)
    if prior is None:
        return None
    latest_text = _fetch_filing_text(latest.primary_doc_url)
    prior_text = _fetch_filing_text(prior.primary_doc_url)
    if not latest_text or not prior_text:
        return None
    deltas: list[SectionDelta] = []
    for sec in sections:
        latest_sec = extract_section(latest_text, sec)
        prior_sec = extract_section(prior_text, sec)
        if not latest_sec and not prior_sec:
            continue
        d = diff_section(prior_sec, latest_sec)
        deltas.append(SectionDelta(
            section=sec, added=d.added, removed=d.removed,
            added_word_count=d.added_word_count,
            removed_word_count=d.removed_word_count, summary=d.summary,
        ))
    return FilingDigest(ticker=ticker.upper(), latest=latest, prior=prior, sections=tuple(deltas))
