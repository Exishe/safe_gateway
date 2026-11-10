"""What a redacted .docx may keep: an allowlist of elements and attributes per part (ADR 0018).

Anything not listed here is removed from the copy, so a construct this module has never
seen fails closed: it is dropped, not copied. The lists hold structure and formatting
whose values are typed by the schema (numbers, enumerations, colours, ids); elements
whose values are free text (bookmark names, content-control aliases and tags, form-field
defaults and help text, table captions, mail-merge settings, style aliases) are not on
them.
"""

import re

from docx.oxml.ns import qn

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _w(*names: str) -> frozenset[str]:
    return frozenset(qn(f"w:{name}") for name in names)


# Text a reader sees; these are the units' pieces and are masked (ADR 0017).
TEXT = _w("t", "delText", "instrText", "delInstrText")

# Content that cannot be read as text: removed, and counted in the copy's notes.
CONTENT = _w("drawing", "pict", "object", "altChunk", "subDoc") | {
    f"{{{_MC}}}AlternateContent",
    f"{{{_MATH}}}oMath",
    f"{{{_MATH}}}oMathPara",
}

# Containers replaced by their children: the text stays, the wrapper and its
# properties (link targets, field instructions, aliases, tags, data bindings) go.
UNWRAPPED = _w("hyperlink", "fldSimple", "sdt", "sdtContent", "customXml", "smartTag", "dir", "bdo")

_RUN_PROPERTIES = _w(
    "rPr", "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike",
    "dstrike", "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish",
    "webHidden", "color", "spacing", "w", "kern", "position", "sz", "szCs", "highlight",
    "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang",
    "eastAsianLayout", "specVanish", "oMath", "rPrChange",
)  # fmt: skip
_PARAGRAPH_PROPERTIES = _w(
    "pPr", "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl",
    "numPr", "ilvl", "numId", "suppressLineNumbers", "pBdr", "top", "left", "bottom",
    "right", "start", "end", "between", "bar", "tabs", "tab", "suppressAutoHyphens",
    "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN",
    "bidi", "adjustRightInd", "contextualSpacing", "mirrorIndents", "suppressOverlap",
    "jc", "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId",
    "cnfStyle", "ind", "pPrChange",
)  # fmt: skip
_SECTION = _w(
    "sectPr", "headerReference", "footerReference", "footnotePr", "endnotePr", "type",
    "pgSz", "pgMar", "paperSrc", "pgBorders", "lnNumType", "pgNumType", "cols", "col",
    "formProt", "vAlign", "noEndnote", "titlePg", "rtlGutter", "docGrid", "sectPrChange",
    "pos", "numFmt", "numStart", "numRestart",
)  # fmt: skip
_TABLE = _w(
    "tbl", "tblPr", "tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
    "tblStyleColBandSize", "tblW", "tblCellSpacing", "tblInd", "tblBorders", "tblLayout",
    "tblCellMar", "tblLook", "tblGrid", "gridCol", "tr", "trPr", "gridBefore", "gridAfter",
    "wBefore", "wAfter", "cantSplit", "trHeight", "tblHeader", "hidden", "tc", "tcPr",
    "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "insideH", "insideV", "tl2br",
    "tr2bl", "noWrap", "tcMar", "tcFitText", "hideMark", "tblPrEx", "tblPrChange",
    "trPrChange", "tcPrChange", "tblGridChange", "tblPrExChange", "cellIns", "cellDel",
    "cellMerge",
)  # fmt: skip
_FORMATTING = _RUN_PROPERTIES | _PARAGRAPH_PROPERTIES | _SECTION | _TABLE

_STORY_STRUCTURE = _w(
    "document", "body", "hdr", "ftr", "footnotes", "footnote", "endnotes", "endnote",
    "comments", "comment", "p", "r", "tab", "br", "cr", "noBreakHyphen", "softHyphen", "sym",
    "ptab", "lastRenderedPageBreak", "pgNum", "fldChar", "footnoteReference",
    "endnoteReference", "commentReference", "annotationRef", "footnoteRef", "endnoteRef",
    "separator", "continuationSeparator", "commentRangeStart", "commentRangeEnd", "ins",
    "del", "moveFrom", "moveTo",
)  # fmt: skip
# Story parts: the body, headers, footers, notes and comments.
STORY = _FORMATTING | TEXT | _STORY_STRUCTURE

STYLES = _FORMATTING | _w(
    "styles", "docDefaults", "rPrDefault", "pPrDefault", "style", "name", "basedOn",
    "next", "link", "autoRedefine", "uiPriority", "semiHidden", "unhideWhenUsed",
    "qFormat", "locked", "tblStylePr",
)  # fmt: skip

NUMBERING = _FORMATTING | _w(
    "numbering", "abstractNum", "nsid", "multiLevelType", "tmpl", "styleLink",
    "numStyleLink", "lvl", "start", "lvlRestart", "isLgl", "suff", "lvlText", "lvlJc",
    "legacy", "num", "abstractNumId", "lvlOverride", "startOverride", "numIdMacAtCleanup",
)  # fmt: skip

SETTINGS = _w(
    "settings", "zoom", "proofState", "defaultTabStop", "characterSpacingControl", "compat",
    "compatSetting", "decimalSymbol", "listSeparator", "themeFontLang", "clrSchemeMapping",
    "footnotePr", "endnotePr", "footnote", "endnote", "pos", "numFmt", "numStart",
    "numRestart", "evenAndOddHeaders", "mirrorMargins", "gutterAtTop", "trackRevisions",
    "doNotTrackMoves", "doNotTrackFormatting", "updateFields", "useFELayout",
    "autoHyphenation", "consecutiveHyphenLimit", "hyphenationZone", "doNotHyphenateCaps",
    "bordersDoNotSurroundHeader", "bordersDoNotSurroundFooter",
)  # fmt: skip

FONTS = _w("fonts", "font", "panose1", "charset", "family", "pitch", "sig", "notTrueType")

_TYPED_ATTRIBUTES = _w(
    "val", "w", "h", "type", "color", "themeColor", "themeTint", "themeShade", "fill",
    "themeFill", "themeFillTint", "themeFillShade", "sz", "space", "shadow", "frame", "ascii",
    "hAnsi", "eastAsia", "cs", "hint", "asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme",
    "bidi", "before", "after", "beforeLines", "afterLines", "beforeAutospacing",
    "afterAutospacing", "line", "lineRule", "left", "right", "start", "end", "hanging",
    "firstLine", "leftChars", "rightChars", "startChars", "endChars", "hangingChars",
    "firstLineChars", "top", "bottom", "header", "footer", "gutter", "orient", "code", "id",
    "pos", "leader", "fldCharType", "fldLock", "dirty", "author", "initials", "char", "font",
    "fmt", "restart", "countBy", "distance", "chapStyle", "chapSep", "num", "equalWidth",
    "sep", "linePitch", "charSpace", "hRule", "firstRow", "lastRow", "firstColumn",
    "lastColumn", "noHBand", "noVBand", "oddVBand", "evenVBand", "oddHBand", "evenHBand",
    "firstRowFirstColumn", "firstRowLastColumn", "lastRowFirstColumn", "lastRowLastColumn",
    "styleId", "customStyle", "default", "abstractNumId", "numId", "ilvl", "tplc",
    "tentative", "display", "offsetFrom", "zOrder", "dropCap", "lines", "wrap", "hAnchor",
    "vAnchor", "x", "xAlign", "y", "yAlign", "hSpace", "vSpace", "anchorLock",
    "leftFromText", "rightFromText", "topFromText", "bottomFromText", "tblpX", "tblpY",
    "tblpXSpec", "tblpYSpec", "horzAnchor", "vertAnchor", "percent", "combine",
    "combineBrackets", "vert", "vertCompress", "column", "grammar", "spelling", "accent1",
    "accent2", "accent3", "accent4", "accent5", "accent6", "bg1", "bg2", "t1", "t2",
    "hyperlink", "followedHyperlink", "csb0", "csb1", "usb0", "usb1", "usb2", "usb3",
    "vMerge", "vMergeOrig", "legacy", "legacySpace", "legacyIndent", "colFirst", "colLast",
)  # fmt: skip
# Attributes kept on an allowed element. Their values are typed by the schema; w:author
# and w:initials are kept only to be replaced (`Redacted`, empty).
ATTRIBUTES = frozenset({qn("xml:space"), qn("r:id")}) | _TYPED_ATTRIBUTES

# Settings for compatibility: kept only with a name Word itself writes.
COMPAT_NAMES = frozenset(
    {
        "compatibilityMode", "overrideTableStyleFontSizeAndJustification",
        "enableOpenTypeFeatures", "doNotFlipMirrorIndents",
        "differentiateMultirowTableHeaders", "useWord2013TrackBottomHyphenation",
        "allowHyphenationAtTrackBottom", "allowTextAfterFloatingTableBreak",
    }
)  # fmt: skip
COMPAT_URI = "http://schemas.microsoft.com/office/word"

# Elements whose w:val names a style: rewritten to the copy's own style ids.
STYLE_REFERENCES = _w(
    "pStyle", "rStyle", "tblStyle", "basedOn", "next", "link", "numStyleLink", "styleLink"
)
# ---------------------------------------------------------------- attribute values
# A kept attribute's value is checked against a strict pattern for its kind; a value that
# does not match is dropped (or, for a font name, replaced), so free text hidden in a typed
# attribute (`<w:lang w:val="a@b.org"/>`) cannot reach the copy (ADR 0018, 2026-11-10).
DEFAULT_FONT = "Calibri"
_NUMBER = re.compile(r"-?\d{1,9}(?:\.\d{1,4})?%?")
_HEX = re.compile(r"[0-9A-Fa-f]{2,8}")
_LANGUAGE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")
# Font names are free text, so a pattern cannot tell a person's name from a family. Only
# these common families pass; any other name becomes DEFAULT_FONT (ADR 0018, 2026-11-10).
_FONT_FAMILIES = (
    "Calibri",
    "Calibri Light",
    "Cambria",
    "Cambria Math",
    "Aptos",
    "Aptos Display",
    "Aptos Narrow",
    "Aptos Serif",
    "Arial",
    "Arial Black",
    "Arial Narrow",
    "Arial Unicode MS",
    "Helvetica",
    "Helvetica Neue",
    "Times New Roman",
    "Times",
    "Courier New",
    "Courier",
    "Georgia",
    "Verdana",
    "Tahoma",
    "Trebuchet MS",
    "Segoe UI",
    "Segoe UI Light",
    "Segoe UI Semibold",
    "Segoe UI Symbol",
    "Consolas",
    "Garamond",
    "Book Antiqua",
    "Palatino Linotype",
    "Century Gothic",
    "Comic Sans MS",
    "Lucida Console",
    "Candara",
    "Constantia",
    "Corbel",
    "Symbol",
    "Wingdings",
    "Wingdings 2",
    "Wingdings 3",
    "Webdings",
    "Liberation Sans",
    "Liberation Sans Narrow",
    "Liberation Serif",
    "Liberation Mono",
    "DejaVu Sans",
    "DejaVu Sans Condensed",
    "DejaVu Sans Mono",
    "DejaVu Serif",
    "DejaVu Serif Condensed",
    "Noto Sans",
    "Noto Serif",
    "Noto Sans Mono",
    "Noto Sans Armenian",
    "Noto Serif Armenian",
    "Noto Sans Cyrillic",
    "Noto Serif Cyrillic",
    "PT Sans",
    "PT Sans Narrow",
    "PT Serif",
    "PT Mono",
    "Open Sans",
    "Roboto",
    "Lato",
    "Ubuntu",
    "Cantarell",
    "Menlo",
    "Monaco",
    "Geneva",
    "Lucida Grande",
    "MS Gothic",
    "MS Mincho",
)
# Looked up with case and spacing normalised; the copy gets the listed spelling.
_FONT_LOOKUP = {" ".join(name.split()).casefold(): name for name in _FONT_FAMILIES}
_ENUMS = frozenset(
    [
        "auto",
        "none",
        "nil",
        "true",
        "false",
        "on",
        "off",
        "default",
        "preserve",
        "single",
        "double",
        "thick",
        "dotted",
        "dash",
        "dotDash",
        "dotDotDash",
        "wave",
        "dashed",
        "words",
        "thin",
        "thinThickSmallGap",
        "thickThinSmallGap",
        "thickThin",
        "thinThick",
        "triple",
        "wavy",
        "left",
        "right",
        "center",
        "both",
        "distribute",
        "justified",
        "start",
        "end",
        "top",
        "bottom",
        "baseline",
        "superscript",
        "subscript",
        "char",
        "line",
        "lines",
        "text",
        "page",
        "margin",
        "column",
        "paragraph",
        "frame",
        "portrait",
        "landscape",
        "continuous",
        "nextPage",
        "evenPage",
        "oddPage",
        "nextColumn",
        "atLeast",
        "exact",
        "decimal",
        "upperRoman",
        "lowerRoman",
        "upperLetter",
        "lowerLetter",
        "ordinal",
        "cardinal",
        "bullet",
        "hex",
        "tab",
        "space",
        "nothing",
        "normal",
        "fixed",
        "autofit",
        "dxa",
        "pct",
        "pt",
        "eastAsia",
        "cs",
        "ascii",
        "hAnsi",
        "hyperlink",
        "followedHyperlink",
        "dark1",
        "light1",
        "dark2",
        "light2",
        "text1",
        "text2",
        "background1",
        "background2",
        "accent1",
        "accent2",
        "accent3",
        "accent4",
        "accent5",
        "accent6",
        "majorHAnsi",
        "minorHAnsi",
        "majorEastAsia",
        "minorEastAsia",
        "majorBidi",
        "minorBidi",
        "majorAscii",
        "minorAscii",
        "begin",
        "separate",
        "hybridMultilevel",
        "multilevel",
        "singleLevel",
        "yellow",
        "green",
        "cyan",
        "magenta",
        "red",
        "blue",
        "black",
        "white",
        "darkBlue",
        "darkCyan",
        "darkGreen",
        "darkMagenta",
        "darkRed",
        "darkYellow",
        "darkGray",
        "lightGray",
        "clear",
        "solid",
        "horzStripe",
        "vertStripe",
        "diagStripe",
        "pct5",
        "pct10",
        "pct20",
        "pct25",
        "pct30",
        "pct40",
        "pct50",
        "pct60",
        "pct70",
        "pct75",
        "pct80",
        "pct90",
        "allCaps",
        "dot",
        "hyphen",
        "underscore",
        "middleDot",
        "heavy",
        "mirror",
        "inside",
        "outside",
        "firstPage",
        "lastPage",
        "wholePage",
        "unlocked",
        "locked",
        "topLeft",
        "topRight",
        "bottomLeft",
        "bottomRight",
    ]
)
_FONT_ATTRIBUTES = _w("ascii", "hAnsi", "eastAsia", "cs", "font")
_LANGUAGE_ELEMENTS = _w("lang", "themeFontLang")
# Judged later in the pipeline instead: style ids and references are replaced by the
# copy's own ids, a style name is a built-in or `Custom N`, a list label is cut by
# `_clean_numbering`, author and initials are replaced, and r:id is checked against the
# kept relationships.
_DEFERRED_ATTRIBUTES = frozenset({qn("w:styleId"), qn("r:id"), qn("w:author"), qn("w:initials")})
_DEFERRED_VALUES = STYLE_REFERENCES | _w("name", "lvlText")


def _scalar_ok(value: str) -> bool:
    return bool(_NUMBER.fullmatch(value) or _HEX.fullmatch(value) or value in _ENUMS)


def _font_or_default(value: str) -> str:
    return _FONT_LOOKUP.get(" ".join(value.split()).casefold(), DEFAULT_FONT)


def keep_attribute(element_tag: str, name: str, value: str, *, extra: bool = False) -> str | None:
    """The value to keep for this attribute, or None to drop it.

    One central check: a language tag on `w:lang`, a listed font family on a font
    attribute (any other name becomes the default font), and elsewhere only a number, a hex
    value or a word from the schema's enumerations. `extra` marks a per-element extra
    (a font's name, a compat setting's name and URI; `_clean_settings` judges the latter).
    """
    if extra:
        return _font_or_default(value) if element_tag == qn("w:font") else value
    if name in _FONT_ATTRIBUTES:
        return _font_or_default(value)
    deferred = name in _DEFERRED_ATTRIBUTES or (
        name == qn("w:val") and element_tag in _DEFERRED_VALUES
    )
    if deferred:
        return value
    ok = _LANGUAGE.fullmatch(value) if element_tag in _LANGUAGE_ELEMENTS else _scalar_ok(value)
    return value if ok else None
