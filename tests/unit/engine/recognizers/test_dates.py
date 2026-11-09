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
