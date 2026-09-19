"""The pre-push test gate (T16, GitLab #17): the suite itself is the gate —
no GitLab runner, no CI. These locks hold the hook's shape (the full suite,
quiet, exit code propagated) and the one-command install, so a fresh clone
picks the gate up and a trimmed-down command cannot sneak in."""

import os
from pathlib import Path

from tests.conftest import REPO_ROOT

HOOK = REPO_ROOT / "scripts" / "githooks" / "pre-push"
README = REPO_ROOT / "README.md"


def test_the_hook_runs_the_whole_suite_quietly():
    script = HOOK.read_text(encoding="utf-8")
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in script, "the gate must run the suite the way the repo runs it"
    assert "python -m pytest -q" in script, "the gate is the whole suite, quietly"
    assert "set -eu" in script, "pytest's exit code must propagate — it is the whole mechanism"
    assert "git rev-parse --show-toplevel" in script, "the gate runs from the repo root, wherever the push comes from"


def test_the_hook_is_not_a_subset():
    """A gate that runs one test file is a formality, not a gate."""
    script = HOOK.read_text(encoding="utf-8")
    assert "test_" not in script.replace("tests", ""), "no specific test file may be named — the whole suite or nothing"


def test_readme_documents_the_one_command_install():
    readme = README.read_text(encoding="utf-8")
    assert "core.hooksPath scripts/githooks" in readme, "a fresh clone must pick the gate up with one command"


def test_hookspath_installed_in_this_checkout():
    """This checkout itself is gated: core.hooksPath points at the committed
    hook, so the next push from here runs the suite."""
    config = subprocess_config()
    assert "core.hookspath=scripts/githooks" in config


def subprocess_config() -> str:
    import subprocess

    result = subprocess.run(
        ["git", "config", "--local", "--list"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.lower()
