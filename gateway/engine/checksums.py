"""Check-digit validation for structured identifiers (ADR 0011).

All functions take the identifier's characters only; callers strip separators.
"""

import unicodedata

# IBAN length per country, from the SWIFT IBAN registry as packaged by schwifty
# (MIT), read 2026-11-09. A wrong length here means a missed IBAN, so update
# it from the registry rather than by hand.
_IBAN_REGISTRY = """
AD24 AE23 AL28 AO25 AT20 AX18 AZ28 BA20 BE16 BF28 BG22 BH22 BI27 BJ28 BL27 BR29
BY28 CF27 CG27 CH21 CI28 CM27 CR22 CV25 CY28 CZ24 DE22 DJ27 DK18 DO28 DZ26 EE20
EG29 ES24 FI18 FK18 FO18 FR27 GA27 GB22 GE22 GF27 GG22 GI23 GL18 GP27 GQ27 GR27
GT28 GW25 HN28 HR21 HU28 IE22 IL23 IM22 IQ23 IR26 IS26 IT27 JE22 JO30 KM27 KW30
KZ20 LB28 LC32 LI21 LT20 LU20 LV21 LY25 MA28 MC27 MD24 ME22 MF27 MG27 MK19 ML28
MN20 MQ27 MR27 MT31 MU30 MZ25 NC27 NE28 NI28 NL18 NO15 OM23 PF27 PK24 PL28 PM27
PS29 PT25 QA29 RE27 RO24 RS22 RU33 SA24 SC31 SD18 SE24 SI19 SK24 SM27 SN28 SO23
ST25 SV28 TD27 TF27 TG28 TL23 TN24 TR26 UA29 VA22 VG24 WF27 XK20 YE30 YT27
"""


def _parse_registry(registry: str) -> dict[str, int]:
    return {entry[:2]: int(entry[2:]) for entry in registry.split()}


IBAN_LENGTHS: dict[str, int] = _parse_registry(_IBAN_REGISTRY)

_INN10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN11_WEIGHTS = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_SNILS_CHECKSUM_FROM = 1_001_998  # numbers up to 001-001-998 carry no checksum


def digits_of(text: str) -> str:
    """ASCII digits for every Unicode decimal digit in `text`; everything else dropped."""
    return "".join(str(unicodedata.digit(char)) for char in text if char.isdecimal())


def luhn_valid(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2  # noqa: PLR2004
        total += value
    return bool(digits) and total % 10 == 0


def iban_valid(iban: str) -> bool:
    compact = "".join(iban.split()).upper()
    if not compact.isascii() or not compact.isalnum():
        return False
    if IBAN_LENGTHS.get(compact[:2]) != len(compact):
        return False
    rearranged = compact[4:] + compact[:4]
    as_number = "".join(str(int(char, 36)) for char in rearranged)
    return int(as_number) % 97 == 1


def _weighted_check(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 11 % 10


def inn_valid(digits: str) -> bool:
    if not digits.isdigit():
        return False
    if len(digits) == 10:  # noqa: PLR2004
        return _weighted_check(digits, _INN10_WEIGHTS) == int(digits[9])
    if len(digits) == 12:  # noqa: PLR2004
        return _weighted_check(digits, _INN11_WEIGHTS) == int(digits[10]) and _weighted_check(
            digits, _INN12_WEIGHTS
        ) == int(digits[11])
    return False


def snils_valid(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit():  # noqa: PLR2004
        return False
    if int(digits[:9]) <= _SNILS_CHECKSUM_FROM:
        return False
    total = sum(int(d) * (9 - i) for i, d in enumerate(digits[:9]))
    check = total % 101 if total > 101 else total  # noqa: PLR2004
    if check == 100:  # noqa: PLR2004
        check = 0
    return check == int(digits[9:])
