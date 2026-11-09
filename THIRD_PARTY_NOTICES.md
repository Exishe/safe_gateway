# Third-party notices

SafeGuard Gateway ships third-party software under the licenses below. The
complete list for a release is generated from the runtime dependency closure
when packaging starts (Phase 6, ADR 0008); this file records the notices that
are required or were approved as exceptions before then.

## regex

Approved as a license exception on 2026-11-09 (ADR 0012,
`tools/license_policy.toml`). Pulled in by presidio-analyzer.

- License: Apache-2.0 AND CNRI-Python.
- The parts derived from CPython's `re` module are under the CNRI license for
  Python 1.6, which requires keeping its copyright notice and license text with
  any distribution. The full license text ships in the package's `LICENSE.txt`
  and must be included verbatim in the installers' notices.

## spaCy models

`en_core_web_lg` and `ru_core_news_lg` 3.8.0 by Explosion AI, MIT license,
downloaded by `safeguard models pull` (not bundled).
