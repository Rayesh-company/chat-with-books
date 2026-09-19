"""The Admin console's server-rendered page (T25, GitLab #26): the
«میز مدیریت» — the Admin's read mirror of the system (ADR-0013). One
self-contained RTL HTML document, every style inline, no framework and
no dependency (report.py's shape), rendered by ONE pure function:
`console_html` takes the caller's snapshot and returns the page — no
store, no registry, no wall clock of its own, so a test drives it with
plain dicts and pins the exact markup.

The snapshot's four tables render in the ticket's order: the Accounts
(email, role, attached phone, Balance in Toman, today's spend, chats
today against the daily limit), the live research turns (id, phone,
state, elapsed), the recently settled turns with their FAILURES called
out in Farsi (a failure is visible, never silent — the Diagnoser's
rule at system scale), and the audit log's newest rows. The caller
(serve.py's GET /admin) gathers every table — the accounts+ledger+quota
snapshot, the registry read under RESEARCH_REGISTRY_LOCK — and this
module only renders what it is handed: the console never mutates the
stores, never touches the registry, and this round has no write path
at all (the top-up and issuance UI is T26, GitLab #28).

The title is the DRAFT platform name «میز مدیریت» (the draft roster in
CONTEXT.md, pending PM approval, 2026-09-19) — it lives in the ONE
constant CONSOLE_TITLE and nowhere else, so the PM's approval is a
one-line rename, the RESEARCH_REPORT_CHIP precedent."""

from __future__ import annotations

import html

# The DRAFT platform name (CONTEXT.md's draft roster, 2026-09-19):
# pending PM approval — a rename is this line and nothing else.
CONSOLE_TITLE = "میز مدیریت"

# The turn states as the Farsi page names them (the poll's state words
# stay the wire vocabulary; only the console labels them).
_TURN_STATE_LABELS = {
    "classifying": "برداشت گفتگو",
    "planning": "برنامه‌ریزی پژوهش",
    "searching": "جست‌وجوی شواهد",
    "mapping": "نگاه به زمینۀ پرسش",
    "writing": "نوشتن پاسخ",
    "reviewing": "بازبینی پایانی",
    "guiding": "پرسش راهنما",
    "done": "تمام شد",
    "failed": "ناتمام ماند",
    "aborted": "لغو شد",
}

# The audit actions' Farsi labels; an unknown action renders as its own
# wire name — a new action is never silently unlabelled.
_ACTION_LABELS = {
    "account_created": "ساختن حساب",
}

_ROLE_LABELS = {
    "admin": "مدیر",
    "operator": "اپراتور نشست",
}

# How many settled turns the page renders — the ring behind them is
# wider (research.py's cap), the console is a glance, not an archive.
_SETTLED_RENDER_CAP = 12


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _state_label(state) -> str:
    return _TURN_STATE_LABELS.get(str(state), str(state))


def _action_label(action) -> str:
    return _ACTION_LABELS.get(str(action), str(action))


def _toman(value) -> str:
    return f"{int(value):,}"


def _table(headers, rows_html) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    if not rows_html:
        body = f'<tr><td colspan="{len(headers)}" class="empty">—</td></tr>'
    else:
        body = "".join(rows_html)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _accounts_table(rows) -> str:
    """The Accounts mirror: one row per Account — the Balance (اعتبار),
    yesterday's and today's spend off the ledger, and the day's chats
    against the daily limit, each read by the caller from its own
    store."""
    rows_html = []
    for row in rows:
        phone = row.get("phone") or ""
        role = _ROLE_LABELS.get(str(row.get("role", "")), str(row.get("role", "")))
        chats = int(row.get("chats_today", 0))
        limit = int(row.get("quota_limit", 5))
        rows_html.append(
            "<tr>"
            f"<td class=\"email\">{_esc(row.get('email', ''))}</td>"
            f"<td>{_esc(role)}</td>"
            f"<td>{_esc(phone) if phone else '—'}</td>"
            f"<td class=\"num\">{_toman(row.get('balance_toman', 0))}</td>"
            f"<td class=\"num\">{_toman(row.get('yesterday_spend_toman', 0))}</td>"
            f"<td class=\"num\">{_toman(row.get('today_spend_toman', 0))}</td>"
            f"<td class=\"num\">{chats} از {limit}</td>"
            "</tr>"
        )
    return _table(
        (
            "حساب",
            "نقش",
            "شمارۀ پیوند‌خورده",
            "اعتبار (تومان)",
            "خرج دیروز (تومان)",
            "خرج امروز (تومان)",
            "گفتگوهای امروز (سقف روزانه)",
        ),
        rows_html,
    )


def _live_table(turns) -> str:
    rows_html = []
    for turn in turns:
        elapsed = float(turn.get("elapsed", 0))
        rows_html.append(
            "<tr>"
            f"<td class=\"mono\">{_esc(turn.get('id', ''))}</td>"
            f"<td class=\"mono\">{_esc(turn.get('phone', ''))}</td>"
            f"<td>{_esc(_state_label(turn.get('state', '')))}</td>"
            f"<td class=\"num\">{elapsed:,.0f} ثانیه</td>"
            "</tr>"
        )
    return _table(
        ("شناسۀ پیام", "شماره", "وضعیت", "زمان سپری‌شده"),
        rows_html,
    )


def _settled_table(turns) -> str:
    """The recently settled turns, newest first as handed in — a FAILED
    turn is called out with the alarm row and its recorded Farsi detail
    (the registry's failure surface), never folded into silence."""
    rows_html = []
    failures = 0
    for turn in turns[:_SETTLED_RENDER_CAP]:
        state = str(turn.get("state", ""))
        failed = state == "failed"
        if failed:
            failures += 1
        detail = turn.get("error") or ""
        detail_cell = (
            f"<td class=\"detail\">{_esc(detail)}</td>" if detail else "<td>—</td>"
        )
        cls = ' class="failed-row"' if failed else ""
        rows_html.append(
            f"<tr{cls}>"
            f"<td class=\"mono\">{_esc(turn.get('id', ''))}</td>"
            f"<td class=\"mono\">{_esc(turn.get('phone', ''))}</td>"
            f"<td>{'<strong>' if failed else ''}{_esc(_state_label(state))}"
            f"{'</strong>' if failed else ''}</td>"
            f"{detail_cell}"
            "</tr>"
        )
    table = _table(
        ("شناسۀ پیام", "شماره", "وضعیت", "جزئیات"),
        rows_html,
    )
    if failures:
        table = (
            f'<p class="failure-count">{failures} پیام پژوهش ناتمام ماند؛ '
            "جزئیات هر ناکامی در همین جدول است.</p>" + table
        )
    return table


def _audit_table(rows) -> str:
    rows_html = []
    for row in rows:
        rows_html.append(
            "<tr>"
            f"<td class=\"mono\">{_esc(row.get('ts', ''))}</td>"
            f"<td>{_esc(_action_label(row.get('action', '')))}</td>"
            f"<td class=\"email\">{_esc(row.get('actor_email') or '—')}</td>"
            f"<td>{_esc(row.get('detail') or '—')}</td>"
            "</tr>"
        )
    return _table(("زمان", "رخداد", "مدیر", "جزئیات"), rows_html)


def console_html(
    accounts_rows,
    live_turns,
    settled_turns,
    audit_rows,
    quota_limit,
    generated: str = "",
) -> str:
    """The «میز مدیریت» page over its inputs — pure, so the tests drive
    it with plain dicts. `accounts_rows` carries one dict per Account
    (email, role, phone, balance_toman, yesterday_spend_toman,
    today_spend_toman, chats_today, quota_limit per row overrides the
    page default); `live_turns` the
    registry snapshot (id, phone, state, elapsed); `settled_turns` the
    recent-settled ring, newest first (id, phone, state, error);
    `audit_rows` the log's newest rows, newest first (ts, action,
    actor_email, detail); `quota_limit` the daily chat limit the
    accounts table counts against; `generated` the caller's timestamp
    label (the function itself reads no clock). Renders in the fixed
    order: Accounts, live turns, settled turns, the audit."""
    limit = int(quota_limit)
    rows = [
        {**row, "quota_limit": row.get("quota_limit", limit)} for row in accounts_rows
    ]
    stamp = f" · {_esc(generated)}" if generated else ""
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(CONSOLE_TITLE)}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="page">
<header class="console">
  <h1>{_esc(CONSOLE_TITLE)}
      <span class="draft" title="نام پیش‌نویس؛ منتظر تأیید مدیر محصول">پیش‌نویس</span></h1>
  <div class="meta">آینۀ سیستم{stamp} · هر کنش مدیر در دفتر رخدادها ثبت می‌شود؛
  این صفحه چیزی را بی‌ثبت تغییر نمی‌دهد.</div>
</header>

<h2>حساب‌ها</h2>
{_accounts_table(rows)}

<h2>پیام‌های پژوهش در جریان</h2>
{_live_table(live_turns)}

<h2>پیام‌های تازه‌تمام‌شده</h2>
{_settled_table(settled_turns)}

<h2>دفتر رخدادها <span class="append-only">فقط افزودنی</span></h2>
{_audit_table(audit_rows)}

<footer class="note">این صفحه فقط می‌خواند: حساب‌ها، خرج امروز و گفتگوهای امروز
از دفترهای خودشان، پیام‌های پژوهش از رجیستری زنده، و رخدادها از دفتر فقط‌افزودنی.
ساختن حساب و شارژ اعتبار از همین میز، در نوبت بعدی می‌آید.</footer>
</div>
</body>
</html>"""


_STYLE = """
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { font-family: Vazirmatn, Tahoma, "Segoe UI", sans-serif; margin: 0;
         background: #f2f0eb; color: #26211d; line-height: 1.9; }
  .page { max-width: 1080px; margin: 0 auto; padding: 28px 32px 56px; }
  header.console { border-bottom: 3px solid #8a3d20; padding-bottom: 10px;
                   margin-bottom: 18px; }
  header.console h1 { font-size: 26px; margin: 0 0 4px; color: #8a3d20; }
  header.console .meta { font-size: 13px; color: #75695f; }
  .draft { font-size: 12px; background: #f3e2c7; color: #7a5a20;
           border: 1px dashed #b4552d; border-radius: 8px;
           padding: 1px 10px; vertical-align: middle; }
  h2 { font-size: 18px; color: #6e3418; border-right: 4px solid #b4552d;
       padding-right: 10px; margin: 26px 0 8px; }
  .append-only { font-size: 12px; background: #e8efe6; color: #2f5d3a;
                 border-radius: 8px; padding: 1px 8px; }
  table { width: 100%; border-collapse: collapse; background: #fff;
          border: 1px solid #e0d8ca; border-radius: 8px; font-size: 14px; }
  th { background: #efe8dc; color: #5c4a3d; font-weight: 600;
       padding: 6px 10px; text-align: right; border-bottom: 2px solid #e0d8ca; }
  td { padding: 5px 10px; border-bottom: 1px dashed #ece5d8;
       vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  td.num { direction: ltr; text-align: right; }
  td.mono { direction: ltr; text-align: right;
            font-family: "Courier New", monospace; font-size: 12.5px; }
  td.email { direction: ltr; text-align: right; }
  td.empty { text-align: center; color: #9a8d80; }
  td.detail { color: #8a2b20; }
  tr.failed-row { background: #fdf1ee; }
  tr.failed-row td { border-bottom: 1px solid #f0d5cd; }
  .failure-count { font-size: 13px; color: #8a2b20; margin: 4px 0 8px; }
  footer.note { margin-top: 30px; font-size: 12.5px; color: #75695f;
                border-top: 1px solid #e0d8ca; padding-top: 10px; }
"""
