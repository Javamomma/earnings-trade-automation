"""Integration tests for the SEC EDGAR path.

These don't touch the network — they substitute requests.get with
fixtures shaped exactly like the real EDGAR responses. The point is
to exercise the parsing + dilution-flagging end-to-end so a refactor
that breaks the Filing dataclass or the form-name list can't slip
through the unit suite (which only covers pure scoring).
"""

from __future__ import annotations

from unittest import mock

import pytest

from src import sec_filings as s


# Real shape of https://www.sec.gov/files/company_tickers.json
TICKER_FIXTURE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1640147, "ticker": "MULN", "title": "Mullen Automotive"},
}

# Fixture dates are generated relative to today so the tests never age
# out of the lookback window (a hardcoded-date version broke two months
# after it was written).
from datetime import date, timedelta


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


# Real shape of https://data.sec.gov/submissions/CIK#########.json,
# trimmed to the fields we actually consume.
AAPL_FIXTURE = {
    "filings": {
        "recent": {
            "form": ["10-Q", "8-K", "4", "10-K", "DEF 14A"],
            "filingDate": [_d(10), _d(20), _d(30), _d(60), _d(90)],
            "accessionNumber": ["b1", "b2", "b3", "b4", "b5"],
            "primaryDocDescription": [
                "Quarterly report",
                "Earnings release",
                "Statement of changes in beneficial ownership",
                "Annual report",
                "Definitive proxy statement",
            ],
        }
    }
}

# A textbook dilutive small-cap pattern: shelf S-3 plus multiple 424B
# prospectus draws plus an FWP carrying ATM-offering language.
MULN_MOST_RECENT_DILUTIVE_DATE = _d(15)
MULN_FIXTURE = {
    "filings": {
        "recent": {
            "form": ["8-K", "S-3", "424B5", "FWP", "10-Q", "424B5"],
            "filingDate": [
                _d(5), MULN_MOST_RECENT_DILUTIVE_DATE,
                MULN_MOST_RECENT_DILUTIVE_DATE,
                _d(30), _d(35), _d(45),
            ],
            "accessionNumber": ["a1", "a2", "a3", "a4", "a5", "a6"],
            "primaryDocDescription": [
                "Current report",
                "Registration statement (shelf)",
                "Prospectus supplement — common stock offering",
                "At-the-market offering — equity distribution agreement",
                "Quarterly report",
                "Prospectus supplement — direct offering",
            ],
        }
    }
}


class _FakeResp:
    def __init__(self, payload, status: int = 200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests as _r
            raise _r.HTTPError(str(self.status_code))

    def json(self):
        return self._p


def _fake_get(url, headers=None, timeout=None):
    if "company_tickers" in url:
        return _FakeResp(TICKER_FIXTURE)
    if "CIK0000320193" in url:
        return _FakeResp(AAPL_FIXTURE)
    if "CIK0001640147" in url:
        return _FakeResp(MULN_FIXTURE)
    return _FakeResp({}, status=404)


@pytest.fixture(autouse=True)
def _reset_cache():
    s._reset_ticker_map_cache()
    yield
    s._reset_ticker_map_cache()


def test_ticker_map_parses_real_shape():
    with mock.patch.object(s.requests, "get", _fake_get):
        m = s._ticker_to_cik_map()
    assert m["AAPL"] == "0000320193"
    assert m["MULN"] == "0001640147"
    assert "BOGUS" not in m


def test_clean_megacap_does_not_flag_dilution():
    with mock.patch.object(s.requests, "get", _fake_get):
        filings = s.fetch_recent_filings("AAPL", lookback_days=120)
    assert len(filings) == 5
    assert all(not f.is_dilutive for f in filings)
    summary = s.dilution_risk_summary(filings)
    assert summary["any_dilutive"] is False
    assert summary["count"] == 0


def test_dilutive_smallcap_flags_correctly():
    with mock.patch.object(s.requests, "get", _fake_get):
        filings = s.fetch_recent_filings("MULN", lookback_days=120)
    summary = s.dilution_risk_summary(filings)
    assert summary["any_dilutive"] is True
    # S-3 + two 424B5 + FWP-with-ATM-language = 4 dilutive items.
    assert summary["count"] == 4
    assert summary["most_recent_form"] in {"S-3", "424B5"}
    assert summary["most_recent_date"] == MULN_MOST_RECENT_DILUTIVE_DATE


def test_filing_dataclass_exists_and_is_constructable():
    """Regression: a refactor accidentally removed the Filing dataclass.
    fetch_recent_filings was importing 'date' but not 'Filing' from this
    module, so the error only surfaced on first call."""
    from datetime import date as _d
    f = s.Filing(
        ticker="X", form="S-3", filed=_d(2026, 1, 1),
        accession="z", description="shelf", is_dilutive=True,
    )
    assert f.is_dilutive is True


def test_lookback_window_excludes_old_filings():
    with mock.patch.object(s.requests, "get", _fake_get):
        # MULN's oldest fixture entry is 2026-04-30; a 7-day lookback
        # from "today" should exclude it. We assert it's not the oldest.
        filings = s.fetch_recent_filings("MULN", lookback_days=10_000)
    dates = [f.filed.isoformat() for f in filings]
    assert dates == sorted(dates, reverse=True), "should be newest first"


def test_unknown_ticker_returns_empty():
    with mock.patch.object(s.requests, "get", _fake_get):
        assert s.fetch_recent_filings("DOESNOTEXIST") == []


def test_atm_language_in_fwp_flags():
    """FWP alone is not dilutive, but FWP + ATM language is."""
    assert s._is_dilutive("FWP", "Free writing prospectus") is False
    assert s._is_dilutive("FWP", "At-the-market offering details") is True
    assert s._is_dilutive("FWP", "ATM offering supplement") is True


@pytest.mark.parametrize(
    "form,expected",
    [
        ("S-1", True),
        ("S-1/A", True),
        ("S-3", True),
        ("S-3ASR", True),
        ("F-3", True),
        ("424B1", True),
        ("424B5", True),
        ("10-K", False),
        ("10-Q", False),
        ("8-K", False),
        ("4", False),
        ("DEF 14A", False),
    ],
)
def test_form_classification(form, expected):
    assert s._is_dilutive(form) is expected
