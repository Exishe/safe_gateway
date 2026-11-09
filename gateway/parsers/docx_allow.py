"""What a redacted .docx may keep: an allowlist of elements and attributes per part (ADR 0018).

Anything not listed here is removed from the copy, so a construct this module has never
seen fails closed: it is dropped, not copied. The lists hold structure and formatting
whose values are typed by the schema (numbers, enumerations, colours, ids); elements
whose values are free text (bookmark names, content-control aliases and tags, form-field
defaults and help text, table captions, mail-merge settings, style aliases) are not on
them.
"""

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
