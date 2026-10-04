"""The Admin console's server-rendered page (T25, GitLab #26): the
«میز مدیریت» — the Admin's read mirror of the system (ADR-0013). One
self-contained RTL HTML document, every style inline, no framework and
no dependency (report.py's shape), rendered by ONE pure function:
`console_html` takes the caller's snapshot and returns the page — no
store, no registry, no wall clock of its own, so a test drives it with
plain dicts and pins the exact markup.

The snapshot's four tables render in the ticket's order: the Accounts
(email, role, attached phone, Balance in Toman, yesterday's and
today's spend, chats today against the daily limit), the live research
turns (id, phone, state, elapsed), the recently settled turns with
their FAILURES called out in Farsi (a failure is visible, never
silent — the Diagnoser's rule at system scale), and the audit log's
newest rows. Beside the Accounts table ride the page's two WRITE
forms (T26, GitLab #28): issuing an Account (email, password, phone
attach) and topping a Balance up — plain HTML forms posting to
serve.py's admin endpoints, answered by a 303 back to the fresh page,
every action appended to the audit log. The caller (serve.py's GET
/admin) gathers every table — the accounts+ledger+quota snapshot, the
registry read under RESEARCH_REGISTRY_LOCK — and this module only
renders what it is handed: the console never mutates the stores, and
never mutates silently at all.

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
# wire name — a new action is never silently unlabelled. (All DRAFT,
# the roster discipline: the PM's approval renames a line, nothing
# else.)
_ACTION_LABELS = {
    "account_created": "ساختن حساب",
    "balance_topped": "شارژ اعتبار",
    "admin_seeded": "ساختن مدیر نخستین",
    "phone_attached": "پیوند شماره",
    "research_access_changed": "تغییر دسترسی پژوهش",
}

# The refused writes' Farsi notes (T26), keyed by the whitelisted error
# code serve.py redirects back with — user text never rides a URL into
# this page.
_ERROR_NOTES = {
    "bad_body": "ایمیل و گذرواژهٔ حساب را بفرستید (گذرواژه خالی نباشد).",
    "bad_phone": "شمارهٔ تلفن همراه را وارد کنید.",
    "email_taken": "این ایمیل پیش‌تر حساب گرفته است.",
    "bad_amount": "مقدار شارژ را به تومان و مثبت وارد کنید.",
    "unknown_account": "حسابی با این ایمیل نیست.",
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


_FA_DIGITS = str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬")


def _fa_digits(text) -> str:
    """Persian digits with the Persian thousands separator — the shell's
    own fa-IR policy (impeccable critique, 2026-09-28): the operator's
    two surfaces speak one digit language."""
    return str(text).translate(_FA_DIGITS)


def _toman(value) -> str:
    return _fa_digits(f"{int(value):,}")


def _table(headers, rows_html) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    if not rows_html:
        body = f'<tr><td colspan="{len(headers)}" class="empty">—</td></tr>'
    else:
        body = "".join(rows_html)
    # The scroll wrapper is the phone's answer to a seven-column mirror:
    # the table keeps its readable width and swipes instead of crushing
    # (the mobile audit).
    return (
        '<div class="tablewrap"><table><thead><tr>'
        f"{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _accounts_table(rows) -> str:
    """The Accounts mirror: one row per Account — the Balance (اعتبار),
    yesterday's and today's spend off the ledger, the day's chats
    against the daily limit, and the guest cut's پژوهش column (2026-10-04:
    a guest chats on its Balance but never opens research), each read
    by the caller from its own store."""
    rows_html = []
    for row in rows:
        phone = row.get("phone") or ""
        role = _ROLE_LABELS.get(str(row.get("role", "")), str(row.get("role", "")))
        chats = int(row.get("chats_today", 0))
        limit = int(row.get("quota_limit", 5))
        research = row.get("research_enabled", True)
        research_cell = (
            '<span class="research-off">ندارد</span>'
            if research is False
            else "دارد"
        )
        rows_html.append(
            "<tr>"
            f"<td class=\"email\">{_esc(row.get('email', ''))}</td>"
            f"<td>{_esc(role)}</td>"
            f"<td>{research_cell}</td>"
            f"<td>{_esc(phone) if phone else '—'}</td>"
            f"<td class=\"num\">{_toman(row.get('balance_toman', 0))}</td>"
            f"<td class=\"num\">{_toman(row.get('yesterday_spend_toman', 0))}</td>"
            f"<td class=\"num\">{_toman(row.get('today_spend_toman', 0))}</td>"
            f"<td class=\"num\">{_fa_digits(chats)} از {_fa_digits(limit)}</td>"
            "</tr>"
        )
    return _table(
        (
            "حساب",
            "نقش",
            "پژوهش",
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
            f"<td class=\"email\">{_esc(turn.get('account', ''))}</td>"
            f"<td>{_esc(_state_label(turn.get('state', '')))}</td>"
            f"<td class=\"num\">{_fa_digits(f'{elapsed:,.0f}')} ثانیه</td>"
            "</tr>"
        )
    return _table(
        ("شناسۀ پیام", "حساب", "وضعیت", "زمان سپری‌شده"),
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
            f"<td class=\"email\">{_esc(turn.get('account', ''))}</td>"
            f"<td>{'<strong>' if failed else ''}{_esc(_state_label(state))}"
            f"{'</strong>' if failed else ''}</td>"
            f"{detail_cell}"
            "</tr>"
        )
    table = _table(
        ("شناسۀ پیام", "حساب", "وضعیت", "جزئیات"),
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


def _write_forms(accounts_rows, error_note: str) -> str:
    """The console's write side (T26, GitLab #28; T21's attach joins;
    the guest cut's research flip follows, 2026-10-04): the issuance
    form (with its research checkbox — unchecked issues a guest), the
    top-up form, the phone-attach form, and the research-access form —
    plain HTML forms posting form-encoded bodies to serve.py's admin
    endpoints and getting a 303 back — post, redirect, get, no
    JavaScript and no framework. The select lists come from the same
    rows the mirror table renders (the function stays pure: it renders
    only what it is handed), and a refused write's Farsi note renders
    above the forms when the redirect carried a whitelisted code."""
    options = "".join(
        f'<option value="{_esc(row.get("email", ""))}">'
        f'{_esc(row.get("email", ""))}</option>'
        for row in accounts_rows
    )
    error = (
        f'<p class="write-error" role="alert">{_esc(error_note)}</p>'
        if error_note
        else ""
    )
    return f"""{error}
<div class="writes">
<form action="/admin/accounts" method="post" class="write">
  <h3>ساختن حساب</h3>
  <label>ایمیل <input type="email" name="email" required dir="ltr"></label>
  <label>گذرواژه <input type="password" name="password" required dir="ltr"></label>
  <label>شمارۀ پیوند‌خورده (اختیاری) <input type="text" name="phone" dir="ltr"></label>
  <label class="check"><input type="checkbox" name="research" checked> حالت پژوهش داشته باشد</label>
  <button type="submit">ساختن حساب</button>
</form>
<form action="/admin/topup" method="post" class="write">
  <h3>شارژ اعتبار</h3>
  <label>حساب <select name="email" required>{options}</select></label>
  <label>مقدار (تومان) <input type="number" name="amount" min="1" step="1" required dir="ltr"></label>
  <button type="submit">شارژ</button>
</form>
<form action="/admin/attach" method="post" class="write">
  <h3>پیوند شماره</h3>
  <label>حساب <select name="email" required>{options}</select></label>
  <label>شمارۀ پیوند‌خورده <input type="text" name="phone" dir="ltr"></label>
  <button type="submit">پیوند</button>
</form>
<form action="/admin/research-access" method="post" class="write">
  <h3>دسترسی پژوهش</h3>
  <label>حساب <select name="email" required>{options}</select></label>
  <label>وضعیت <select name="state" required>
    <option value="on">فعال</option>
    <option value="off">غیرفعال</option>
  </select></label>
  <button type="submit">تغییر</button>
</form>
</div>"""


def console_html(
    accounts_rows,
    live_turns,
    settled_turns,
    audit_rows,
    quota_limit,
    generated: str = "",
    error_code: str = "",
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
    label (the function itself reads no clock); `error_code` a
    whitelisted refused-write code (T26) or "". Renders in the fixed
    order: Accounts, the write forms, live turns, settled turns, the
    audit."""
    limit = int(quota_limit)
    rows = [
        {**row, "quota_limit": row.get("quota_limit", limit)} for row in accounts_rows
    ]
    stamp = f" · {_fa_digits(_esc(generated))}" if generated else ""
    error_note = _ERROR_NOTES.get(str(error_code or ""), "")
    writes = _write_forms(accounts_rows, error_note)
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

<h2>نوشتن از همین میز <span class="write-note">هر دو کنش ثبت می‌شوند</span></h2>
{writes}

<h2>پیام‌های پژوهش در جریان</h2>
{_live_table(live_turns)}

<h2>پیام‌های تازه‌تمام‌شده</h2>
{_settled_table(settled_turns)}

<h2>دفتر رخدادها <span class="append-only">فقط افزودنی</span></h2>
{_audit_table(audit_rows)}

<footer class="note">این صفحه فقط می‌خواند — جز ساختن حساب و شارژ اعتبار که
از همین میز انجام می‌شود؛ هر دو کنش در دفتر رخدادها ثبت می‌شوند و بدون ثبت
هیچ‌چیز عوض نمی‌شود.</footer>
</div>
</body>
</html>"""


_STYLE = """
  /* The console wears the shell's approved world (v2 «دفترِ نسخ»,
     the critique's third-identity fix, 2026-09-28): parchment desk,
     surface sheets, lapis headings, gold illumination — the same
     tokens ui/index.html carries, self-hosted Vazirmatn included.
     Light-only by design: the console is an operator's desk mirror. */
  @font-face { font-family: "Vazirmatn"; src: url("/vendor/fonts/Vazirmatn-Regular.woff2") format("woff2");
               font-weight: 400; font-style: normal; font-display: swap; }
  @font-face { font-family: "Vazirmatn"; src: url("/vendor/fonts/Vazirmatn-Bold.woff2") format("woff2");
               font-weight: 700; font-style: normal; font-display: swap; }
  :root {
    color-scheme: light;
    --desk: #d8cbae; --surface: #f3ead6; --raised: #faf4e6; --rule: #cdbf9f;
    --ink: #2e2417; --mute: #6d5e48; --lapis: #1f4a78; --lapis-deep: #16375a;
    --gold: #a67c2e; --gold-soft: rgba(166, 124, 46, 0.16);
    --bad: #9c3529; --bad-soft: rgba(156, 53, 41, 0.08);
    --shadow: rgba(46, 36, 24, 0.22);
  }
  * { box-sizing: border-box; }
  body { font-family: Vazirmatn, Tahoma, "Segoe UI", sans-serif; margin: 0;
         background: var(--desk); color: var(--ink); line-height: 1.9; }
  :focus-visible { outline: 2px solid var(--gold); outline-offset: 2px; }
  .page { max-width: 1080px; margin: 0 auto; padding: 28px 32px 56px; }
  header.console { border-bottom: 3px solid var(--gold); padding-bottom: 10px;
                   margin-bottom: 18px; }
  header.console h1 { font-size: 26px; margin: 0 0 4px; color: var(--lapis); }
  header.console .meta { font-size: 13px; color: var(--mute); }
  .draft { font-size: 12px; background: var(--gold-soft); color: var(--gold);
           border: 1px dashed var(--gold); border-radius: 8px;
           padding: 1px 10px; vertical-align: middle; }
  h2 { font-size: 18px; color: var(--lapis-deep); border-right: 4px solid var(--gold);
       padding-right: 10px; margin: 26px 0 8px; }
  .append-only { font-size: 12px; background: rgba(62, 107, 79, 0.12); color: #3e6b4f;
                 border-radius: 8px; padding: 1px 8px; }
  .tablewrap { overflow-x: auto; -webkit-overflow-scrolling: touch;
               border-radius: 8px; }
  table { width: 100%; border-collapse: collapse; background: var(--raised);
          border: 1px solid var(--rule); border-radius: 8px; font-size: 14px; }
  th { background: var(--surface); color: var(--lapis-deep); font-weight: 700;
       padding: 6px 10px; text-align: right; border-bottom: 2px solid var(--rule); }
  td { padding: 5px 10px; border-bottom: 1px dashed rgba(205, 191, 159, 0.55);
       vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  td.num { direction: ltr; text-align: right; }
  td.mono { direction: ltr; text-align: right;
            font-family: "Courier New", monospace; font-size: 12.5px; }
  td.email { direction: ltr; text-align: right; }
  td.empty { text-align: center; color: var(--mute); }
  td.detail { color: var(--bad); }
  tr.failed-row { background: rgba(156, 53, 41, 0.07); }
  tr.failed-row td { border-bottom: 1px solid rgba(156, 53, 41, 0.18); }
  .failure-count { font-size: 13px; color: var(--bad); margin: 4px 0 8px; }
  .write-note { font-size: 12px; background: var(--gold-soft); color: var(--gold);
                border-radius: 8px; padding: 1px 8px; }
  .write-error { background: rgba(156, 53, 41, 0.07); color: var(--bad);
                 border: 1px solid rgba(156, 53, 41, 0.35); border-radius: 8px;
                 padding: 6px 12px; font-size: 13.5px; margin: 8px 0; }
  .writes { display: flex; gap: 18px; flex-wrap: wrap; }
  form.write { background: var(--raised); border: 1px solid var(--rule);
               border-radius: 8px; padding: 12px 16px 14px; flex: 1 1 300px; }
  form.write h3 { margin: 0 0 10px; font-size: 15px; color: var(--lapis-deep); }
  form.write label { display: block; font-size: 13px; color: var(--mute);
                     margin-bottom: 8px; }
  form.write input, form.write select { display: block; width: 100%;
                     margin-top: 3px; padding: 5px 8px; font-size: 14px;
                     border: 1px solid var(--rule); border-radius: 6px;
                     background: var(--surface); box-sizing: border-box;
                     font-family: inherit; }
  form.write button { margin-top: 4px; background: var(--lapis); color: #fff;
                      border: none; border-radius: 6px; padding: 7px 18px;
                      font-size: 14px; font-family: inherit; cursor: pointer; }
  form.write button:hover { background: var(--lapis-deep); }
  /* The guest cut's mark (2026-10-04): a guest's «ندارد» wears the
     mute, never the bad — a closed door is a fact, not a failure. */
  .research-off { color: var(--mute); }
  label.check { display: flex; align-items: center; gap: 8px; }
  label.check input { width: auto; margin-top: 0; }
  footer.note { margin-top: 30px; font-size: 12.5px; color: var(--mute);
                border-top: 1px solid var(--rule); padding-top: 10px; }
  /* The phone: the page's gutter narrows and the tables keep a
     readable width inside their swipe wrapper, never crushed into
     illegibility (the mobile audit). */
  @media (max-width: 719.98px) {
    .page { padding: 18px 14px 40px; }
    table { font-size: 13px; min-width: 640px; }
    th, td { padding: 5px 8px; }
  }
  /* A finger, not a mouse: roomier targets, and a 16px field font so
     iOS never zooms the form on focus. */
  @media (pointer: coarse) {
    form.write input, form.write select { font-size: 16px; padding: 8px 10px; }
    form.write button { padding: 9px 20px; }
  }
"""
