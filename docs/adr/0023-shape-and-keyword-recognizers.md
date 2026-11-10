# 0023. Shape and keyword recognizers for values that fail validation

- Status: Accepted
- Date: 2026-11-09
- Amends: ADR 0011, points 6 and 9

## Context

The first OpenPII run (`docs/BENCHMARKS.md`, commit bc8662e) masked 43.4% of English
phone numbers, 27.3% of card numbers, 53.1% of passport numbers and 21.1% of ID card
numbers. A miss analysis on the same seeded 10,000-record sample found that every
structured miss came from a missing recognizer or an over-strict rule: synthetic and
mistyped values fail libphonenumber or Luhn, and passports, ID cards and driving
licences had no recognizer at all. Real documents contain mistyped numbers too; a
leaked mistyped card is still a leaked card.

## Decision

Six rules, each its own recognizer, all listed in `PRIORITY` after every validating
recognizer, so a validated value keeps its recognizer and score:

| Recognizer | Rule | Type | Score |
|------------|------|------|------:|
| `phone_shape` | `+` and 10 to 15 digits, single separators (space, dot, hyphen, brackets) | PHONE | 0.85 |
| `phone_context` | no `+`: three digit groups or a leading `(`, 9 to 15 digits, a phone keyword in the 40 characters before | PHONE | 0.8 |
| `id_document_context` | a document keyword in the 40 characters before; 8 to 20 characters: 9 to 14 digits, or at least two upper-case letters and two digits, parts joined by single spaces or hyphens | PASSPORT, DRIVER_LICENSE or NATIONAL_ID by keyword | 0.75 |
| `passport_shape` | `AA9999999`, word-bounded | PASSPORT | 0.7 |
| `id_card_shape` | `AA99999AA`, word-bounded | NATIONAL_ID | 0.65 |
| `credit_card_shape` | Luhn-invalid, exact shape: 16 digits starting 2-6, 15 starting 3, 19 starting 3-6; contiguous or grouped by four | CREDIT_CARD | 0.55 |

1. **A shape alone only where it is distinctive.** The `+` is required for an
   ungated phone, so IP addresses, dates and amounts are never read as phones.
   Card shapes are the whole digit run, never a window of a longer one.
2. **Everything else needs a keyword.** Phone keywords: tel, phone, mobile, mob, cell,
   call, fax, contact, whatsapp, телефон, тел, моб, հեռ (case-insensitive; all but
   "phone" must start a word, so "cancellation" is not "cell"). Document keywords:
   passport, паспорт (PASSPORT); licence, license, driver, DL, водительск-
   (DRIVER_LICENSE); ID, identity, identification, удостоверени- (NATIONAL_ID). The
   nearest specific keyword names the type; a generic one only gives NATIONAL_ID, so
   "водительское удостоверение" is a driving licence. A keyword is never part of the
   value.
3. **Fallbacks defer to the validating phone recognizer** (`merge.defer`) when it
   found a phone at the fallback's start. Where libphonenumber found "+374 77 123456",
   the shape "+374 77 123456 0010" keeps only "0010": the validated phone keeps its
   exact span, and no digit that a rule found is left unmasked (ADR 0013). A validated
   match further in ("83600-7213" read without the "+68" before it) read only part of
   the number, so the shape is kept whole and wins the merge as the longer span. A
   last digit group glued to a word ("01K@example.com") is not part of a phone.
4. **Scores.** 0.55 for a card shape is above the default threshold, so it is masked;
   a customer who sees order numbers masked raises the CREDIT_CARD threshold above 0.55
   and keeps Luhn-valid cards (1.0).
5. **Not done.** Building numbers and postcodes wait until GLiNER's ADDRESS label is
   measured. Keywords in other languages (de, es, fr, it, nl, hi, te) are not added.

## Consequences

- Masking recall on OpenPII rises for phones, cards, passports and ID cards; the new
  figures and the per-label change are in `docs/BENCHMARKS.md`.
- More over-masking: a 16-digit order number starting with 4, a nine-digit number
  after "ID", or "ADR 0013" in a sentence that mentions an ID, is masked. This is the
  trade ADR 0013 already made.
- `tools.scan_pii`, a commit gate, keeps to checksum-validated cards and national IDs
  (engine threshold 1.0 for CREDIT_CARD and NATIONAL_ID), so these rules do not block
  commits of order numbers or code comments.

## Amended 2026-11-09: leak hunt 1c

The third leak-hunter round (`tests/unit/engine/test_leak_hunt_1c.py`) found
eight shape and keyword inputs that leaked. Decisions:

1. **Russian series and number** (`id_document_context`). `серия 4599 № 012345`,
   `серии 45 99 номер 012345` and `сер. 4599 N012345` are one value: the series
   (two and two digits, the space optional), then optional `№`, `N`, `No`,
   `номер` or `ном.`, then six digits, masked together. The word for "series"
   is the gate. The type is PASSPORT when a passport keyword stands in the 40
   characters before, else NATIONAL_ID (other Russian certificates are written
   the same way).
2. **One letter and seven or more digits** is a code after a document keyword
   (`N1234567`: Indian and Australian passports, Californian licences;
   `A1234567890` after "ID"). Without a keyword it is not reported.
3. **Case.** After a keyword, codes are read in any case (`ab1234567`). A
   lower-case word without a digit is never part of a code, so "passport
   number ab1234567" masks the code, not the word "number". Without a keyword,
   `passport_shape` now accepts two lower-case letters and seven digits as well
   as two capitals; mixed case (`Ab1234567`) stays a word. This was weighed
   against over-masking: the lower-case shape also matches some user names,
   ticket and serial numbers in logs and code, which are then masked as
   PASSPORT. A passport number typed in lower case in a chat is a plausible
   leak, and a masked serial number costs the customer a placeholder, not a
   disclosure, so the shape is accepted in both cases. `id_card_shape`
   (`AA99999AA`) stays upper-case only: in lower case it reads like a hash
   fragment, and no leak was found for it.
4. **Armenian document keywords**: `անձնագ-` (passport: PASSPORT), `վարորդական`
   (driver's: DRIVER_LICENSE), `սոցիալական քարտ` (social card), `ՀԾՀ` (public
   service number, printed on the social card) and `նույնականացման`
   (identification), the last three NATIONAL_ID; case-insensitive.
5. **Card shapes with mixed or doubled separators** (`credit_card_shape`).
   Each gap between groups of four may be one space, dot or hyphen, chosen
   independently, or two spaces (justified PDF text). Doubled hyphens or dots,
   three spaces, or two different separators in one gap still end the number:
   they read as layout between two numbers, not inside one.
6. **`+` phones.** Between groups a `+` number may also use two spaces, a
   spaced hyphen (` - `) or a slash. The `+` still gates them, so dates and
   amounts are not read. The keyword-gated `phone_context` is unchanged.
7. **A last group glued to a word keeps its digits** (amends point 3).
   `+99 912 345 6789Fax:` (PDF columns run together) ends at the letter, so no
   digit is dropped. Only a group glued to an e-mail address (an `@` before the
   next whitespace, as in `01K@example.com`) still belongs to the address and
   is dropped; the e-mail recognizer masks it.
8. **Property tests.** Hypothesis now generates `+` phones (`+99`, groups of
   one to four digits, every accepted gap, an optional bracketed group),
   `AA9999999` in both cases, `AA99999AA` and card shapes with mixed gaps, and
   checks each at its exact offsets in the recognizer and fully masked through
   `detect()` (`tests/property/test_shape_detection.py`).

Consequences.

- More over-masking: lower-case `xx1234567` tokens anywhere, more codes after
  a document keyword, and numbers with doubled spaces. The OpenPII deltas,
  precision included, are in `docs/BENCHMARKS.md`.
- A digit glued to a word after a `+` number (`+99 912 345 678 9ab`) is now
  masked with the phone: one extra digit masked rather than one left.

## Amended 2026-11-09: leak hunt 1d

The fourth leak-hunter round (`tests/unit/engine/test_leak_hunt_1d.py`) found
inputs among its 21 where a document code, a card or a `+` phone leaked through
its shape or keyword rule. Decisions (the clean view, dates and routing are in ADR
0016, the known-value sweep in ADR 0014):

1. **Luhn-valid cards with other separators** (`credit_card`). Besides the digit
   runs of ADR 0013, a card in its usual grouping (groups of four with a last group
   of one to four digits; Amex 4-6-5; 4-4-5) may use any gap of one to three
   characters that are neither letters nor digits: a slash, a tab, three spaces,
   `- `, `. `, a line break and the next line's indent. Luhn and the issuer digit
   keep this precise, as hard rule 4 requires 100% here. Two limits are chosen,
   not offered: the grouping is required with these gaps, so a table of small
   numbers joined by tabs is not read as one run; and a comma or semicolon is never
   such a gap, because it separates a list (a list of four years starting with 2
   is Luhn-valid one time in ten). A card written with a comma between its groups is
   therefore still found only by the models, if at all.
2. **A short group beside a card shape is left out** (`credit_card_shape`, amends
   1c point 5). The shape used to refuse a card next to any digit group, to keep
   it a whole run, so `... 9010 12/29` (an expiry date) and `qty 2 4000-...` (a
   quantity) leaked. The recognizer now reads a whole run of digit groups and,
   when it is not a card shape, drops the fewest groups of one to three digits at
   either end until it is one. A neighbouring group of four or more digits still
   makes the whole run a longer number, refused. A quantity before a test card, a
   negative case in 1c, is now masked as a card plus `12` left over: more masking.
3. **`+` phones** (amends 1c point 6). A gap may also be one or two tabs (table
   cells), or a line break with up to two spaces or tabs on each side (a wrapped
   PDF line); a bracket may have one space inside it (`+99 ( 912 ) 345 6789`). The
   `+` still gates the rule; the 15-digit limit cuts a number joined to the next
   line's digits.
4. **Russian series and number** (amends 1c point 1). Both words are read in any
   case (`ПАСПОРТ СЕРИЯ ... НОМЕР ...`); the word for "series" also in the
   instrumental, accusative and genitive plural (`серией`, `серию`, `серий`);
   "and" may stand before the word for number (`и номер`); `N.`, `Nr.` and `#`
   join `№`, `N`, `No` and `номер`. The number may come first (`номер 012345 серия
   4599`): the span runs from the number to the series, both words inside it, and
   both words are the gate. Type as before: PASSPORT after a passport keyword,
   else NATIONAL_ID.
5. **Cyrillic letters in document codes** (`id_document_context`). A code part may
   use Cyrillic letters as well as Latin ones, so a Russian driving licence (two
   digits, two Cyrillic letters, six digits) and a passport number typed on a
   Russian keyboard are codes after a keyword. A word in Cyrillic capitals is a
   part too, as Latin ones were: a heading in capitals next to a code may be masked
   with it (more over-masking, the trade 1c made for Latin).
6. **Keyword distance.** Of the two fixes offered (a wider window, or the rest of
   the line), neither is taken as such: both would let any keyword earlier in a
   long chat line gate every code after it, and the 1c negative case `Passport
   office hours are Monday to Friday; ref 123456789` would be masked. Instead the
   window stays 40 characters, and becomes 80 when the code directly follows a
   colon (a label: `Passport number (as printed on the data page): ...`).
7. **An underscore is a boundary** for the passport and ID card shapes and for
   keyword-gated codes, as in a file name (`scan_passport_AB1234567.pdf`). A hyphen
   or a letter before or after the code still refuses it.
8. **Look-alike letters** (`passport_shape`, `id_card_shape`). Before matching,
   Cyrillic and Greek letters that look like Latin ones are folded to them one for
   one (Cyrillic А В Е К М Н О Р С Т У Х І Ј Ѕ and their small look-alikes, Greek
   capitals and small omicron; the table is built from code points, and a test
   checks each entry's Unicode name). Only these two shapes read the fold: the
   clean view and the NER models keep the letters as written, because folding them
   everywhere would turn Russian words into Latin nonsense for the Russian model.
9. **Property tests** generate Luhn-valid cards with each loose gap, card shapes
   after a quantity or before an expiry month, `+` phones with tabs, line breaks
   and spaced brackets, and passport shapes with Cyrillic look-alike letters or
   glued to a file name by underscores (`tests/property/test_shape_detection.py`).

Consequences.

- More over-masking: Luhn-valid numbers in card grouping with unusual gaps; a short
  number beside a 16-digit block; Cyrillic codes and capitals after a document
  keyword; codes 41 to 80 characters after a keyword when a colon precedes them;
  two-letter, seven-digit tokens after an underscore or with Greek or Cyrillic
  letters. The OpenPII deltas, precision included, are in `docs/BENCHMARKS.md`.
- Left out on purpose: a card with commas between its groups, a code further than
  40 characters from its keyword without a colon before it.
