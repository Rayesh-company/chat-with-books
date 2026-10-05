"""The manager's ticket inbox (the ticket-system map, 2026-10-05):
«صندوق تیکت‌ها» — the console's sibling server-rendered page. The
console stays the system mirror; this page is the one conversation
surface the manager works: the queue filtered by category, status, and
time (the operator's «بر اساس دسته و زمان»), the ticket's own
snapshot first (the answer AS THE USER SAW IT, durable past any
session deletion), the read-only transcript one link deeper for chat
sessions, the reply form walking the ticket to «پاسخ داده شد».

Pure functions over handed-in rows — the tests drive them with plain
dicts, the same contract console_html carries. The words are the
map's DRAFT roster: «صندوق تیکت‌ها», «پاسخ», «بستن تیکت»,
«بازگشایی» — a PM rename is a constant, nothing else."""

from __future__ import annotations

import html

try:
    from ui.ticket_store import CATEGORY_LABELS, STATUS_LABELS
except ImportError:  # the container runs this file as a script beside the modules
    from ticket_store import CATEGORY_LABELS, STATUS_LABELS

# The DRAFT platform names (CONTEXT.md's roster discipline): pending
# PM approval — a rename is this block and nothing else.
INBOX_TITLE = "صندوق تیکت‌ها"
TRANSCRIPT_TITLE = "دیدن کل نشست"
REPLY_BTN = "پاسخ"
CLOSE_BTN = "بستن تیکت"
EDIT_BTN = "ویرایش پاسخ"
BACK_TO_INBOX = "← صندوق تیکت‌ها"
UNREAD_TAG = "پاسخِ شما هنوز خوانده نشده"
NO_REPLIES_NOTE = (
    "هنوز پاسخی نیست — نخستین پاسخِ شما وضعیت تیکت را"
    " «پاسخ داده شد» می‌کند."
)
CLOSED_NOTE = "بسته‌شده — کاربر می‌تواند تا ۷ روز بازگشایی کند."

# The refused writes' Farsi notes, keyed by the store's refusal codes
# the redirects carry (user text never rides a URL into this page).
_ERROR_NOTES = {
    "bad_body": "متن پاسخ را بنویسید؛ پاسخ خالی نیست.",
    "not_found": "تیکت پیدا نشد.",
    "closed": "این تیکت بسته است.",
    "bad_category": "دسته نامعتبر است.",
    "short_body": "توضیح کوتاه است.",
    "forbidden": "این پاسخ از شما نیست؛ فقط پاسخ خودتان ویرایش می‌شود.",
}

_DAYS_OPTIONS = (
    ("", "همهٔ زمان‌ها"),
    ("1", "۲۴ ساعت گذشته"),
    ("7", "۷ روز گذشته"),
    ("30", "۳۰ روز گذشته"),
)

_MODE_LABELS = {"chat": "چت عادی", "research": "پژوهش"}


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _fa_digits(text) -> str:
    return str(text).translate(
        str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬")
    )


def _toman(value) -> str:
    return _fa_digits(f"{int(value):,}") if value is not None else "—"


def _status_chip(status: str) -> str:
    return (
        f'<span class="st st-{_esc(status)}">'
        f"{_esc(STATUS_LABELS.get(status, status))}</span>"
    )


def _filters(category: str, status: str, days: str) -> str:
    cat_options = "".join(
        f'<option value="{_esc(key)}"{" selected" if key == category else ""}>'
        f"{_esc(CATEGORY_LABELS[key])}</option>"
        for key in CATEGORY_LABELS
    )
    st_options = "".join(
        f'<option value="{_esc(key)}"{" selected" if key == status else ""}>'
        f"{_esc(STATUS_LABELS[key])}</option>"
        for key in STATUS_LABELS
    )
    day_options = "".join(
        f'<option value="{_esc(key)}"{" selected" if key == days else ""}>'
        f"{_esc(label)}</option>"
        for key, label in _DAYS_OPTIONS
    )
    return f"""
<form class="filters" method="get" action="/admin/tickets">
  <label>دسته <select name="category">
    <option value="">همه</option>{cat_options}</select></label>
  <label>وضعیت <select name="status">
    <option value="">همه</option>{st_options}</select></label>
  <label>بازه <select name="days">{day_options}</select></label>
  <button type="submit">نمایش</button>
</form>"""


def _inbox_rows(tickets) -> str:
    rows = []
    for ticket in tickets:
        unread = (
            '<div class="unread">خوانده‌نشده</div>'
            if ticket.get("unread_for_user")
            else ""
        )
        rows.append(
            f"<tr>"
            f'<td class="mono">#{_fa_digits(ticket["id"])}</td>'
            f"<td>{_esc(ticket['created_at'] or '')}</td>"
            f'<td><span class="cat">{_esc(ticket["category_label"])}</span></td>'
            f"<td>{_status_chip(ticket['status'])}{unread}</td>"
            f'<td class="email">{_esc(ticket["account"])}</td>'
            f'<td class="snip">{_esc(ticket.get("snippet") or "")}</td>'
            f'<td><a href="/admin/tickets/{ticket["id"]}">جزئیات</a></td>'
            "</tr>"
        )
    if not rows:
        rows = [
            '<tr><td colspan="7" class="empty">— تیکتی در این فیلتر نیست —</td></tr>'
        ]
    return "".join(rows)


def admin_inbox_html(tickets, category="", status="", days="",
                     error_code: str = "") -> str:
    """The queue page: filters in the querystring (category / status /
    days), newest first, one row per ticket with the unread callout on
    «پاسخ داده شد» rows the user has not opened since."""
    error_note = _ERROR_NOTES.get(str(error_code or ""), "")
    error = (
        f'<p class="write-error" role="alert">{_esc(error_note)}</p>'
        if error_note
        else ""
    )
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(INBOX_TITLE)}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="page">
<header class="console">
  <h1>{_esc(INBOX_TITLE)}
      <span class="draft" title="نام پیش‌نویس؛ منتظر تأیید مدیر محصول">پیش‌نویس</span></h1>
  <div class="meta"><a href="/admin">میز مدیریت</a> ← صندوق تیکت‌ها ·
  هر پاسخ و بستن در دفتر رخدادها ثبت می‌شود.</div>
</header>
{error}
{_filters(str(category or ""), str(status or ""), str(days or ""))}
<div class="tablewrap"><table>
<thead><tr><th>#</th><th>زمان</th><th>دسته</th><th>وضعیت</th>
<th>حساب</th><th>گزیدهٔ گزارش</th><th></th></tr></thead>
<tbody>{_inbox_rows(tickets)}</tbody></table></div>
<footer class="note">این صفحه گزارش‌های کاربران را مرور می‌کند؛ متن پاسخِ
اول کاربر همان لحظهٔ ثبت ثابت مانده است (اسنپ‌شات) و حتی با حذف نشست
پایان نمی‌یابد.</footer>
</div>
</body>
</html>"""


def _snapshot_card(ticket) -> str:
    snapshot = ticket.get("snapshot") or {}
    extra = snapshot.get("extra") or {}
    refs = extra.get("references") or []
    refs_note = (
        "".join(
            f'<li class="mono">{_esc(ref)}</li>' for ref in refs[:6]
        )
        if refs
        else ""
    )
    refs_block = (
        f'<div class="refs"><div>ارجاع‌های پاسخ:</div><ul>{refs_note}</ul></div>'
        if refs_note
        else ""
    )
    image = (
        f'<a href="/tickets/{ticket["id"]}/file" target="_blank">'
        f'<img class="attach" src="/tickets/{ticket["id"]}/file" '
        f'alt="تصویر پیوست کاربر" loading="lazy"></a>'
        if ticket.get("has_image")
        else ""
    )
    model = (
        f'<div><span class="k">مدل: </span><span class="mono">{_esc(snapshot["model"])}</span></div>'
        if snapshot.get("model")
        else ""
    )
    return f"""
<div class="snapshot">
  <div><span class="k">پرسش: </span>{_esc(snapshot.get("question") or "—")}</div>
  <div class="ans"><span class="k">پاسخِ ثبت‌شده: </span>{_esc(snapshot.get("answer") or "—")}</div>
  <div class="meta-row">
    <div><span class="k">کتاب: </span>{_esc(snapshot.get("book") or "—")}</div>
    <div><span class="k">حالت: </span>{_esc(_MODE_LABELS.get(snapshot.get("mode"), snapshot.get("mode") or "—"))}</div>
    <div><span class="k">هزینهٔ نوبت: </span>{_toman(ticket.get("turn_cost_toman"))} تومان</div>
    {model}
  </div>
  {refs_block}
  {image}
  <div class="ids mono">session_id={_esc(ticket["session_id"])} ·
  message_id={_esc(ticket["message_id"])} ·
  ask_key={_esc(ticket.get("ask_key") or "—")}</div>
</div>"""


def _thread(ticket) -> str:
    rows = []
    for reply in ticket.get("replies") or []:
        who = "کاربر" if reply["author_role"] == "user" else "پاسخ مدیر"
        edited = (
            f' <span class="edited">(ویرایش‌شده {_esc(reply["edited_at"])})</span>'
            if reply.get("edited_at")
            else ""
        )
        read = (
            ""
            if reply["author_role"] == "user"
            else (
                f' <span class="readnote">خوانده‌شده</span>'
                if reply.get("read_at")
                else f' <span class="readnote unread">خوانده‌نشده</span>'
            )
        )
        edit_form = ""
        if reply["author_role"] == "admin":
            edit_form = f"""
<details class="edit"><summary>{_esc(EDIT_BTN)}</summary>
<form action="/admin/tickets/edit-reply" method="post">
  <input type="hidden" name="reply_id" value="{reply['id']}">
  <textarea name="body" required>{_esc(reply["body"])}</textarea>
  <button type="submit">ذخیرهٔ ویرایش</button>
</form></details>"""
        rows.append(
            f'<div class="msg {reply["author_role"]}">'
            f'<div class="who">{_esc(who)} — {_esc(reply["created_at"])}'
            f"{edited}{read}</div>{_esc(reply['body'])}{edit_form}</div>"
        )
    return "".join(rows)


def admin_ticket_html(ticket, error_code: str = "") -> str:
    """The ticket's working view: snapshot first, transcript one link
    deeper, the two-way thread, the reply form, the close button — and
    the edit-own-reply disclosure on each of the manager's rows."""
    error_note = _ERROR_NOTES.get(str(error_code or ""), "")
    error = (
        f'<p class="write-error" role="alert">{_esc(error_note)}</p>'
        if error_note
        else ""
    )
    unread_banner = (
        f'<div class="unread-banner">{_esc(UNREAD_TAG)}</div>'
        if ticket.get("unread_for_user")
        else ""
    )
    if ticket["status"] == "closed":
        actions = f'<div class="closed-note">{_esc(CLOSED_NOTE)}</div>'
    else:
        actions = f"""
<form action="/admin/tickets/reply" method="post" class="replyform">
  <input type="hidden" name="ticket_id" value="{ticket['id']}">
  <textarea name="body" required
    placeholder="پاسخ شما به کاربر…"></textarea>
  <div class="formrow">
    <button type="submit" class="primary">{_esc(REPLY_BTN)}</button>
  </div>
</form>
<form action="/admin/tickets/close" method="post" class="closeform">
  <input type="hidden" name="ticket_id" value="{ticket['id']}">
  <button type="submit">{_esc(CLOSE_BTN)}</button>
</form>"""
    no_replies = (
        f'<div class="noreplies">{_esc(NO_REPLIES_NOTE)}</div>'
        if not (ticket.get("replies") or [])
        else ""
    )
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(INBOX_TITLE)} · #{_fa_digits(ticket["id"])}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="page">
<header class="console">
  <h1>{_esc(INBOX_TITLE)} · تیکت #{_fa_digits(ticket["id"])}
      <span class="draft" title="نام پیش‌نویس؛ منتظر تأیید مدیر محصول">پیش‌نویس</span></h1>
  <div class="meta"><a href="/admin/tickets">{_esc(BACK_TO_INBOX)}</a> ·
  <a href="/admin/tickets/{ticket["id"]}/transcript">{_esc(TRANSCRIPT_TITLE)}</a></div>
</header>
{error}
<div class="detailhead">
  <span class="cat">{_esc(ticket["category_label"])}</span>
  {_status_chip(ticket["status"])}
  <span class="who">{_esc(ticket["account"])} ·
  {_esc(ticket["created_at"])} ·
  #{_fa_digits(ticket["id"])}</span>
</div>
{unread_banner}
<h2>آنچه کاربر دید (اسنپ‌شات)</h2>
{_snapshot_card(ticket)}
<h2>گفت‌وگو</h2>
{no_replies}{_thread(ticket)}
<h2>پاسخ شما</h2>
{actions}
<footer class="note">ویرایش فقط برای پاسخ خودتان؛ هیچ چیزی از این صفحه
حذف نمی‌شود — دفتر رخدارها صادق بماند.</footer>
</div>
</body>
</html>"""


def admin_transcript_html(ticket, session) -> str:
    """The read-only whole-session view behind the snapshot: every turn
    the Session store holds, user asks and settled answers alike — a
    page, not a pipeline: nothing here re-runs anything. A session the
    user has since deleted renders the honest absence over the
    snapshot that outlived it."""
    if session is None:
        body = (
            '<div class="noreplies">این نشست پس از ثبت تیکت حذف شده است؛'
            " اسنپ‌شاتِ تیکت همهٔ چیزی است که مانده.</div>"
        )
    else:
        rows = []
        for message in session.get("messages") or []:
            payload = message.get("payload") or {}
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                parts = []
                for block in payload.get("blocks") or []:
                    if isinstance(block, dict) and block.get("type") == "paragraph":
                        for part in block.get("parts") or []:
                            if isinstance(part, dict) and isinstance(
                                part.get("text"), str
                            ):
                                parts.append(part["text"].strip())
                text = " ".join(p for p in parts if p)
            cls = "user" if message.get("role") == "user" else "admin"
            label = "کاربر" if cls == "user" else "کتاب"
            rows.append(
                f'<div class="msg {cls}"><div class="who">{_esc(label)} —'
                f" {_esc(message.get('ts') or '')}</div>{_esc(text[:2000])}</div>"
            )
        body = "".join(rows) or '<div class="noreplies">—</div>'
    session_note = (
        f"نشست #{_fa_digits(ticket['session_id'])}"
        if session is None
        else (
            f"نشست «{_esc(session.get('title') or '')}» ·"
            f" کتاب {_esc(session.get('book') or '—')}"
        )
    )
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(TRANSCRIPT_TITLE)}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="page narrow">
<header class="console">
  <h1>{_esc(TRANSCRIPT_TITLE)}</h1>
  <div class="meta"><a href="/admin/tickets/{ticket["id"]}">← تیکت
  #{_fa_digits(ticket["id"])}</a> · {session_note} · فقط خواندنی</div>
</header>
{body}
</div>
</body>
</html>"""


_STYLE = """
  /* The console's own approved world (v2 «دفترِ نسخ») — the sibling
     page wears the identical desk so the manager reads one surface. */
  @font-face { font-family: "Vazirmatn"; src: url("/vendor/fonts/Vazirmatn-Regular.woff2") format("woff2");
               font-weight: 400; font-style: normal; font-display: swap; }
  @font-face { font-family: "Vazirmatn"; src: url("/vendor/fonts/Vazirmatn-Bold.woff2") format("woff2");
               font-weight: 700; font-style: normal; font-display: swap; }
  :root {
    color-scheme: light;
    --desk: #d8cbae; --surface: #f3ead6; --raised: #faf4e6; --rule: #cdbf9f;
    --ink: #2e2417; --mute: #6d5e48; --lapis: #1f4a78; --lapis-deep: #16375a;
    --gold: #a67c2e; --gold-soft: rgba(166, 124, 46, 0.16);
    --ok: #3e6b4f; --bad: #9c3529; --bad-soft: rgba(156, 53, 41, 0.08);
    --shadow: rgba(46, 36, 24, 0.22);
  }
  * { box-sizing: border-box; }
  body { font-family: Vazirmatn, Tahoma, "Segoe UI", sans-serif; margin: 0;
         background: var(--desk); color: var(--ink); line-height: 1.9; }
  :focus-visible { outline: 2px solid var(--gold); outline-offset: 2px; }
  .page { max-width: 1080px; margin: 0 auto; padding: 28px 32px 56px; }
  .page.narrow { max-width: 760px; }
  header.console { border-bottom: 3px solid var(--gold); padding-bottom: 10px;
                   margin-bottom: 18px; }
  header.console h1 { font-size: 24px; margin: 0 0 4px; color: var(--lapis); }
  header.console .meta { font-size: 13px; color: var(--mute); }
  header.console .meta a { color: var(--lapis); }
  .draft { font-size: 12px; background: var(--gold-soft); color: var(--gold);
           border: 1px dashed var(--gold); border-radius: 8px;
           padding: 1px 10px; vertical-align: middle; }
  h2 { font-size: 18px; color: var(--lapis-deep); border-right: 4px solid var(--gold);
       padding-right: 10px; margin: 26px 0 8px; }
  a { color: var(--lapis); }
  .tablewrap { overflow-x: auto; -webkit-overflow-scrolling: touch;
               border-radius: 8px; }
  table { width: 100%; border-collapse: collapse; background: var(--raised);
          border: 1px solid var(--rule); border-radius: 8px; font-size: 14px; }
  th { background: var(--surface); color: var(--lapis-deep); font-weight: 700;
       padding: 6px 10px; text-align: right; border-bottom: 2px solid var(--rule); }
  td { padding: 5px 10px; border-bottom: 1px dashed rgba(205, 191, 159, 0.55);
       vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  td.mono, .mono { direction: ltr; text-align: right;
                   font-family: Consolas, Menlo, monospace; font-size: 12px; }
  td.email { direction: ltr; text-align: right; font-size: 12.5px; }
  td.snip { color: var(--mute); font-size: 13px; max-width: 320px; }
  td .empty, .empty { color: var(--mute); text-align: center; }
  .cat { font-size: 12px; background: rgba(31, 74, 120, 0.08); color: var(--lapis);
         border-radius: 8px; padding: 1px 8px; white-space: nowrap; }
  .st { font-size: 12px; border-radius: 8px; padding: 1px 8px;
        border: 1px solid var(--rule); color: var(--mute); white-space: nowrap; }
  .st-open { color: var(--bad); border-color: var(--bad); background: var(--bad-soft); }
  .st-answered { color: var(--gold); border-color: var(--gold); background: var(--gold-soft); }
  .st-closed { color: var(--ok); border-color: var(--ok); }
  .unread { font-size: 11.5px; color: var(--bad); font-weight: 700; }
  .unread-banner { background: var(--gold-soft); border: 1px dashed var(--gold);
                   color: var(--gold); border-radius: 8px; padding: 4px 12px;
                   margin: 10px 0; font-size: 13px; }
  .filters { display: flex; gap: 14px; flex-wrap: wrap; align-items: center;
             background: var(--surface); border: 1px solid var(--rule);
             border-radius: 8px; padding: 8px 12px; margin-bottom: 14px;
             font-size: 13.5px; }
  .filters select { font: inherit; background: var(--raised); color: var(--ink);
                    border: 1px solid var(--rule); border-radius: 8px;
                    padding: 2px 8px; }
  .filters button { font: inherit; background: var(--lapis); color: var(--surface);
                    border: none; border-radius: 8px; padding: 4px 16px;
                    cursor: pointer; }
  .detailhead { display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
                background: var(--surface); border: 1px solid var(--rule);
                border-radius: 8px; padding: 8px 12px; }
  .detailhead .who { font-size: 12.5px; color: var(--mute); direction: ltr; }
  .snapshot { background: var(--raised); border: 1px dashed var(--rule);
              border-radius: 8px; padding: 12px 14px; font-size: 14px; }
  .snapshot .k { color: var(--mute); font-size: 12.5px; }
  .snapshot .ans { border-right: 3px solid var(--gold); padding-right: 10px;
                   margin: 8px 0; }
  .snapshot .meta-row { display: flex; gap: 18px; flex-wrap: wrap;
                        font-size: 13px; }
  .snapshot .ids { margin-top: 8px; color: var(--mute); }
  .refs { font-size: 13px; margin-top: 6px; }
  .refs ul { margin: 4px 0 0; padding-inline-start: 18px; }
  .attach { max-width: 320px; max-height: 320px; border-radius: 8px;
            border: 1px solid var(--rule); display: block; margin-top: 10px; }
  .msg { border-radius: 10px; padding: 8px 12px; margin-bottom: 8px;
         border: 1px solid var(--rule); font-size: 14px; white-space: pre-wrap; }
  .msg.user { background: rgba(31, 74, 120, 0.08); }
  .msg.admin { background: var(--gold-soft); border-color: var(--gold); }
  .msg .who { font-size: 11.5px; color: var(--mute); margin-bottom: 2px; }
  .msg .who { direction: rtl; }
  .edited { color: var(--mute); }
  .readnote { color: var(--ok); }
  .readnote.unread { color: var(--bad); font-weight: 700; }
  .noreplies { color: var(--mute); font-size: 13.5px; margin: 8px 0; }
  .closed-note { background: rgba(62, 107, 79, 0.1); color: var(--ok);
                 border-radius: 8px; padding: 6px 12px; font-size: 13.5px; }
  .replyform textarea, .edit textarea { width: 100%; min-height: 90px;
         font: inherit; color: var(--ink); background: var(--raised);
         border: 1px solid var(--rule); border-radius: 8px; padding: 8px 10px; }
  .formrow { margin-top: 8px; }
  button, .closeform button { font: inherit; cursor: pointer;
         background: var(--raised); color: var(--ink);
         border: 1px solid var(--rule); border-radius: 8px; padding: 4px 18px; }
  button.primary { background: var(--lapis); color: var(--surface);
         border-color: var(--lapis); }
  .closeform { margin-top: 12px; }
  .closeform button { color: var(--bad); border-color: var(--bad); }
  .edit { margin-top: 6px; }
  .edit summary { font-size: 12px; color: var(--lapis); cursor: pointer; }
  .edit form { margin-top: 6px; }
  .edit button { padding: 2px 12px; font-size: 12.5px; }
  .write-error { background: var(--bad-soft); border: 1px solid var(--bad);
                 color: var(--bad); border-radius: 8px; padding: 6px 12px; }
  footer.note { margin-top: 30px; font-size: 12.5px; color: var(--mute); }
"""
