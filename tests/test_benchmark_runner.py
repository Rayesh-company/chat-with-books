"""The benchmark runner's Quote selection mode (issue #29): the TKI set
through the product-as-sold first answer — the HYBRID_COMPLETION recall
plus exactly one picker call on the sheet's /quote-selection. The
runner's Evidence parsing mirrors the sheet's (browser splitCitation +
evidenceSources, serve.parse_evidence_sources); an empty or failed
picker is the recorded prose fallback, never a crash."""

import importlib.util
import json
import sys
import urllib.request

from tests.conftest import REPO_ROOT
from tests.helpers import FakeResponse

spec = importlib.util.spec_from_file_location(
    "run_benchmark", REPO_ROOT / "benchmark" / "run_benchmark.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


# A recall reply the way Cognee shapes it: prose answer, then the
# Evidence block of `locator: "quoted passage"` bullets.
RECALL_TEXT = (
    "پاسخ نخست دربارهٔ قرآن.\n\n"
    "Evidence:\n"
    '- chunk 101 of document tarhe-kolli (pages 740-745): "قرآن کتابی است برای زندگی"\n'
    '- chunk 29 of document tarhe-kolli: "انسان در جامعه می‌زیید"'
)


def test_parse_evidence_sources_mirrors_the_sheet_parsing():
    sources = runner.parse_evidence_sources(RECALL_TEXT)
    assert sources == [
        {
            "reference": "chunk 101 of document tarhe-kolli (pages 740-745)",
            "passage": "قرآن کتابی است برای زندگی",
        },
        {
            "reference": "chunk 29 of document tarhe-kolli",
            "passage": "انسان در جامعه می‌زیید",
        },
    ]


def test_parse_evidence_sources_without_block_or_bullet_yields_no_invented_pool():
    # No Evidence marker: no pool, never an invented passage.
    assert runner.parse_evidence_sources("پاسخ بدون استناد.") == []
    # A bullet without the `: "` passage shape drops; the rest stays.
    text = 'Evidence:\n- chunk 3: malformed\n- chunk 4: "سالم"'
    assert runner.parse_evidence_sources(text) == [
        {"reference": "chunk 4", "passage": "سالم"}
    ]


def test_pick_selection_posts_pool_with_session_phone(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["phone"] = request.get_header("X-session-phone")
        captured["timeout"] = timeout
        body = json.dumps(
            {"selections": [{"text": "جملهٔ برگزیده"}], "pool_size": 2}
        ).encode("utf-8")
        return FakeResponse(body)

    monkeypatch.setattr(runner, "urlopen", fake_urlopen)
    payload, wall = runner.pick_selection(
        "http://localhost:8765",
        "09120000000",
        "پرسش؟",
        [{"reference": "chunk 1", "passage": "متن"}],
        timeout=120,
    )
    assert captured["url"] == "http://localhost:8765/quote-selection"
    assert captured["payload"] == {
        "question": "پرسش؟",
        "sources": [{"reference": "chunk 1", "passage": "متن"}],
    }
    assert captured["phone"] == "09120000000"
    assert captured["timeout"] == 120
    assert payload == {"selections": [{"text": "جملهٔ برگزیده"}], "pool_size": 2}
    assert isinstance(wall, float) and wall >= 0.0


def test_picker_defaults_are_the_sheet_and_the_reserved_benchmark_phone():
    parser = runner.build_parser()
    args = parser.parse_args(["--quote-selection"])
    assert args.quote_selection is True
    assert args.sheet_url == "http://localhost:8765"
    assert args.phone == "09120000000"
    off = parser.parse_args([])
    assert off.quote_selection is False


def test_stage_timings_carry_avg_and_total_per_stage():
    timings = runner.stage_timings(
        recalls=[10.0, 30.0], pickers=[2.0, 4.0], totals=[12.0, 34.0]
    )
    assert timings == {
        "recall": {"avg": 20.0, "total": 40.0},
        "picker": {"avg": 3.0, "total": 6.0},
        "total": {"avg": 23.0, "total": 46.0},
    }


def test_picker_outcome_records_selection_texts_and_fallback():
    rec = {"recall_wall_seconds": 10.0}
    ok = runner.apply_picker_outcome(
        dict(rec),
        payload={"selections": [{"text": "یک"}, {"text": "دو"}], "pool_size": 5},
        wall=3.25,
    )
    assert ok["picker_wall_seconds"] == 3.25
    assert ok["selection"] == ["یک", "دو"]
    assert ok["selection_count"] == 2
    assert ok["selection_fallback"] is False
    assert ok["total_wall_seconds"] == 13.25

    empty = runner.apply_picker_outcome(dict(rec), payload={"selections": []}, wall=1.0)
    assert empty["selection_count"] == 0
    assert empty["selection_fallback"] is True  # the recorded fallback
    assert empty["total_wall_seconds"] == 11.0

    failed = runner.apply_picker_outcome(dict(rec), error="HTTPError: 429")
    assert failed["selection_count"] == 0
    assert failed["selection_fallback"] is True
    assert failed["picker_error"] == "HTTPError: 429"
    assert failed["picker_wall_seconds"] is None
