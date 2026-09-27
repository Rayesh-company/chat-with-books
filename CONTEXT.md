# chat-with-books

Farsi question-answering over a fixed Book set through a prebuilt Cognee agent, for a B2B Customer.

## Language

**Customer**:
A Farsi-speaking professional who needs answers from the Book set. In Phase 2 this is a persona, not a named company; industry and job title are left unspecified.
_Avoid_: Client, user, account, the Session operator

**Stand-in**:
A named human the PM accepts to complete the Phase 2 exit sitting in place of a paying Customer.
_Avoid_: Persona, "a colleague" without a name

**Session operator**:
The person who sits with the product to complete a Session. For the Phase 2 exit check this is the Stand-in.
_Avoid_: User

**Session**:
One sitting of Farsi Q&A against the Book set that can meet the Phase 2 exit checks: answers with Citations, a Research Mode conversation, and a first answer that does not feel too slow. A Session endures under its Account — it is listed and resumable; a new ask opens a new Session instead of erasing the running one.
_Avoid_: Chat, demo (showing the product at an expo is not automatically a Session)

**Research Mode**:
The guided research conversation (حالت پژوهش) of the same Session, started by the Session operator on the same question: a multi-turn chat that maintains the Research state, gathers evidence with its own searches of the Book set, refines the research question through approved proposals, and closes with a citation-backed Brief. A gather may feel slow. It is not a longer first answer, and it is not a new question. (Supersedes the Deep dive, the one-shot study.)
_Avoid_: deep study, wizard, questionnaire (it keeps conversational freedom), COT (the Cognee type is not the domain name)

**Research state**:
The persistent record behind a Research Mode conversation: the versioned research question, scope, sub-questions, evidence ledger, claim ledger, gaps, and decisions. Research-question changes append versions through accepted proposals; they never silently replace.
_Avoid_: chat history, cache, context window

**Journey layer**:
The steering of a Research Mode conversation (ADR-0009): a stage machine moved only by code (orientation, mapping, investigating, synthesizing, drafting), guided questions the agent asks but never answers itself, the research map, and the narrator that opens each operation's reply. Adapted from the Wayfinder skill's map/ticket/fog architecture.
_Avoid_: chatbot flow, wizard steps, state machine UI

**Research skill**:
One bounded capability of the Research Mode engine that the guide invokes when the conversation needs it — gathering, analyzing, probing, section-writing, reviewing — each with a declared purpose, its own limits, and the guard's discipline applied to its output. Skills do the work; the map records what they changed. The development-time agent skills that inspired them are not these.
_Avoid_: tool (a Tool is what a skill uses), command, step, plug-in

**Tool**:
A way of searching the Book set that a Research skill may use (hybrid retrieval, graph completion, and the like). Tools multiply and are chosen per need; the Book set remains the only citable source.
_Avoid_: knowledge base, source, dataset, integration

**Brief plan**:
The proposed section plan of the Research Brief — each section tied to its named open questions and the claims that will support it — landed as a proposal the Session operator accepts or rejects before any writing happens.
_Avoid_: outline, template, table of contents

**Section contract**:
The pre-declared acceptance of one Brief section: which claims (by identity) it must carry, which question it answers, and which scope lines it must not cross. The section is written against it and checked against it.
_Avoid_: prompt, checklist, wish list

**Closing review**:
The final check of a written Research Brief on two axes: traceability (every section traces to claims and evidence, nothing outside scope) and the destination judgment (does the document deliver the destination, or honestly state what the Books cannot establish). Its result lands with accept/revise options.
_Avoid_: audit (the audit inspects the claim ledger, not the Brief), proofread, QA pass

**Host (میزبان)**:
The conversational Research skill: a side answer built by bounded cited reasoning over the Book set — retrieval and graph Tools, at most two hops, every quote through the guard, every citation on a real page — with the evidence ledger open to it. Reasoning the Books cannot support renders as commentary («برداشت», its own visibly distinct block), never as a claim.
_Avoid_: chat mode, free chat, chit-chat

**Diagnoser (تشخیص‌گر)**:
The Research skill that names why an answer failed to satisfy — a starved corpus, the wrong Tool, dropped quotes, a question the Books cannot feed — records the diagnosis, and proposes an adjustment (narrow the question, change Tool, or declare a Gap) instead of silently repeating the previous answer. The standing balance between the Session operator's intent and what the Book set can support.
_Avoid_: fallback, retry, error message

**Research map**:
The visible route of a research session (نقشۀ پژوهش): destination, research question with its version count, the frontier, the named open questions with statuses, the decisions index, the fog, out of scope, and the counts — the artifact both the user and the engine read.
_Avoid_: status strip, dashboard, sidebar

**Guided question**:
One turn where the journey asks the user a single sharp question with option chips and a skip — the skip exists wherever the engine asks, in every stage; the user's answer lands as a decision, and in orientation it names the destination. The agent never answers its own guided question.
_Avoid_: prompt, form field, clarification message

**Frontier**:
The one open question the journey is working now — the oldest pending named question, highlighted on the map and worked by the targeted chips.
_Avoid_: queue head, current task, focus

**Book pick**:
The one Book chosen when a Session is created — ask, phases, widen, and the Research journey all search it. Shown as the Session's Book chip; the ask stays disabled until a pick exists. (Supersedes the per-ask Book selection toggles.)
_Avoid_: book selection toggles, filter, dataset choice

**Evidence fallback**:
The phase-1 safety net (ADR-0011): when the streamed reply carries no Evidence block, ONE pinned reference-on search over the picked Book fetches the citation pool before the sheet admits defeat. The first answer stays the Quote selection.
_Avoid_: retry, re-ask, generic answer

**Chart mode vs Work mode**:
The journey's two modes (ADR-0011): chart edits (question/scope proposals) land only on exploration turns; a working turn — an explicit command or an investigation intent — executes instead of editing the map. A decided proposal cools its kind for two turns.
_Avoid_: approval loop, planning phase

**Fog**:
A question the investigation can see coming but cannot yet state sharply enough to ask (هنوز نامشخص); it graduates into an open question when the journey makes it specifiable — and the fog probe (کاوشگر) makes that graduation earn itself: one bounded search over the oldest unprobed note graduates it on evidence, predicts its Gap early when the Books starve it, and refuses the out-of-scope. Out-of-scope items never graduate.
_Avoid_: backlog, TODO, open item

**Claim ledger**:
The recorded claims of an investigation, each with its code-derived status — direct support, supported synthesis — and the evidence passages behind it. The Brief and the audit read from it.
_Avoid_: summary, notes, model output

**Gap**:
An honest result: a sub-question the Book set cannot feed enough evidence to establish. The system states it plainly and never fills it by invention; the researcher may narrow the question, accept a partial conclusion, or stop.
_Avoid_: failure, error, missing data

**Book set**:
The named, fixed collection of Books the product answers from. This is two Books: طرح کلی اندیشۀ اسلامی در قرآن and انسان ۲۵۰ ساله.
_Avoid_: corpus, library, knowledge base, documents, "some books"

**Book**:
One titled work in the Book set.
_Avoid_: file, PDF, document

**Citation**:
The Book identity plus the exact pages of a quoted passage, shown with an answer. A passage whose pages are unknown cites the Book alone, never an invented page.
_Avoid_: footnote, Evidence (Cognee's block name is not the domain name)

**Quote selection**:
The first answer of an ask: a list of verbatim Book sentences that together answer the question, each shown with its own Citation, with no AI-written text.
_Avoid_: evidence list (the Cognee block name is not the domain name), quote-only answer, fast answer, snippet list

**Quoted answer**:
The woven answer of an ask: paragraphs that each interleave Filler text with embedded verbatim Book sentences and end with the pages they cite. It follows the Quote selection and precedes Research Mode.
_Avoid_: citation paragraph (the superseded paragraph-only design), chat, first answer (the Quote selection is the first answer)

**Widen**:
The «جست‌وجوی بیشتر» operation of an ask (ADR-0010): one broaden call picks at most two adjacent facet queries, the pinned searchers run them once, and only the pool's new passages ride back; the sheet merges and re-answers. Part of the same chat; never counts one.
_Avoid_: search more results, refresh, re-search

**Quoted paragraph**:
A paragraph of a Quoted answer: Filler text with verbatim Book sentences embedded inside it, each sentence highlighted and hoverable for its own Citation (the passage's first page), the paragraph ending with the page range of every passage it quoted. May weave several passages.
_Avoid_: evidence block, snippet, quote-only paragraph

**Filler text**:
The AI-written connective text inside a Quoted paragraph; it claims no pages and is never shown as quoted.
_Avoid_: glue text, preamble, filler paragraph (the superseded standalone-paragraph design)

**Book reader**:
The split-view panel (کتاب‌خوان) that renders the original Book PDFs; every Quote on the sheet clicks through to the passage's actual PDF page with the quoted letters highlighted (ADR-0007).
_Avoid_: PDF viewer chrome (it is a provenance surface, not a generic viewer), book preview

**Account**:
The credential the platform recognizes (ADR-0013): an email and a password, issued by the Admin — never self-created — owning one Balance and every Session run under it. It replaced the honor-system phone gate; the phone number is legacy data attached to it, not an identity.
_Avoid_: user (the glossary does not use the word), profile (the Profile is the Account's own view), registration (Accounts are issued, not registered), phone

**Login**:
The one entry where an email and a password open the platform to an Account. There is no other door.
_Avoid_: signup, sign in page (the product has one Login, not flows), phone gate (retired by ADR-0013)

**Balance (اعتبار)**:
The prepaid Toman amount on an Account that asks and research turns deduct from; at zero the service stops with the honest Farsi note. Only the Admin tops it up.
_Avoid_: credit card, wallet, quota (the daily ask limit is not the Balance; the Balance adds on top of it)

**Tariff (تعرفه)**:
The price table that turns recorded usage into Toman. Held apart from the code so prices change without the code.
_Avoid_: pricing logic, hardcoded price, rate limiter

**Admin (مدیر)**:
The role on one Account that issues every other Account, tops up Balances, and watches the system. The PM holds it.
_Avoid_: superuser, root, operator (the Session operator is the customer side)

**Admin console (میز مدیریت)**:
The Admin's surface: creating Accounts, spend and Balance with top-up, live research turns, failures and diagnoses, quota state — every action appended to an audit log, nothing mutated silently.
_Avoid_: dashboard (the research map's avoided word), admin panel, separate admin app

**Session report (گزارش نشست)**:
The walk-away artifact of a Session: the research question with its versions, the destination, the map summary, the Brief sections with their citations, and the «منابع». The chat transcript is process, not deliverable, and stays out.
_Avoid_: export (the action, not the artifact), PDF, backup, transcript copy

## Persian display names

The approved plain-Persian naming table (ADR-0012): the single source every Research skill, stage, chip, and map row is named from — the Session operator never decodes the UI. Approved by the PM in the ADR-0012 roster and the ticket titles (#7–#14); the sheet renames only what this table names.

**Research skills**:

| Skill | Display name |
|---|---|
| guide (research_exploration) | راهنما |
| gather (active_research) | جست‌وجوگر |
| landscape survey | نقشه‌کش |
| fog probe | کاوشگر |
| synthesize | تحلیل‌گر |
| Brief sections writer | نویسنده |
| Closing review | بازبین |
| claim-ledger audit (evidence_audit) | بازبینِ دفتر ادعاها |
| conversational (chat skills) | میزبان |
| map keeper | نقشه‌بان |
| diagnoser | تشخیص‌گر |

**Journey stages**: نام‌گذاری مقصد، نقشه‌برداری، گردآوری شواهد، تحلیل و جمع‌بندی، نوشتن خلاصه.

**Fixed chips and texts**: gather «شواهد بیشتری از کتاب‌ها پیدا کن» · gather-all «همهٔ پرسش‌های باز را جست‌وجو کن» · synthesize «شواهد را تحلیل و جمع‌بندی کن» · brief «خلاصۀ پژوهش را بنویس» · audit «ادعاها و استنادها را بازبینی کن» · guide «ادامهٔ سفر پژوهش» · skip «فعلاً همین کافی است؛ ادامه بده» · stop «توقف پژوهش» · accept/reject «می‌پذیرم» / «رد می‌کنم» · the diagnoser's adjustment menu (T6) «پرسش را محدودتر کن» / «با روش دیگری جست‌وجو کن» / «همین را شکاف اعلام کن» · the closing review's revise (T9) «بازنویسی بخش‌های ناکام خلاصه» · the map keeper's survey (T12) «نقشه را مرتب کن» · the fog probe (T13) «مه را کاوش کن».

**Map rows**: مقصد، پرسش پژوهش، در حال پرداختن (the frontier)، پرسش‌های باز، تصمیم‌ها، تشخیص‌ها (the diagnoser's named causes)، هنوز نامشخص (the fog)، خارج از دامنه، شمارش.

**State caps (T12)**: the working ledgers — evidence, claims, gaps, decisions — keep their newest entries past their hard caps, so the writer prompts stay bounded in a long session. The map's own rows (open questions, fog) never trim silently: the map keeper (نقشه‌بان) surveys them and proposes each cleanup — a duplicate question, stale fog, a finished question's row — as the operator's decision through the usual accept/reject flow.

**Question statuses**: در انتظار، جست‌وجو شد، شکاف. **Claim statuses**: پشتوانهٔ مستقیم، ترکیب شواهد، شواهد ناکافی.

**Platform names (DRAFT — pending PM approval, 2026-09-19, spec2.md)**: Account «حساب» · Login «ورود» · Balance «اعتبار» · Tariff «تعرفه» · Admin «مدیر» · Admin console «میز مدیریت» · Session report «گزارش نشست» · the report chip «دریافت گزارش نشست». One deliberate exception (T19): the report chip already rides the sheet, draft-marked, its text pinned to one server constant (`ui/research.py`'s `RESEARCH_REPORT_CHIP`) so the PM's approval renames it in one line. None of the other rows render until the PM approves them; then the approved rows join the tables above.

**Platform names (DRAFT — pending PM approval, 2026-09-20, chat-shell redesign)**: Session list «فهرست نشست‌ها» · New Session «نشست تازه». The shell's navigation over an Account's Sessions and its new-Session action; neither renders until the PM approves, and both stay pinned to single constants like the report chip.

**Platform names (DRAFT — pending PM approval, 2026-09-27, ADR-0015 research toggle)**: the research section's empty note «پرسش پژوهشی خود را از همین کامپوزر پایین بپرسید؛ نقشۀ پژوهش کنار گفتگو می‌آید.» — the one NEW string the composer's research toggle added, pinned to `RESEARCH_SECTION_NOTE` in the shell's draft table. The toggle's own label reuses the approved glossary row «حالت پژوهش», and the armed composer's placeholder («پیام پژوهش خود را بنویسید») and send word («فرستادن») reuse the research strings the roster already carries.

**Platform names (DRAFT — pending PM approval, 2026-09-27, ADR-0018 map redraw)**: the clamp toggles «نمایش بیشتر» / «نمایش کمتر» — the two NEW strings the map's two bodies added, pinned to `MAP_MORE_LABEL` / `MAP_LESS_LABEL` in the shell's draft table. Everything else the redraw shows reuses the roster: the map title «نقشۀ پژوهش», the journey stages, the map rows, and the closed conversation's resume note, which mirrors `ui/research.py`'s `RESEARCH_SESSION_CLOSED_DETAIL` verbatim («این گفتگوی پژوهش بسته است؛ پرسش تازه‌ای بپرسید.»).
