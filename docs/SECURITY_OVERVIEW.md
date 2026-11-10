# SafeGuard Gateway: security overview

For law, accounting, medical and HR firms considering a pilot. Detail and limits are in
[SECURITY.md](SECURITY.md) and [BENCHMARKS.md](BENCHMARKS.md). This page is not legal advice.

## What it does on your machine

SafeGuard Gateway runs on the computer your staff use. Before text goes to an AI model, it looks
for personal data (names, addresses, phone numbers, card and ID numbers, emails and similar) and
replaces each value with a placeholder such as `[PERSON_1]`. When the answer comes back, it puts the
real values back. Finding and replacing happen locally. The table that links placeholders to real
values stays on the machine, in memory or encrypted with a key from the operating system's keychain,
and is deleted when the session ends.

## What leaves the machine

Only the masked text, sent to the AI provider you chose, with your own API key (kept in the
operating system keychain). Apart from a one-time download of the detection models, the gateway sends nothing to us, and the detection engine is not allowed
to open network connections. Two things still reach the provider: any value the detector missed (see
limits), and the shape of your question. A masked contract still looks like a contract about a
dispute. If that is not acceptable, the gateway can use a model running on the same machine.

## What it records

A tamper-evident audit log on the machine: for each request, the time, provider host, model,
how many values of each type were masked, a hash of the masked text, and the size. It never stores
the text itself, masked or raw. Each row is chained to the one before it and signed with a key in the
keychain, so edits, deletions and insertions are detectable by anyone who does not hold that key.
It cannot detect someone who has the key, or deletion of the whole file; keep periodic exports
(`safeguard audit export`) somewhere staff cannot change. The log shows what the gateway sent,
not what staff pasted into other services.

## Known limits

- **Detection is measured, not perfect.** Card numbers, IBANs, US Social Security numbers and similar
  structured identifiers pass 100% of our generated test values; that is a test on synthetic data,
  not a promise about your documents. Names are statistical. On the public ai4privacy OpenPII English
  test set (10,000 records, measured 2026-11-10), 83.6% of given names and 85.4% of surnames were
  masked; addresses and building numbers were lower (street 78.2%, building number 37.6%). Names in
  Russian and Armenian are not measured. Review text before sending, and keep a list of client names
  that must always be masked.
- **Masked is not anonymous.** Under GDPR Art. 4(5), masked text that can be linked back to a person
  with the separately kept table is pseudonymised data, and is still personal data.
- **Not HIPAA Safe Harbor de-identification.** Safe Harbor requires removing all 18 listed
  identifiers; we do not cover all of them, and recall for names is below 100%. We do not claim it.
- **Installers are unsigned** in the pilot, so your operating system will warn on first run
  (see [INSTALL.md](INSTALL.md)).
- **The browser extension is tested on local fixture pages only,** not on the live chat sites. If a
  site changes its page, messages there may go out unmasked without a notice. It does not mask
  attachments, images or voice input.
- The local service reduces exposure to the network and web pages; it is not a defence against malware running as the same
  user.

## Who is responsible for compliance

You are. Compliance with GDPR, HIPAA or any other regime stays with your firm, including the choice
of provider, your contract with it, notices to clients and any breach duties. We provide controls
and evidence: local masking, a tamper-evident log, and a written
[runbook for a suspected leak](RUNBOOK_SUSPECTED_LEAK.md). Your data protection officer or counsel
should review the gateway before it is used with client data.
