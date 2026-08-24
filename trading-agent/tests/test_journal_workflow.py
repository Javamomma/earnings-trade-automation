"""Integration tests for theses + proposals + outcomes against a
real SQLite DB in a tmp_path. Exercises the schema and the basic
agent-proposes-human-acts flow."""

from __future__ import annotations

import pytest

from src import proposals, theses, trade_journal


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point SETTINGS.journal_path() at a tmpdir DB so each test is
    isolated and we don't pollute the developer's real journal."""
    from src import config

    db = tmp_path / "test.db"

    class _S(config.Settings):
        pass

    fake = _S(journal_db_path=str(db))
    # patch where trade_journal reads it
    monkeypatch.setattr(trade_journal, "SETTINGS", fake)
    # also patch the property so journal_path resolves to the tmpdir absolute
    monkeypatch.setattr(
        type(fake), "journal_path",
        lambda self: db
    )
    yield


class TestProposalLifecycle:
    def test_propose_then_review(self):
        pid = proposals.propose(
            ticker="MSFT", kind="csp",
            rationale="Sell 380p 30dte",
            structured={"strike": 380, "expiry": "2026-09-19"},
            invalidation="Close if MSFT < 370",
        )
        assert isinstance(pid, int) and pid > 0

        open_ = proposals.list_open()
        assert len(open_) == 1
        assert open_[0].ticker == "MSFT"
        assert open_[0].kind == "csp"
        assert open_[0].structured["strike"] == 380

        proposals.review(pid, "accepted", note="placed via Fidelity")
        assert proposals.list_open() == []

        recent = proposals.list_recent(days=7)
        assert recent[0].status == "accepted"
        assert recent[0].review_note == "placed via Fidelity"

    def test_unknown_action_rejected(self):
        pid = proposals.propose("X", "csp", "x")
        with pytest.raises(ValueError):
            proposals.review(pid, "BOGUS")


class TestAgentAnnotation:
    """The headless evaluator's only write: annotate without touching
    status. Also exercises the additive column migration."""

    def test_annotate_preserves_status(self):
        pid = proposals.propose("MSFT", "csp", "Sell 380p")
        proposals.annotate(pid, "Aligned with buy zone; IV rich.", "endorse")
        open_ = proposals.list_open()
        assert len(open_) == 1, "annotation must not close the proposal"
        assert open_[0].agent_stance == "endorse"
        assert "IV rich" in open_[0].agent_analysis

    def test_annotate_without_stance(self):
        pid = proposals.propose("X", "cc", "y")
        proposals.annotate(pid, "Needs more data.")
        assert proposals.list_open()[0].agent_stance is None

    def test_invalid_stance_rejected(self):
        pid = proposals.propose("X", "cc", "y")
        with pytest.raises(ValueError):
            proposals.annotate(pid, "text", "strong_buy")

    def test_review_after_annotation_keeps_analysis(self):
        pid = proposals.propose("MSFT", "csp", "Sell 380p")
        proposals.annotate(pid, "Good setup.", "endorse")
        proposals.review(pid, "accepted", note="done")
        recent = proposals.list_recent(days=7)
        assert recent[0].status == "accepted"
        assert recent[0].agent_stance == "endorse"
        assert recent[0].agent_analysis == "Good setup."


class TestThesisInvalidations:
    def test_price_invalidation_fires(self):
        tid = theses.create_thesis(
            ticker="MSFT", direction="long",
            setup="quality on sale",
            thesis="Compounder at 15% discount",
            invalidation_price=350.0,
            invalidates="If MSFT closes below 350",
        )
        # Above invalidation: no hit.
        assert theses.check_invalidations({"MSFT": 400.0}) == []
        # Below invalidation: hit.
        hits = theses.check_invalidations({"MSFT": 340.0})
        assert len(hits) == 1
        assert hits[0][0].id == tid
        assert "350" in hits[0][1]

    def test_close_thesis_removes_from_active(self):
        tid = theses.create_thesis(
            ticker="X", direction="long",
            setup="x", thesis="x",
        )
        assert any(t.id == tid for t in theses.list_active())
        theses.close_thesis(tid, reason="manual exit")
        assert not any(t.id == tid for t in theses.list_active())

    def test_horizon_invalidation_fires(self):
        # Created today with horizon 0 should already qualify.
        tid = theses.create_thesis(
            ticker="X", direction="long",
            setup="x", thesis="x", horizon_days=0,
        )
        hits = theses.check_invalidations({"X": 100.0})
        assert any(t.id == tid for t, _ in hits)
