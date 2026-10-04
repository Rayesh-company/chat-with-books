"""The usage metering (T22, GitLab #24): the tariff is arithmetic the
tests pin table by table; the ledger's honesty is its own lock — an
entry is metered only when the upstream actually exposed its token
counts, and everything else rides marked as estimated; the session
anchor re-anchors per ask; the composer tap fires once per call and a
crashing listener can never take the answer down; the endpoint answers
the Account's own totals and no one else's."""

import json

import pytest

from tests.helpers import FakeResponse
from ui import composer, ledger
from ui.ledger import (
    cost_toman,
    estimate_tokens,
    record,
    record_composer_call,
    record_size_estimate,
    session_total,
    today_total,
)


# --- the pure tariff ---------------------------------------------------------


@pytest.mark.parametrize(
    "input_tokens,output_tokens,expected",
    [
        (0, 0, 0),  # nothing spent, nothing billed
        (1_000_000, 0, 30000),  # exactly one million input at the input rate
        (0, 1_000_000, 120000),  # exactly one million output at the output rate
        (500_000, 250_000, 45000),  # the halves sum: 15000 + 30000
        (750_000, 125_000, 37500),  # mixed thirds at the two rates
        (2_000_000, 1_000_000, 180000),  # the rates compose linearly
    ],
)
def test_the_tariff_is_pure_arithmetic(input_tokens, output_tokens, expected):
    assert cost_toman(input_tokens, output_tokens) == expected


def test_the_tariff_reads_config_not_code(monkeypatch):
    """A price change is an .env edit and a restart — the function reads
    the module's config attributes, which the container gets from compose."""
    monkeypatch.setattr(ledger, "TARIFF_INPUT_TOMAN_PER_MTOK", 10_000)
    monkeypatch.setattr(ledger, "TARIFF_OUTPUT_TOMAN_PER_MTOK", 0)
    assert cost_toman(1_000_000, 5_000_000) == 10_000


@pytest.mark.parametrize(
    "text,expected",
    [("", 0), ("یک", 1), ("abcd", 2), ("abcdef", 2), ("abcdefg", 3)],
)
def test_the_estimator_is_characters_over_three(text, expected):
    assert estimate_tokens(text) == expected


# --- the ledger's honesty ----------------------------------------------------


def test_a_metered_entry_records_the_upstreams_own_counts():
    entry = record("09120000000", "picker", 1500, 900, metered=True)
    assert entry["metered"] is True
    assert entry["cost_toman"] == cost_toman(1500, 900)


def test_an_estimated_entry_is_marked_estimated():
    entry = record("09120000000", "ask", 300, 0, metered=False)
    assert entry["metered"] is False, "the estimate never passes for a measurement"


def test_the_composer_reply_with_usage_rides_metered():
    reply = {
        "choices": [{"message": {"content": "پاسخ"}}],
        "usage": {"prompt_tokens": 2000, "completion_tokens": 800},
    }
    entry = record_composer_call("09120000000", "picker", "پرسش", reply)
    assert entry["metered"] is True
    assert entry["input_tokens"] == 2000 and entry["output_tokens"] == 800


def test_the_composer_reply_without_usage_rides_estimated():
    reply = {"choices": [{"message": {"content": "پاسخِ پانصد نویسه" * 30}}]}
    entry = record_composer_call("09120000000", "writer", "پرسش", reply)
    assert entry["metered"] is False
    assert entry["input_tokens"] == estimate_tokens("پرسش")
    assert entry["output_tokens"] == estimate_tokens(reply["choices"][0]["message"]["content"])


def test_the_ask_entry_is_an_input_side_estimate():
    entry = record_size_estimate("09120000000", "ask", 1200)
    assert entry["kind"] == "ask" and entry["metered"] is False
    assert entry["output_tokens"] == 0


# --- the session anchor ------------------------------------------------------


def test_the_session_total_reanchors_on_each_ask():
    phone = "09120000000"
    record_size_estimate(phone, "ask", 900)  # session one opens — 9 toman estimated
    record(phone, "picker", 1_000_000, 0, metered=True)  # 30000
    assert session_total(phone) == 30009

    record_size_estimate(phone, "ask", 900)  # session two opens — re-anchor
    record(phone, "writer", 0, 250_000, metered=True)  # 30000 at the output rate
    # 30000 for the writer + the ask entry's own estimate (300 tokens at
    # the input rate rounds to 9): the anchor re-anchors, nothing bleeds.
    assert session_total(phone) == 30009, "session one's spend never bleeds into session two"


def test_the_session_total_is_zero_before_any_ask():
    record("09120000000", "picker", 1_000_000, 0, metered=True)
    assert session_total("09120000000") == 0, "no ask, no open Session"


def test_the_totals_are_per_account():
    record_size_estimate("09120000000", "ask", 900)
    record("09120000000", "picker", 1_000_000, 0, metered=True)
    assert session_total("09350000000") == 0
    assert today_total("09350000000") == 0
    assert today_total("09120000000") == session_total("09120000000")


# --- the composer tap --------------------------------------------------------


def test_the_tap_fires_once_per_call_with_the_raw_reply(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    seen = []
    composer.set_meter(lambda prompt, reply: seen.append((prompt, reply)))
    try:
        monkeypatch.setattr(
            composer,
            "urlopen",
            lambda request, timeout=None: FakeResponse(
                json.dumps({
                    "choices": [{"message": {"content": "پاسخ"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }).encode("utf-8")
            ),
        )
        composer._composer_reply("پرسش آزمایشی", "disabled")
    finally:
        composer.set_meter(None)

    assert len(seen) == 1
    assert seen[0][0] == "پرسش آزمایشی"
    assert seen[0][1]["usage"]["prompt_tokens"] == 10


def test_a_crashing_listener_never_takes_the_answer_down(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    def exploding(prompt, reply):
        raise RuntimeError("the meter is not the product")

    composer.set_meter(exploding)
    try:
        monkeypatch.setattr(
            composer,
            "urlopen",
            lambda request, timeout=None: FakeResponse(
                json.dumps({"choices": [{"message": {"content": "پاسخ سالم"}}]}).encode("utf-8")
            ),
        )
        reply = composer._composer_reply("پرسش", "disabled")
    finally:
        composer.set_meter(None)

    assert reply["choices"][0]["message"]["content"] == "پاسخ سالم"


def test_a_cleared_tap_fires_nothing(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    seen = []
    composer.set_meter(lambda prompt, reply: seen.append(reply))
    composer.set_meter(None)
    monkeypatch.setattr(
        composer,
        "urlopen",
        lambda request, timeout=None: FakeResponse(
            json.dumps({"choices": [{"message": {"content": "پاسخ"}}]}).encode("utf-8")
        ),
    )
    composer._composer_reply("پرسش", "disabled")
    assert seen == [], "one request can never bill another"


class FakeUpstream:
    """Stands in for Cognee at the relay seam: the ask's entry lands at the
    gate, before the reply streams — this only has to answer JSON."""

    def __call__(self, request, timeout=None):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def read(self):
                return json.dumps({"answer": "پاسخ", "evidence": []}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Response()


# --- the endpoint and the config contract ------------------------------------


def test_the_usage_live_endpoint_answers_the_accounts_own_totals():
    from tests.helpers import TEST_BALANCE_TOMAN, get, post, with_gate, stop_gate
    import tempfile
    from pathlib import Path

    base, server, originals = with_gate(Path(tempfile.mkdtemp()), FakeUpstream())
    try:
        status, body = get(base, "/usage/live", phone="09120000000")
        assert status == 200
        assert body["session_toman"] == 0 and body["today_toman"] == 0

        # The ask path records: the gate's ask entry lands on the POST.
        post(
            base,
            "/api/v1/recall",
            {"query": "پرسش آزمایشی دربارهٔ قرآن و اندیشۀ اسلامی " * 60},
            phone="09120000000",
        )
        status, body = get(base, "/usage/live", phone="09120000000")
        assert status == 200
        assert body["session_toman"] > 0 and body["today_toman"] >= body["session_toman"]
        # The chip speaks credit (2026-09-29): the Balance rides beside
        # the totals — the sitting's share of the credit is one read,
        # and the ask above visibly spent some of it.
        assert 0 < body["balance_toman"] < TEST_BALANCE_TOMAN
    finally:
        stop_gate(server, originals)


def test_the_usage_live_endpoint_refuses_the_anonymous():
    from tests.helpers import raw_get, with_gate, stop_gate

    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp())
    base, server, originals = with_gate(tmp, None)
    try:
        status, body = raw_get(base, "/usage/live")
        assert status == 401
    finally:
        stop_gate(server, originals)


def test_the_config_contract_is_pinned():
    """The image ships the ledger module; compose holds the ledger's path
    on the persisted volume and the two tariff dials — the shape that
    makes a price change an .env edit, not a deploy."""
    dockerfile = (ledger.__file__ and "") or ""
    from pathlib import Path

    df = (Path(ledger.__file__).parent / "Dockerfile").read_text(encoding="utf-8")
    assert "ledger.py" in df
    compose = (Path(ledger.__file__).parent.parent / "compose.yaml").read_text(encoding="utf-8")
    assert "LEDGER_DB: /data/usage_ledger.sqlite3" in compose
    assert "TARIFF_INPUT_TOMAN_PER_MTOK" in compose
    assert "TARIFF_OUTPUT_TOMAN_PER_MTOK" in compose
