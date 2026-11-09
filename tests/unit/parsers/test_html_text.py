from gateway.parsers.html_text import to_text


def test_block_tags_break_lines_and_inline_text_joins() -> None:
    html = "<html><body><p>Dear <b>Anna</b>,</p><div>line two<br>line three</div></body></html>"
    assert to_text(html) == "Dear Anna,\nline two\nline three"


def test_entities_are_decoded_and_whitespace_collapsed() -> None:
    assert to_text("<p>a&nbsp;&amp;   b\n  c &lt;x&gt;</p>") == "a\xa0& b c <x>"


def test_script_style_and_head_are_skipped() -> None:
    html = "<head><title>t</title><style>p{}</style></head><script>var x=1</script><p>kept</p>"
    assert to_text(html) == "kept"


def test_table_cells_are_tab_separated() -> None:
    html = "<table><tr><td>Name</td><td>SSN</td></tr><tr><td>Anna</td><td>1</td></tr></table>"
    assert to_text(html) == "Name\tSSN\nAnna\t1"


def test_pre_keeps_its_text_as_written() -> None:
    assert to_text("<pre>a  b\nc   d</pre>") == "a  b\nc   d"


def test_attributes_are_not_text() -> None:
    assert to_text('<a href="mailto:anna@example.org">write</a>') == "write"
