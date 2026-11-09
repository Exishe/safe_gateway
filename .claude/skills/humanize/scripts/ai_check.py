"""ai_check.py: flags AI-writing patterns (Wikipedia "Signs of AI writing") in English and Russian text.

Usage: python3 ai_check.py text.txt [--plain]
  --plain   the text goes somewhere Markdown doesn't render (email, messenger, Word): flag Markdown too.

Everything it prints is a candidate to look at, not a verdict. It can't judge whether a sentence
says something specific; read the text yourself for that.
"""
import re
import statistics
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
plain = "--plain" in sys.argv
text = open(args[0], encoding="utf-8").read() if args else sys.stdin.read()
low = text.lower()
issues = []


def add(kind, frag):
    issues.append((kind, " ".join(frag.split())[:90]))


def around(m, src, a=30, b=20):
    return src[max(0, m.start() - a):m.end() + b]


# 1. AI vocabulary, counted per paragraph (density is the signal)
VOCAB = [
    "additionally", "align with", "aligns with", "boast", "bolster", "crucial", "delve", "demystif",
    "emphasizing", "enduring", "enhanc", "foster", "garner", "highlighting", "highlights", "interplay",
    "intricate", "intricacies", "landscape", "meticulous", "navigat", "nuanced", "pivotal", "realm",
    "robust", "showcas", "tapestry", "testament", "underscor", "valuable", "vibrant", "vital",
    "comprehensive", "multifaceted", "holistic", "synergy", "paradigm", "embark", "journey",
    "deep dive", "dive into", "unpack", "leverag", "streamline", "ever-evolving", "fast-paced",
    "seamless", "elevate", "unlock", "unleash", "empower", "harness", "cutting-edge",
    "game-changer", "game changer", "revolutioniz", "world-class", "state-of-the-art", "bespoke",
    "tailored", "curated", "groundbreaking", "renowned", "nestled", "breathtaking", "stunning",
    "must-visit", "transformative", "innovative",
    # Russian
    "является", "являются", "представляет собой", "ключев", "уникальн", "инновацион", "широкий спектр",
    "комплексн", "погруз", "эффективн", "осуществля", "данный", "данная", "данное", "в рамках",
    "на сегодняшний день", "незаменим", "неотъемлем",
]
for i, para in enumerate([p for p in re.split(r"\n\s*\n", text) if p.strip()], 1):
    pl = para.lower()
    hits = [w for w in VOCAB if w in pl]
    if len(hits) >= 3:
        add(f"Paragraph {i}: {len(hits)} AI-vocabulary words, rewrite at least half", ", ".join(hits))
    elif hits:
        add(f"Paragraph {i}: AI vocabulary (fine alone, check it's the right word)", ", ".join(hits))

# 2. Stock phrases
PHRASES = {
    "testament to": "inflated significance", "plays a vital role": "inflated significance",
    "plays a crucial role": "inflated significance", "plays a key role": "inflated significance",
    "plays a pivotal role": "inflated significance", "plays a significant role": "inflated significance",
    "key turning point": "inflated significance", "pivotal moment": "inflated significance",
    "indelible mark": "inflated significance", "lasting legacy": "inflated significance",
    "deeply rooted": "inflated significance", "broader movement": "inflated significance",
    "reflects broader": "inflated significance", "setting the stage": "inflated significance",
    "evolving landscape": "inflated significance", "focal point": "inflated significance",
    "more than just": "inflated framing", "serves as": "copula avoidance: use is", "stands as": "copula avoidance: use is",
    "functions as": "copula avoidance", "in the heart of": "promotional", "look no further": "promotional",
    "to the next level": "promotional", "rich heritage": "promotional", "rich history": "promotional",
    "diverse array": "promotional: list them", "wide range of": "vague: list them",
    "it's important to note": "editorializing", "it is important to note": "editorializing",
    "important to remember": "editorializing", "it is worth noting": "editorializing",
    "it's worth noting": "editorializing", "worth mentioning": "editorializing", "notably,": "editorializing",
    "interestingly,": "editorializing", "keep in mind": "editorializing",
    "in conclusion": "summary ending", "in summary": "summary ending", "overall,": "summary ending",
    "ultimately,": "summary ending", "all in all": "summary ending", "at the end of the day": "summary ending",
    "despite its": "challenges formula?", "faces several challenges": "challenges formula",
    "the future of": "future-prospects formula?", "poised to": "future-prospects formula",
    "experts say": "vague attribution", "experts argue": "vague attribution", "studies show": "vague attribution",
    "many believe": "vague attribution", "critics argue": "vague attribution", "observers note": "vague attribution",
    "some argue": "vague attribution", "industry reports": "vague attribution",
    "while specific details": "knowledge-gap filler", "not widely documented": "knowledge-gap filler",
    "as of my last": "knowledge-cutoff talk", "as an ai": "chatbot residue",
    "on the one hand": "false balance?", "it can be argued": "hedging",
    "in today's": "formulaic opening", "in the ever": "formulaic opening", "when it comes to": "formulaic opening",
    "have you ever wondered": "formulaic opening", "imagine a world": "formulaic opening",
    "whether you're": "formulaic 'Whether you're X or Y'", "whether you are": "formulaic 'Whether you're X or Y'",
    "here's the thing": "colon reveal", "the bottom line": "colon reveal", "the best part?": "rhetorical reveal",
    "certainly!": "chatbot opener", "absolutely!": "chatbot opener", "great question": "chatbot opener",
    "i'd be happy to": "chatbot opener", "i hope this helps": "chatbot closer",
    "let me know if you have": "chatbot closer", "feel free to reach out": "chatbot closer",
    "happy coding": "chatbot closer", "here's a polished": "meta line inside text",
    "here is a revised": "meta line inside text",
    "contentreference": "tool artifact", "oaicite": "tool artifact", "oai_citation": "tool artifact",
    "turn0search": "tool artifact", "[web:": "tool artifact", "utm_source=chatgpt": "tool artifact",
    "utm_source=openai": "tool artifact", "↩": "tool artifact",
    # Russian
    "ключевую роль": "раздувание значимости", "важную роль": "раздувание значимости", "решающую роль": "раздувание значимости",
    "важно отметить": "назидательная рамка", "стоит отметить": "назидательная рамка",
    "следует отметить": "назидательная рамка", "следует подчеркнуть": "назидательная рамка",
    "в современном мире": "шаблонное начало", "в эпоху": "шаблонное начало", "в наше время": "шаблонное начало",
    "давайте разберёмся": "шаблонное начало", "давайте разберемся": "шаблонное начало",
    "давайте рассмотрим": "шаблонное начало", "в заключение": "итоговая концовка",
    "подводя итог": "итоговая концовка", "таким образом,": "итоговая концовка (проверить)",
    "надеюсь, это поможет": "концовка чат-бота", "если остались вопросы": "концовка чат-бота",
    "обращайтесь!": "концовка чат-бота", "эксперты считают": "размытая ссылка", "многие считают": "размытая ссылка",
}
for k, v in PHRASES.items():
    n = low.count(k)
    if n:
        add(f"{v} ×{n}", k)

# 3. Sentence shapes
for m in re.finditer(r"\b(not just|not only|isn't just|is not just|don't just|doesn't just|it's not about)\b[^.!?]{0,80}?(\bbut\b|\bit's\b|\bit is\b|, (we|it|they|you)\b)", text, re.I):
    add("Negative parallelism: state the second half directly", around(m, text, 0, 0))
for m in re.finditer(r"не (просто|только)[^.!?]{0,80}\b(а|но и)\b", text, re.I):
    add("Негативный параллелизм: скажите вторую часть прямо", around(m, text, 0, 0))
for m in re.finditer(r",\s+(highlighting|emphasizing|underscoring|showcasing|reflecting|demonstrating|illustrating|symbolizing|signaling|ensuring|contributing|marking|solidifying|cementing)\b[^.!?]*[.!?]", text, re.I):
    add("Trailing -ing analysis clause: delete it or make it a real sentence", around(m, text, 30, 0))
for m in re.finditer(r"\b[\w-]+, [\w-]+,? and [\w-]+\b", text):
    add("Triplet: is it really three things, or a reflex?", m.group())
for m in re.finditer(r"\b[\w-]+, [\w-]+ и [\w-]+\b", text):
    add("Тройка: точно три вещи, или по привычке?", m.group())
for m in re.finditer(r"\bfrom [\w' -]{2,30} to [\w' -]{2,30}", text, re.I):
    add("'from X to Y': is it a real range?", m.group())
for m in re.finditer(r"(?m)^[^\n]{0,60}\?\s*$", text):
    if len(m.group().split()) <= 8:
        add("Short question as heading/opener: rhetorical?", m.group())
for m in re.finditer(r"(?m)^(No|Not) [^.\n]{1,30}\. (No|Not) [^.\n]{1,30}\.", text):
    add("Outline of negatives", m.group())

# 4. Punctuation and formatting
em = text.count("—")
paras = max(1, len([p for p in re.split(r"\n\s*\n", text) if p.strip()]))
if em and em / paras > 0.5:
    add(f"Em dashes: {em} in {paras} paragraph(s), replace most with , : . or ( )", "—")
if re.search(r"[“”‘’]", text) and re.search(r"[\"']", text):
    add("Curly and straight quotes mixed", "pick one style")
emoji = re.findall(r"[\U0001F300-\U0001FAFF✅✨⭐➡✔]", text)
if emoji:
    add(f"Emoji ×{len(emoji)}", " ".join(emoji[:10]))
for m in re.finditer(r"(?m)^#{1,6} (.+)$", text):
    words = [w for w in m.group(1).split() if len(w) > 3]
    if len(words) >= 2 and all(w[0].isupper() for w in words):
        add("Title Case heading: use sentence case", m.group(1))
bold = re.findall(r"\*\*[^*]+\*\*", text)
if len(bold) > 3:
    add(f"Bold ×{len(bold)}: bold almost nothing", ", ".join(bold[:5]))
inline_hdr = re.findall(r"(?m)^\s*[-*•]\s+\*\*[^*]+:\*\*|^\s*[-*•]\s+\*\*[^*]+\*\*:", text)
if len(inline_hdr) >= 3:
    add(f"Inline-header bullet list ×{len(inline_hdr)}: prose or a plain list", inline_hdr[0])
if plain:
    md = re.findall(r"(?m)^#{1,6} |\*\*[^*]+\*\*|^\s*---\s*$|```", text)
    if md:
        add(f"Markdown in a plain-text destination ×{len(md)}: use the medium's own formatting or none", md[0])
for m in re.finditer(r"\[(your|insert|company|name|date|ваше|имя|название)[^\]]*\]|\b20\d\d-xx-xx\b", text, re.I):
    add("Unfilled placeholder", m.group())
if re.match(r"\s*subject:", low) is not None:
    add("'Subject:' line: only for emails", text.strip().splitlines()[0])

# 5. Rhythm
sents = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.split()) >= 3 and not s.lstrip().startswith(("#", "-", "*", "|"))]
lens = [len(s.split()) for s in sents]
if len(lens) >= 6:
    sd = statistics.pstdev(lens)
    if sd < 4:
        add(f"Uniform sentence length (sd {sd:.1f} words): mix short and long sentences", f"lengths {lens[:12]}")
for s in sents:
    if len(s.split()) > 35:
        add("Long sentence (>35 words): split it?", s[:80])

print(f"Sentences: {len(lens)}, avg words: {statistics.mean(lens) if lens else 0:.1f}, "
      f"sd: {statistics.pstdev(lens) if len(lens) > 1 else 0:.1f}, em dashes: {em}")
print(f"Candidates: {len(issues)}")
for k, f in issues:
    print(f"- {k}: {f}")
