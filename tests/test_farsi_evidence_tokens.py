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
