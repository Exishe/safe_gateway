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
3. **Fallbacks defer to the validating phone recognizer** (`merge.defer`). Where
   libphonenumber found "+374 77 123456", the shape "+374 77 123456 0010" keeps only
   "0010": the validated phone keeps its exact span, and no digit that a rule found is
   left unmasked (ADR 0013).
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
