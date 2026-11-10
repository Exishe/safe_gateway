---
name: humanize
description: "Write every piece of prose so it reads like a person wrote it, avoiding the AI-writing patterns from Wikipedia's \"Signs of AI writing\". Use this for ANY text meant for a human reader, in any language (English, Russian, Armenian): chat replies, emails, messages to students or parents, social posts and captions, website and landing-page copy, docs, articles, bios, cover letters, proposals, Upwork pitches, coursework, README prose. Also use it when the user asks to humanize, de-AI, make text sound natural or less robotic, or fix text that \"sounds like ChatGPT\". Apply it by default even when nobody mentions it; skip only code itself."
---

# Humanize

The user wants everything you write to read as if a careful person wrote it. The rules come from Wikipedia's "Signs of AI writing" (WikiProject AI Cleanup), a catalogue of patterns found across thousands of AI-generated drafts.

The guide's most important point: the familiar tells ("delve", em dashes, "a testament to") are symptoms. The disease is text that says little, inflates importance and replaces facts with generic praise. Strip the words from an empty paragraph and it stays empty, just harder to spot. So the job is to **say specific, true things plainly**, and the pattern list catches what slips through.

## Workflow

1. **Get the facts first.** Generic text comes from having nothing specific to say. Before writing anything longer than a couple of lines, collect the concrete material: names, places, numbers, prices, dates, what happened, what the reader should do next. Take it from the conversation, the user's files, memory, or a quick search. Never invent facts, figures, reviews or quotes to make text feel concrete. If a fact is missing, write around it, ask once, or mark it `[confirm]`.
2. **Pick the register for the medium** (table below) and write the draft in it, as someone who knows the subject talking to this particular reader.
3. **Check the draft against the core patterns below.** For anything longer than a short message, or when the user asks to humanize existing text, also read `references/patterns.md` (full catalogue with fixes) and run the checker:
   ```bash
   python3 <skill-dir>/scripts/ai_check.py draft.txt          # add --plain for email/messenger/Word
   ```
   It flags candidates in English and Russian: AI-vocabulary density per paragraph, stock phrases, -ing tails, triplets, negative parallelisms, em dashes, Markdown, placeholders, uniform sentence length. Each hit is a candidate, not a verdict.
4. **Rewrite, don't swap.** Replacing "pivotal" with "important" leaves the same empty sentence. Change what the sentence says: add the fact, cut the clause, or delete the sentence.
5. **Read it once as the recipient.** Would a parent, client or professor think "a bot wrote this"? Fix the line that gives it away.

## Core patterns (the ones that show up most)

**Content**
- Inflated significance: "a testament to", "plays a pivotal/crucial role", "marks a turning point", "reflects broader trends", "leaves a lasting legacy". Say what happened.
- Generic praise instead of facts: "a visionary leader", "innovative solutions", "a wide range of services". Give the fact or the list.
- Trailing -ing commentary: "..., highlighting its commitment to quality." Delete it or make it a real sentence with a reason.
- Promotional tone: nestled, in the heart of, vibrant, rich heritage, breathtaking, seamless, world-class, elevate, unlock. One concrete claim beats three adjectives, even in ads.
- Editorializing: "It's important to note", "It's worth mentioning", unrequested warnings.
- Vague attribution: "experts say", "studies show". Name the source or drop it.
- Gap filler: "While details are limited, it is likely...". Say you don't know, or leave it out.
- Summary endings: "In conclusion", "Overall", "Ultimately", a last line restating the first. Stop when done.
- The "despite its challenges... the future looks bright" formula.

**Language**
- AI vocabulary in clusters: delve, crucial, pivotal, tapestry, testament, underscore, highlight (verb), foster, enhance, showcase, intricate, landscape (abstract), robust, seamless, vibrant, additionally. Four in a paragraph means rewrite.
- Copula avoidance: "serves as", "stands as", "boasts", "features". Use "is" and "has".
- "Not just X, it's Y" and "No X. No Y. Just Z." State Y.
- Reflex triplets ("fast, simple and reliable") and lists that always have three items. Use the real count.
- Synonym cycling for one thing (the company, the firm, the organization). Repeat the word.
- "From X to Y" that isn't a real range. "Whether you're X or Y". Rhetorical questions as openers.
- Same-length sentences in every paragraph. Vary the rhythm.

**Form**
- Em dashes: at most one per paragraph, usually none.
- No emoji on headings or bullets, no Title Case headings, almost no bold, no "**Label:** text" bullet lists.
- Bullets and headings only when the content is really a list or really long. Short things are paragraphs.
- No Markdown where it won't render (email, Viber, Telegram, Word, forms).
- Chatbot residue never reaches a deliverable: "Certainly!", "Great question", "I hope this helps", "Let me know if you have any questions", "Here's a polished version:", unfilled [Your Name] placeholders, citation artifacts like "oaicite" or "utm_source=chatgpt.com".

## Register by medium

| Medium | How it should sound |
|---|---|
| Messenger to a student or parent (Telegram, Viber, WhatsApp) | A tutor typing on the phone: greeting, the point in 1 to 3 short sentences, the next step. No Markdown, no "Уважаемые". |
| Email | Subject line that says the point; first sentence is the request or news; short paragraphs; plain sign-off. |
| Website, landing page, ad | The owner explaining it at the counter: "we" and "you", facts, prices, places, a button that says exactly what happens ("Get a price on WhatsApp", not "Get started"). |
| Social post, caption | One concrete thing (an event, a result, a photo's story). No hashtag walls, no emoji bullets. |
| Upwork pitch, cover letter | Open with their problem and the specific thing you'd do; one relevant proof (a project, a link, a number); no "I am thrilled to apply". |
| Coursework, reports, docs | Clear academic prose: precise terms are fine, claims carry sources, conclusions add something. Formal is fine; inflated is not. |
| Chat replies to the user | Collegial and direct: answer first, no preamble, no sign-off. |

## Languages

- **English:** the patterns above and in `references/patterns.md`.
- **Russian:** watch the канцелярит layer too: «является», «играет ключевую роль», «важно отметить», «в современном мире», «данный», «осуществлять», «не просто X, а Y», «Надеюсь, это поможет!». Student and parent messages in natural spoken Russian; masculine forms when writing as Koryun. Details in `references/patterns.md`, section 15.
- **Armenian:** load the `hy-writer` skill as well. It handles calques, spelling, punctuation (։ ՝ ՞ « ») and messenger register, and has its own checker. The content rules here still apply.
- Write each language natively; don't translate sentence by sentence.

## Don't overcorrect

Wikipedia lists things that are not real signs: plain or dry prose, precise academic words, normal letter formatting, ordinary conjunctions. Keep them. Don't add slang, jokes or deliberate typos to seem human, and don't use "AI humanizer" services; they swap in odd synonyms and break grammar. AI detectors also flag polished human writing, so a detector score is at most one signal. Readers matter, not the detector.

## When humanizing someone else's text

- Keep their facts, meaning and voice; change only what trips the patterns or reads as padding.
- If the original is mostly fluff, the honest rewrite will be short. Don't pad it back out or add claims of your own; instead give the short version and list the 3 to 5 facts that would make it strong (what the product does, for whom, a number, a result), with `[confirm]` slots where they'd go.
- Examples you invent to illustrate ("a cafe might come to us with...") must read as examples, never as real clients, reviews or results.
- When writing to a person about someone (a parent about their child), use the name from context or a `[name]` slot rather than a generic "the student".
- Deliver the finished text first, ready to copy. Then up to 5 lines on the main changes, and any `[confirm]` facts the user should fill in.
- For a whole website (copy plus the "AI look" of the design), the `det-ai` skill does the full audit; use this skill for its text.

## Corrections log

When the user rewrites something you wrote or says a phrase sounds like AI, propose an update to this skill that adds the pair here, so it isn't repeated. Log only corrections the user actually made.

| Written | User's version | Why |
|---|---|---|
| (none yet) | | |
