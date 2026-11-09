"""Dates in numbers or with an English or Russian month name (ADR 0016)."""

import pytest

from gateway.engine.recognizers.dates import DATE
from gateway.engine.types import EntityType


def found(text: str) -> list[str]:
    return [text[s.start : s.end] for s in DATE.find(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DOB: 12.03.1985", "12.03.1985"),
        ("born 3.5.1985, Novosibirsk", "3.5.1985"),
        ("seen on 12/03/1985.", "12/03/1985"),
        ("seen on 03/31/1985.", "03/31/1985"),  # US month first
        ("seen on 31-03-1985", "31-03-1985"),
        ("Admitted: 2024-02-14", "2024-02-14"),
        ("Admitted: 2024/02/14", "2024/02/14"),
        ("signed 12.03.85 in Lyon", "12.03.85"),
        ("on 12 March 1985 at noon", "12 March 1985"),
        ("on 12th of March, 1985", "12th of March, 1985"),
        ("on March 12, 1985 at noon", "March 12, 1985"),
        ("on Mar 12 1985", "Mar 12 1985"),
        ("on Sept. 3rd, 2024", "Sept. 3rd, 2024"),
        ("ON 12 MARCH 1985", "12 MARCH 1985"),
        ("Пациент родился 12 марта 1985 года.", "12 марта 1985"),
        ("Договор заключён 3 мая 2024 г.", "3 мая 2024"),
        ("Дата: 12 мар. 1985", "12 мар. 1985"),
        ("ДАТА: 1 ЯНВАРЯ 2000 Г.", "1 ЯНВАРЯ 2000"),
        ("Дата рождения: 12.03.1985", "12.03.1985"),
    ],
)
def test_dates_in_each_format(text: str, expected: str) -> None:
    assert found(text) == [expected]
    assert {span.entity_type for span in DATE.find(text)} == {EntityType.DATE}


@pytest.mark.parametrize(
    "text",
    [
        "32.01.1985",  # no day 32
        "12.13.1985",  # no month 13 either way
        "13/13/1985",
        "2024-13-01",
        "2024-02-32",
        "0 March 1985",
        "March 32, 1985",
        "12 мартобря 1985",
        "12 March 3085",  # not a year we read
    ],
)
def test_impossible_dates_are_refused(text: str) -> None:
    assert found(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "server 192.168.10.12 is down",
        "server 10.11.12.13:8080",
        "ping 203.0.113.7",
        "dns 2001:db8::1985",
        "version 3.12.3",
        "Python 3.11.12",
        "build v10.12.15",
        "Windows 10.0.19045",
        "release 1.2.3.4.5",
        "call +7 912 345-67-89",
        "call 8-912-345-67-89",
        "call (201) 555-0123",
        "call 555-12-345",
        "ratio 12/03",
        "order 12.03.19856",
        "id 112.03.1985",  # pragma: allowlist secret
    ],
)
def test_addresses_versions_and_phone_numbers_are_not_dates(text: str) -> None:
    assert found(text) == []


# ------------------------------------------------------------------ leak hunt 1c (ADR 0016 amended)

EN_DASH = chr(0x2013)
RANGE = "12.03.1985-15.03.1985"  # pragma: allowlist secret


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"stay {RANGE} here", ["12.03.1985", "15.03.1985"]),
        (
            f"stay 12.03.1985{EN_DASH}15.03.1985 here",
            ["12.03.1985", "15.03.1985"],
        ),  # pragma: allowlist secret
        ("visits 12.03.1985,15.03.1985,16.03.1985.", ["12.03.1985", "15.03.1985", "16.03.1985"]),
        ("period 1985-03-12/1985-03-15 inclusive", ["1985-03-12", "1985-03-15"]),
        ("from 12 March 1985-15 March 1985", ["12 March 1985", "15 March 1985"]),
    ],
)
def test_ranges_and_lists_of_dates_without_spaces(text: str, expected: list[str]) -> None:
    assert found(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "call 12-03-1985-1234",  # a date shape inside a longer number
        "ref 1234/12.03.1985",
        "ids 12.03.1985,1234",
    ],
)
def test_a_separator_and_a_digit_next_to_a_date_still_refuse_it_unless_both_are_dates(
    text: str,
) -> None:
    assert found(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Договор заключён «12» марта 1985 г.", "«12» марта 1985"),
        ('Договор от "3" мая 2024 г.', '"3" мая 2024'),
        ("Договор от „3“ мая 2024 г.", "„3“ мая 2024"),
    ],
)
def test_russian_day_in_guillemets_or_quotes(text: str, expected: str) -> None:
    assert found(text) == [expected]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DOB 12-MAR-1985 admitted.", "12-MAR-1985"),
        ("Дата рождения: 12-Mar-1985, пол мужской.", "12-Mar-1985"),
        ("log 12/Mar/1985 ok", "12/Mar/1985"),
        ("seen 3.Sept.2024 ok", "3.Sept.2024"),
        ("seen 01-JANUARY-2000 ok", "01-JANUARY-2000"),
        ("выписан 12-мар-1985 г.", "12-мар-1985"),
        ("выписан 12-МАЯ-1985 г.", "12-МАЯ-1985"),
        ("drawn 1985-Mar-12 ok", "1985-Mar-12"),
    ],
)
def test_hyphenated_and_abbreviated_month_names(text: str, expected: str) -> None:
    assert found(text) == [expected]


@pytest.mark.parametrize(
    "text",
    ["12-Mar-3085", "32-Mar-1985", "12-Mar/1985", "12-Marz-1985", "A12-Mar-1985", "x12-мар-1985"],
)
def test_hyphenated_month_names_still_need_a_real_date(text: str) -> None:
    assert found(text) == []
