"""Leak-hunter round 2 (Phase 1b): the NER path of detect().

Every test here failed when it was written. Each one names the trick; the
docstring says what detect() returned instead. Names, organisations and places
are invented. Characters that are invisible or easily confused are built with
chr(), so the source holds none of them.

The model tests pair each failing input with a control: the same text in a form
the same model already handles. The control passes today, so what fails is the
form our code hands to the model (or the model it picks), not the model itself.
Inputs the model misses even when given them cleanly are measured, not asserted
(CLAUDE.md rule 4), and are not in this file.
"""

import base64
import time
import unicodedata

import pytest

import gateway.engine.detect as detect_module
from gateway.engine.detect import detect
from gateway.engine.ner import SPACY_MODEL_DIRS, ModelMissing

Case = tuple[str, str]  # (text, value as written)

APOSTROPHE = chr(0x2019)  # right single quotation mark: Word's autocorrected apostrophe
VS16 = chr(0xFE0F)  # variation selector-16: follows most emoji (scales, phone, envelope, arrow)
SCALES, PHONE, ENVELOPE, ARROW = chr(0x2696), chr(0x260E), chr(0x2709), chr(0x27A1)
PARAGRAPH_SEPARATOR = chr(0x2029)
FORM_FEED = chr(0x0C)  # pdftotext puts one between pages
LONE_SURROGATE = chr(0xD83D)  # half an emoji: what JavaScript leaves when it cuts a string

EN = "Zorvath Quillin joined Brightwater Analytics in Portland last March."
RU = "Договор подписал Зорват Квиллин, директор ООО «Халверн Биотех», в Новосибирске."
EN_VALUES = ("Zorvath Quillin", "Brightwater Analytics", "Portland")


@pytest.fixture
def real_models() -> None:
    for lang in SPACY_MODEL_DIRS:
        try:
            detect_module.model_dir(lang)
        except ModelMissing as error:
            pytest.skip(f"model absent: {error}")


def leaks(cases: list[Case]) -> list[str]:
    """Every case with a letter or digit of its value outside all spans, and what came back."""
    report = []
    for text, value in cases:
        spans = detect(text)
        start = text.index(value)
        bare = "".join(
            text[i]
            for i in range(start, start + len(value))
            if text[i].isalnum() and not any(s.start <= i < s.end for s in spans)
        )
        if bare:
            got = [(text[s.start : s.end], s.entity_type.value) for s in spans]
            report.append(f"{value!r} in {text!r}: {bare!r} unmasked; detect() returned {got!r}")
    return report


def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


# ------------------------------------------------------------------ what the model is handed


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_all_caps_english_name_in_a_header_or_signature() -> None:
    """Capitals reach the English model as written, and it reads case as a cue. Each name
    is masked when it alone is in title case; in capitals detect() returned [] for the
    first four and only ('KASIMIR', ORG) for the fifth. Over 10 invented names per
    header, 2 to 3 of 10 were masked in capitals against 9 to 10 of 10 in title case."""
    templates = [
        ("BETWEEN: {} (THE CLAIMANT)", "Selene Marchetti"),
        ("WITNESS STATEMENT OF {}", "Mirela Okonkwo"),
        ("{} SIGNED THE LEASE ON BEHALF OF THE TENANT.", "Orsolya Fenwright"),
        ("Kind regards,\n\n{}\nSenior Partner", "Ptolemy Harrowgate"),
        ("RE: {} V. BRIGHTWATER ANALYTICS", "Kasimir Dellacourt"),
    ]
    assert leaks([(t.format(name), name) for t, name in templates]) == []  # control
    assert leaks([(t.format(name.upper()), name.upper()) for t, name in templates]) == []


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_all_caps_russian_name_in_a_court_header() -> None:
    """The same for the Russian model: in capitals detect() returned no PERSON, only
    ('ПРОЖИВАЮЩИЙ', ORG) and ('НОВОСИБИРСКЕ', ORG) for the first and [] for the others;
    with the name in title case each is masked. Over 10 names, these headers masked 0 to 1
    of 10 in capitals and 10 of 10 in title case."""
    templates = [
        ("ИСТЕЦ: {}, ПРОЖИВАЮЩИЙ В НОВОСИБИРСКЕ.", "Квиллин Зорват Петрович"),
        ("ДОГОВОР ПОДПИСАЛ {} В ПРИСУТСТВИИ НОТАРИУСА.", "Мирела Оконкво"),
        ("ПРОТОКОЛ ДОПРОСА СВИДЕТЕЛЯ {}", "Тавиэль Брандт"),
    ]
    assert leaks([(t.format(name), name) for t, name in templates]) == []  # control
    assert leaks([(t.format(name.upper()), name.upper()) for t, name in templates]) == []


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_names_with_decomposed_accents_nfd() -> None:
    """macOS file names and some PDF text layers use NFD: 'é' is 'e' plus U+0301. The text
    view does not normalise, so the model sees a combining mark mid-word. NFC is masked;
    in NFD detect() returned ('José', ORG) only, ('May', DATE) only, ('Paris', LOCATION)
    only, and masked 'Ёлкин' but not 'Пётр'. Over 10 names: 1 to 3 of 10 in NFD."""
    cases = [
        ("Our client José Quillén called about the invoice.", "José Quillén"),
        ("The tenant Renée Duquesné moved out in May.", "Renée Duquesné"),
        ("Witness: François Lefèvre-Quillin, Paris.", "François Lefèvre-Quillin"),
        ("Договор подписал Зорват Квиллин в Новосибирске; Ёлкин Пётр присутствовал.", "Пётр"),
    ]
    assert leaks(cases) == []  # control
    assert leaks([(nfd(text), nfd(value)) for text, value in cases]) == []


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_names_with_a_typographic_apostrophe() -> None:
    """Word turns ' into U+2019 as you type. The text view unifies dashes and spaces but not
    apostrophes. With ' each name is masked; with U+2019 detect() returned [] for Taviel
    D'Arbanne and only ('Lyon', LOCATION) for Lucien D'Orvalle. Over 10 names with an
    apostrophe: 10 of 10 masked with ', 6 of 10 with U+2019."""
    cases = [
        ("Taviel D'Arbanne signed the contract.", "Taviel D'Arbanne"),
        ("Contact Lucien D'Orvalle at the Lyon office.", "Lucien D'Orvalle"),
    ]
    assert leaks(cases) == []  # control
    curly = [(t.replace("'", APOSTROPHE), v.replace("'", APOSTROPHE)) for t, v in cases]
    assert leaks(curly) == []


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_emoji_with_a_variation_selector_glued_to_a_name() -> None:
    """U+FE0F (category Mn) is not among the characters the text view removes. Glued after
    an emoji it hides the first name: detect() returned ('Quillin', PERSON),
    ('Okonkwo', ORG), ('Brandt', ORG) and []; inside a name, ('Zorvath', PERSON) only.
    Without U+FE0F each name is masked whole."""
    cases = [
        (f"Our client {SCALES}{{}}Zorvath Quillin called about the invoice.", "Zorvath Quillin"),
        (f"{PHONE}{{}}Mirela Okonkwo signed the contract.", "Mirela Okonkwo"),
        (f"{ENVELOPE}{{}}Taviel Brandt signed the contract.", "Taviel Brandt"),
        (f"{ARROW}{{}}Selene Marchetti signed the contract.", "Selene Marchetti"),
        ("Our client Zorvath Quil{}lin called about the invoice.", "Zorvath Quil{}lin"),
    ]
    assert leaks([(t.format(""), v.format("")) for t, v in cases]) == []  # control
    assert leaks([(t.format(VS16), v.format(VS16)) for t, v in cases]) == []


# ------------------------------------------------------------------ which model reads what


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_paragraphs_separated_by_cr_form_feed_or_paragraph_separator() -> None:
    """script._PARAGRAPH_BREAK knows only a blank line made with \\n. Classic-Mac CR text,
    a pdftotext page break and U+2029 leave both paragraphs as one, routed to the Russian
    model (more letters), which returned only the three Russian entities. With \\n\\n
    every English value is masked. `safeguard scan` on such a file shows the same."""
    control = [(f"{EN}\n\n{RU}", value) for value in EN_VALUES]
    assert leaks(control) == []
    separators = ["\r\r", f"\n{FORM_FEED}", f"{FORM_FEED}\n", PARAGRAPH_SEPARATOR]
    assert leaks([(f"{EN}{sep}{RU}", value) for sep in separators for value in EN_VALUES]) == []


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_lines_in_two_scripts_separated_by_one_line_break() -> None:
    """A chat box sends Enter as one \\n. An English request above a pasted Russian clause is
    one 'paragraph' to the router and goes to the Russian model: detect() returned only
    the Russian entities. A blank line between them masks the English name and company.
    (Next to the known per-paragraph limit, but the user sees two paragraphs here.)"""
    request = "Please check this clause for Zorvath Quillin of Brightwater Analytics:"
    values = ("Zorvath Quillin", "Brightwater Analytics")
    assert leaks([(f"{request}\n\n{RU}", value) for value in values]) == []  # control
    assert leaks([(f"{request}\n{RU}", value) for value in values]) == []


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_a_date_alone_in_its_own_paragraph() -> None:
    """A paragraph with no letters is 'und' and goes to no model, even between English
    paragraphs. Forms extracted from PDFs often put a label and its value in separate
    blocks: detect() returned []. On one line with its label each date is a DATE."""
    controls = [
        ("DOB: 1985-03-12", "1985-03-12"),
        ("Date of incident: 2023-08-30", "2023-08-30"),
        ("Admitted: 2024-02-14\nDischarged: 2024-02-19", "2024-02-19"),
    ]
    assert leaks(controls) == []
    split = [
        ("DOB:\n\n1985-03-12", "1985-03-12"),
        ("Date of incident:\n\n2023-08-30", "2023-08-30"),
        ("Admitted:\n\n2024-02-14\n\nDischarged:\n\n2024-02-19", "2024-02-19"),
    ]
    assert leaks(split) == []


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_russian_dates_are_never_detected() -> None:
    """ru_core_news_lg has no DATE label (PER, LOC, ORG only) and nothing else finds dates,
    so no Russian date is ever masked, numeric or written out; SECURITY.md does not say
    so. detect() returned [] or only the place. The English model finds each of these
    in English ('12 March 1985', '3 May 2024')."""
    cases = [
        ("Дата рождения: 12.03.1985", "12.03.1985"),
        ("Пациент родился 12 марта 1985 года в Новосибирске.", "12 марта 1985"),
        ("Договор заключён 3 мая 2024 г. в Новосибирске.", "3 мая 2024"),
    ]
    assert leaks(cases) == []


# ------------------------------------------------------------------ crashes and time


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_lone_surrogate_does_not_crash_detect() -> None:
    """JSON may carry '\\ud83d' (an emoji cut in half); json.loads gives a lone surrogate.
    spaCy's tokenizer encodes every token to UTF-8 and raised UnicodeEncodeError
    ('surrogates not allowed') out of detect(). With NER off the same text returns []."""
    text = f"Our client Zorvath Quillin called {LONE_SURROGATE} about the invoice."
    assert leaks([(text, "Zorvath Quillin")]) == []


def test_a_long_run_without_whitespace_is_routed_in_linear_time() -> None:
    """script._ADDRESS (r'\\S*(?:@|://|www\\.)\\S*') backtracks over every start in a run with
    no whitespace and no address in it: quadratic. segments() runs whenever ner='auto',
    models or not. A 40 KB data URI in a chat prompt took ~7 s here (100 KB ~45 s); with
    ner='off' it takes ~0.2 s."""
    blob = base64.b64encode(bytes(range(256)) * 118).decode()[:40_000]
    text = f"Please describe this scan: ![scan](data:image/png;base64,{blob})"
    started = time.perf_counter()
    detect(text)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"{elapsed:.2f}s for {len(text)} characters"
