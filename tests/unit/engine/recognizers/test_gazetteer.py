from pathlib import Path

import pytest

from gateway.engine.recognizers.gazetteer import Gazetteer, load_gazetteer
from gateway.engine.types import EntityType

# Invented names; no real client is listed anywhere in this repository.
ENTRIES = {
    "Zorvath Quillin": EntityType.PERSON,
    "Acme Holdings": EntityType.ORG,
    "Acme": EntityType.ORG,
    "Bluebird": EntityType.CODENAME,
    "Зорват Квиллин": EntityType.PERSON,
    "Զորվաթ Քվիլին": EntityType.PERSON,
}


def found(text: str, entries: dict[str, EntityType] = ENTRIES) -> list[tuple[str, EntityType]]:
    return [(text[s.start : s.end], s.entity_type) for s in Gazetteer(entries).find(text)]


def test_case_insensitive_whole_words() -> None:
    assert found("project BLUEBIRD starts") == [("BLUEBIRD", EntityType.CODENAME)]


def test_no_match_inside_a_longer_word() -> None:
    assert found("bluebirds and Acmeworks") == []


def test_multi_word_entries_tolerate_extra_spaces_and_line_breaks() -> None:
    assert found("Dear Zorvath\n  Quillin,") == [("Zorvath\n  Quillin", EntityType.PERSON)]


def test_longest_entry_wins_where_entries_overlap() -> None:
    assert found("Acme Holdings signed") == [("Acme Holdings", EntityType.ORG)]


def test_cyrillic_and_armenian_names() -> None:
    assert found("клиент ЗОРВАТ КВИЛЛИН") == [("ЗОРВАТ КВИЛЛИН", EntityType.PERSON)]
    assert found("հաճախորդ Զորվաթ Քվիլին։") == [("Զորվաթ Քվիլին", EntityType.PERSON)]


def test_entries_with_punctuation_match_on_their_own_edges() -> None:
    entries = {"O'Brien & Co.": EntityType.ORG}
    assert found("Ask O'Brien & Co. today", entries) == [("O'Brien & Co.", EntityType.ORG)]


def test_empty_gazetteer_finds_nothing() -> None:
    assert found("anything at all", {}) == []


def test_csv_loader_with_blank_type_means_codename(tmp_path: Path) -> None:
    csv_file = tmp_path / "clients.csv"
    csv_file.write_text("value,entity_type\nZorvath Quillin,PERSON\nBluebird,\n", encoding="utf-8")

    gazetteer = load_gazetteer(csv_file)

    assert gazetteer.entries == {
        "Zorvath Quillin": EntityType.PERSON,
        "Bluebird": EntityType.CODENAME,
    }


def test_csv_loader_names_the_line_of_an_unknown_type(tmp_path: Path) -> None:
    csv_file = tmp_path / "clients.csv"
    csv_file.write_text("value,entity_type\nAcme,ORG\nBluebird,PROJECT\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"line 3.*PROJECT"):
        load_gazetteer(csv_file)


def test_csv_loader_rejects_non_utf8_with_a_clear_message(tmp_path: Path) -> None:
    csv_file = tmp_path / "clients.csv"
    csv_file.write_bytes("value\nJosé\n".encode("cp1252"))

    with pytest.raises(ValueError, match="UTF-8"):
        load_gazetteer(csv_file)
