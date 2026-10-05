"""[hidden] must actually hide: every author `display` rule that can match a
hidden-attr element needs a `[hidden]` display:none guard (or set display
only under :not([hidden])).

Regression for the 2026-10-05 prod incident: the ticket deploy styled
`.tk-modal`/`#tickets-panel`/`.tickets-badge` with unconditional `display`
rules while the JS toggles them via the `hidden` attribute. Author rules
override the UA `[hidden] { display: none }`, so the "closed" empty modal
rendered as a full-viewport backdrop blocking every click on the app
(scroll kept working — wheel events are not pointer-events-gated).

The house style is a sibling `[hidden]` guard rule (see #sl-backdrop,
.thread-list, .composer-dock, #reader-backdrop); this test holds every
hidden-toggled element to that style.
"""
import re
from html.parser import HTMLParser

from tests.conftest import REPO_ROOT

UI = REPO_ROOT / "ui" / "index.html"


class _HiddenCollector(HTMLParser):
    """Static-markup elements carrying the hidden attribute."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden_elements = []  # (id, [classes])

    def handle_starttag(self, tag, attrs):
        names = {k for k, _ in attrs}
        if "hidden" not in names:
            return
        attrd = dict(attrs)
        ident = attrd.get("id")
        classes = (attrd.get("class") or "").split()
        if ident or classes:
            self.hidden_elements.append((ident, classes))


def _css_blocks(html):
    for m in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.S):
        yield m.group(1)


def _parse_rules(css):
    """Flatten selector{declarations} pairs, recursing into at-rule blocks."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j == -1:
            break
        header = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
            k += 1
        body = css[j + 1 : k - 1]
        if header.startswith("@media") or header.startswith("@supports"):
            rules.extend(_parse_rules(body))
        elif not header.startswith("@"):
            selectors = [s.strip() for s in header.split(",") if s.strip()]
            if selectors:
                rules.append((selectors, body))
        i = k
    return rules


def _declares_display(declarations, *values):
    """True if a `display:` declaration matches one of the given values.

    Called with no values: any display other than none (a hidden-override
    hazard). Called with "none": a `[hidden]` guard rule.
    """
    for decl in declarations.split(";"):
        prop, _, value = decl.partition(":")
        value = value.strip().lower()
        if prop.strip().lower() != "display":
            continue
        if value == "none" and not values:
            continue
        if not values or value in values:
            return True
    return False


def _matches_hook(selector, hook):
    # hook ('.tk-modal' / '#tickets-panel') as a whole compound in the selector
    return re.search(r"(^|[>\s+~,])" + re.escape(hook) + r"($|[:\s>.+~,])", selector) is not None


def _subject(selector):
    """The compound a rule actually styles: its last compound (`a .b > .c` -> .c)."""
    compound = selector.split()[-1] if selector.split() else ""
    return compound.lstrip(">+~")


def test_every_hidden_element_with_a_display_rule_has_a_hidden_guard():
    html = UI.read_text(encoding="utf-8")

    collector = _HiddenCollector()
    # Feed markup only — style/script bodies are not element attributes.
    for m in re.finditer(r"<body[^>]*>(.*)</body>", html, re.S):
        markup = re.sub(r"<style[^>]*>.*?</style>|<script[^>]*>.*?</script>", "", m.group(1), flags=re.S)
        collector.feed(markup)

    guarded = set()
    hazard = {}  # hook -> selector that styles it with display while hidden-able
    for selectors, declarations in _parse_rules("\n".join(_css_blocks(html))):
        hides = _declares_display(declarations, "none")
        shows = _declares_display(declarations)
        for sel in selectors:
            subject = _subject(sel)
            if not subject:
                continue
            if subject.endswith("[hidden]"):
                if hides:
                    guarded.add(subject[: -len("[hidden]")])
            elif ":not([hidden])" in sel:
                continue  # display applies only when visible — safe
            elif shows and subject.startswith((".", "#")):
                hazard.setdefault(subject, sel)

    problems = []
    for ident, classes in collector.hidden_elements:
        hooks = ([f"#{ident}"] if ident else []) + [f".{c}" for c in classes]
        for hook in hooks:
            if hook in hazard and hook not in guarded:
                problems.append(
                    f"{hook} is styled with `display` ({hazard[hook]!r}) "
                    f"but has no `{hook}[hidden] {{ display: none }}` guard"
                )

    assert not problems, (
        "Elements toggled via the hidden attribute render anyway:\n  "
        + "\n  ".join(problems)
    )
