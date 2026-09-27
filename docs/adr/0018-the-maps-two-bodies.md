# ADR 0018 — The map's two bodies: the redrawn rail and the mobile compass

Date: 2026-09-27
Status: accepted
Amends: ADR-0009 (the visible map's anatomy — every row survives, the presentation moves), ADR-0014 (the shell's layout vocabulary)
Context for: the 2026-09-27 operator feedback round (the map's clutter, and the map on the phone)

## Context

The operator's report: the map's rail (T10) had grown cluttered — rows of joined strings that could not break («؛»-joined open questions, «|»-joined decisions), stage labels pinned `nowrap` inside a wrapping flex, a nested scroll surface with a hard-coded sticky offset — and on the phone it was worse than cluttered: the rail rendered as a full-width static block *above* the conversation (`order: -1`), so the map pushed every message down and re-rendered over them on every turn. The ask: a cleaner map that matches the «دفترِ نسخ» sheet, one that stays attached to the conversation on the phone — compact when closed, fuller when opened.

The T10 contract said the map is "an always open rail beside the chat, never a collapsible details the chat scrolls away". That contract was written for the desktop's wide sheet; on a 390 px phone it is exactly the wrong rule.

## Decision

- **One renderer, two bodies.** `buildMapBody` renders the same server projection into the desktop rail (`#research-rail` → `#research-journey` + `#research-state-body`, ids unchanged) and the mobile compass (`#research-compass` → `#compass-panel` → `#compass-journey` + `#compass-state-body`); CSS picks the visible body per breakpoint. The desktop rail stays always-open — the T10 contract holds where it was written.
- **The rail is redrawn, the anatomy is not.** The five stages become a vertical stepper — dots on a connecting line, gold for done, lapis for the running one — replacing the `nowrap` horizontal strip. The rows become a headed card (destination + research question with its version chip, gold rule on lapis ground), then one section per row with each list standing as its own line; nothing joins into unbreakable strings any more. The counts leave the prose and become a stat row. `overflow-wrap: anywhere` and `min-width: 0` everywhere.
- **The compass rides the composer dock.** Inside `.composer-dock` (already sticky at the bottom), one thin bar: five mini dots, the running stage's name, the destination in an ellipsis, a chevron. Collapsed it costs the conversation one strip; the chevron raises the full map as a bottom sheet (max-height 60dvh, own scroll), `aria-expanded` honest, Escape puts it home. The map never pushes the chat again.
- **The clamp remembers the reader.** Long lists show three lines with a «نمایش بیشتر (N)» / «نمایش کمتر» toggle; the operator's expansions live in a module-level Set, so the two-second poll's re-renders never collapse a list under a reading eye. The rail keeps its scroll position across re-renders the same way.
- **The poll churn is bounded where it was cheap.** The rail's re-render restores its scroll; the compass panel's open/closed state is explicit DOM state, not re-derived.

## Consequences

- The contract test's shape updates with the change: the ids stand, the `<details>` ban stands (the compass panel is not a `<details>`, and it is the mobile body — the desktop rail it defers to is still never a collapsible), and the compass ids join the pins.
- Two NEW draft strings («نمایش بیشتر» / «نمایش کمتر») joined the shell's draft table and CONTEXT.md's draft roster for PM sign-off; every other visible string — the map title, the stages, the row labels, the closed note — reuses approved roster text.
- The mobile map is one strip tall by default; the operator who wants the full route pays one tap, not a lost conversation.
