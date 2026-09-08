import importlib.util

from tests.conftest import REPO_ROOT

PATCHER = REPO_ROOT / "docker" / "enable_farsi_evidence.py"

COGNEE_TOKEN_SITES = '''
import re
_STOPWORDS = frozenset({"the"})

def _significant_terms(text: str):
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {token for token in tokens if len(token) >= 3 and token not in _STOPWORDS}

def chunk_terms(text: str):
    return set(re.findall(r"[a-z0-9]+", text.lower()))
'''

COGNEE_BULLET_SITES = '''
import re
from typing import Any, Optional

def _provenance_suffix(data_id, chunk_id):
    parts = []
    return f" ({', '.join(parts)})" if parts else ""

def _snippet(text):
    return text

def _chunk_id(obj: Any, payload: dict) -> Optional[str]:
    return None

def build(document_name, number, text, data_id, chunk_id):
    return (
        f"- chunk {number} of document {document_name}"
        f\'{_provenance_suffix(data_id, chunk_id)}: "{_snippet(text)}"\'
    )
'''

PRISTINE_BULLET = 'f\'{_provenance_suffix(data_id, chunk_id)}: "{_snippet(text)}"\''
BULLET_WITH_IDS_AND_PAGES = (
    'f\'{_provenance_suffix(data_id, chunk_id)}{_pages_suffix(text)}: "{_snippet(text)}"\''
)


def _load_patcher():
    spec = importlib.util.spec_from_file_location("enable_farsi_evidence", PATCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exec(source: str) -> dict:
    namespace: dict = {}
    exec(source, namespace)
    return namespace


def test_patch_references_lets_farsi_terms_ground_evidence():
    patcher = _load_patcher()
    namespace = _exec(patcher.patch_references(COGNEE_TOKEN_SITES))

    answer = "اندیشه اسلامی در قرآن"
    chunk = "طرح کلی اندیشۀ اسلامی در قرآن"

    terms = namespace["_significant_terms"](answer)
    assert terms == {"اندیشه", "اسلامی", "قرآن"}
    assert len(terms & namespace["chunk_terms"](chunk)) >= 1


def test_patch_bullets_show_pages_and_drop_id_provenance():
    patcher = _load_patcher()
    patched = patcher.patch_bullets(COGNEE_BULLET_SITES)
    assert patcher.patch_bullets(patched) == patched

    namespace = _exec(patched)
    suffix = namespace["_pages_suffix"]
    assert suffix("Page 263: متن کتاب Page 265: ادامه") == " (pages 263-265)"
    assert suffix("Page 265: متن کتاب") == " (page 265)"
    assert suffix("متنی بدون نشانهٔ صفحه") == ""

    bullet = namespace["build"]("tarhe-kolli", 29, "Page 214: متن کتاب", None, None)
    assert bullet == '- chunk 29 of document tarhe-kolli (page 214): "Page 214: متن کتاب"'
    assert "data_id" not in bullet and "chunk_id" not in bullet


def test_patch_bullets_upgrades_the_pages_only_state():
    patcher = _load_patcher()
    already_pages = COGNEE_BULLET_SITES.replace(
        PRISTINE_BULLET, BULLET_WITH_IDS_AND_PAGES, 1
    )
    patched = patcher.patch_bullets(already_pages)
    assert patcher.patch_bullets(patched) == patched

    namespace = _exec(patched)
    bullet = namespace["build"]("tarhe-kolli", 29, "Page 214: متن کتاب", None, None)
    assert bullet == '- chunk 29 of document tarhe-kolli (page 214): "Page 214: متن کتاب"'
