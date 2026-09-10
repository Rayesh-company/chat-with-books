# The Quoted answer replaces the streamed first answer

The first answer streams today as plain prose with a separate citation paragraph below it; the product now wants the answer itself to read like `docs/example.txt` — AI-written Filler paragraphs interleaved with verbatim Book Quote paragraphs, each quote hoverable for its Citation and labeled with its exact pages. That document cannot be streamed (one composed JSON blob), so on 2026-09-10 we split the render in two phases: the streamed answer stays exactly as it is (preview, TTFT, deltas untouched) and, once it finishes, the composer (`glm-5.3-flash`, thinking disabled, the streamed answer as context) writes the Quoted answer; on success it replaces the streamed answer in place. The swap threshold is part of the contract: swap only with at least one guard-passing Quote paragraph and at least one Filler or heading; anything less falls back to today's rendering — streamed answer plus the raw Evidence list, which is also the fallback on composer failure or timeout.

## Considered options

- Append the Quoted answer below the streamed answer. Rejected: two competing answers visible on one sheet, and a duplicate citation surface.
- Skip streaming and wait for the composed document. Rejected: minutes of dead air before the first token; breaks the "first answer that does not feel too slow" Session exit check.
- Keep the streamed answer's paragraphs as fillers and only insert quotes between them. Rejected: the streamed prose was not written as connective tissue around quotes and cannot produce the example.txt essay structure.

## Consequences

- The final form lands after the stream ends plus one composer call (sentence-picking alone measured 16.5 s on 2026-09-10; the full document write is unmeasured, expected 30–90 s). The wait is masked by reading the preview; a fast reader will see the text swap under them.
- The streamed answer is unreachable after the swap — no raw-answer toggle, deliberately.
- Filler paragraphs cannot be machine-guarded; "summarize what the quotes establish, introduce no new Book claims" is a prompt-level constraint only. Quote paragraphs stay under the verbatim guard, sentence-level drops.
- One passage per Quote paragraph, so the paragraph-end page label and every sentence tooltip cite exactly one locator; passages without text-layer page markers cite the Book title alone, never an invented page.

## Amendments

- 2026-09-10, later the same day (PM format call): the interleaving moved inside the paragraph. Every paragraph is one unit — AI text with embedded verbatim quotes, several passages allowed — matching `docs/example.txt` ("AI synthesized, quote, the whole paragraph's reference pages"); standalone Filler paragraphs and quote-only paragraphs are gone, and a paragraph with no surviving quote or no AI text drops whole. Swap threshold restated for the unit format: at least two quoting paragraphs, or one plus a heading. The one-passage-per-paragraph consequence above no longer holds: the paragraph end lists each quoted passage's pages in order, while each sentence tooltip still cites exactly the passage that sentence came from.
- 2026-09-10, evening (PM polish call): embedded quotes carry a resting clay highlight (tint + solid underline), and after the swap the whole Evidence section — «استناد» heading and list — is hidden instead of carrying a pointer note; a new question restores it for the fallback path. That call also moved the labels to first-page-only; the PM reversed the paragraph-end label back to the full chunk range («صفحات 740 تا 745») after one smoke, keeping the first-page form («صفحه 740») on the per-sentence tooltip.
