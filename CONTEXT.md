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
One sitting of Farsi Q&A against the Book set that can meet the Phase 2 exit checks: answers with Citations, a Deep dive, and a first answer that does not feel too slow.
_Avoid_: Chat, demo (showing the product at an expo is not automatically a Session)

**Deep dive**:
A second study in the same Session, started by the Session operator on the same question, that plans its own searches of the Book set and returns a long, headed study — multiple pages, every paragraph a Quoted paragraph. It may feel slow. It is not a longer first answer, and it is not a new question.
_Avoid_: deep search, second pass, follow-up (a new question is a new first answer), COT (the Cognee type is not the domain name), Next-tier search (the superseded name)

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
The woven answer of an ask: paragraphs that each interleave Filler text with embedded verbatim Book sentences and end with the pages they cite. It follows the Quote selection and precedes the Deep dive.
_Avoid_: citation paragraph (the superseded paragraph-only design), chat, first answer (the Quote selection is the first answer)

**Quoted paragraph**:
A paragraph of a Quoted answer: Filler text with verbatim Book sentences embedded inside it, each sentence highlighted and hoverable for its own Citation (the passage's first page), the paragraph ending with the page range of every passage it quoted. May weave several passages.
_Avoid_: evidence block, snippet, quote-only paragraph

**Filler text**:
The AI-written connective text inside a Quoted paragraph; it claims no pages and is never shown as quoted.
_Avoid_: glue text, preamble, filler paragraph (the superseded standalone-paragraph design)
