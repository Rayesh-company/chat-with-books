"""The Notebook store (the selection map, ticket 08): the researcher's
capture rows — quote fixed, category/opinion editable, source a JSON
snapshot the Session's deletion can never cut, ownership the cookie."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import ui.note_store as note_store


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(note_store, "NOTES_DB", tmp_path / "notes.sqlite3")
    return note_store


BOOK_SOURCE = {"kind": "book", "doc": "tarhe-kolli", "title": "طرح کلی"}
REFS = [{"page": 10, "at": 434, "len": 197, "full": True}]


def test_quick_save_keeps_the_defaults(db):
    note = db.create_note("a@x", "نقل‌قول تمیزشده", doc="tarhe-kolli",
                          pages=[10], refs=REFS, source=BOOK_SOURCE)
    assert note["id"] > 0
    assert note["text"] == "نقل‌قول تمیزشده"
    assert note["category"] == "" and note["opinion"] == ""
    assert note["pages"] == [10] and note["refs"] == REFS
    assert note["source"] == BOOK_SOURCE
    assert note["created_at"] and note["updated_at"]


def test_list_is_newest_first_and_only_the_callers(db):
    db.create_note("a@x", "نخست")
    db.create_note("a@x", "دوم")
    db.create_note("b@x", "از حساب دیگر")
    rows = db.list_notes("a@x")
    assert [r["text"] for r in rows] == ["دوم", "نخست"]
    assert db.list_notes("b@x")[0]["text"] == "از حساب دیگر"
    assert db.list_notes("nobody@x") == []


def test_quote_is_fixed_category_and_opinion_editable(db):
    note = db.create_note("a@x", "عینِ منبع")
    saved = db.update_note("a@x", note["id"], category="بدون دسته",
                           opinion="نظر من")
    assert saved["text"] == "عینِ منبع"
    assert saved["category"] == "بدون دسته" and saved["opinion"] == "نظر من"
    # A field left None keeps its value — the panel edits one at a time.
    saved = db.update_note("a@x", note["id"], opinion="بازنویسی نظر")
    assert saved["category"] == "بدون دسته"
    assert saved["opinion"] == "بازنویسی نظر"


def test_foreign_note_is_silence(db):
    note = db.create_note("a@x", "مال یکی دیگر")
    assert db.get_note("b@x", note["id"]) is None
    assert db.update_note("b@x", note["id"], opinion="هک") is None
    assert db.delete_note("b@x", note["id"]) is False
    assert db.get_note("a@x", note["id"])["text"] == "مال یکی دیگر"


def test_delete_one(db):
    note = db.create_note("a@x", "موقت")
    assert db.delete_note("a@x", note["id"]) is True
    assert db.get_note("a@x", note["id"]) is None


def test_bulk_delete_counts_only_the_callers(db):
    mine1 = db.create_note("a@x", "یکی")
    mine2 = db.create_note("a@x", "دو")
    other = db.create_note("b@x", "غیره")
    gone = db.delete_notes_many("a@x", [mine1["id"], mine2["id"], other["id"], "junk"])
    assert gone == 2
    assert db.list_notes("a@x") == []
    assert db.get_note("b@x", other["id"]) is not None


def test_empty_bulk_delete_is_zero(db):
    db.create_note("a@x", "می‌ماند")
    assert db.delete_notes_many("a@x", []) == 0
    assert len(db.list_notes("a@x")) == 1


def test_runaway_payload_is_capped(db):
    note = db.create_note(
        "a@x", "ح" * 20000, category="د" * 200, opinion="ن" * 5000
    )
    assert len(note["text"]) == note_store._TEXT_FLOOR
    assert len(note["category"]) == note_store._CATEGORY_FLOOR
    assert len(note["opinion"]) == note_store._OPINION_FLOOR
