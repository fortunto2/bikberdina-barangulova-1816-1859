"""Build the person-level CSV from the working transcripts of the 1816 and 1859 revision lists.

Usage: uv run parse.py <transcripts-dir> > revizii-1816-1859.csv

The transcripts are working Markdown files, one per case, written in five slightly different
layouts. This script reads only the household lists in them; notes, tables and commentary are
skipped. Every row keeps the transcribed line as written (column `запись`), so whatever the
heuristics get wrong in `имя` or the age columns can be checked against it.
"""

import csv
import re
import sys
from pathlib import Path

SITE = "https://basharchive.ru/census/reviz/"
# (delo, village) -> basharchive card id; 706 Бикбердина has two cards, see README
CARDS = {
    ("145", "Барангулова"): "72344",
    ("145", "Бикбердина"): "72348",
    ("146", "Бикбердина"): "68501",
    ("161", "Бикбердина"): "84119",
    ("170", "Бикбердина"): "54991",
    ("170", "Ишмуратова"): "55970",
    ("704", "Бикбердина"): "50165",
    ("706", "Барангулова"): "82857",
}

FILES = {
    "1816_bikberda_i_burangul.md": dict(год="1816", кантон="9-й башкирский"),
    "1859_bikberda_d704.md": dict(год="1859", дело="704", деревня="Бикбердина", юрта="6-я", кантон="3-й башкирский"),
    "1859_bikberda_d706a_i_1816_d146.md": dict(),
    "1859_bikberda_d706b.md": dict(год="1859", дело="706", деревня="Бикбердина", юрта="2-я", кантон="3-й башкирский"),
    "1859_burangul_d706.md": dict(год="1859", дело="706", деревня="Барангулова", юрта="1-я", кантон="3-й башкирский"),
}

# headings that open a data part of a file: (pattern, context it sets)
SECTIONS = [
    (r"Барангулова, дело 145", dict(дело="145", деревня="Барангулова")),
    (r"Бикбердина, дело 145", dict(дело="145", деревня="Бикбердина")),
    (r"Бикбердина, дело 161", dict(дело="161", деревня="Бикбердина")),
    (r"Бикбердина, дело 170", dict(дело="170", деревня="Бикбердина")),
    (r"ДЕЛО 706а", dict(год="1859", дело="706", деревня="Бикбердина", юрта="9-я", кантон="3-й башкирский")),
    (r"ДЕЛО 146", dict(год="1816", дело="146", деревня="Бикбердина", юрта="", кантон="9-й башкирский")),
    (r"ДВОРЯНЕ", dict(сословие="башкиры дворянского происхождения")),
    (r"ПРОСТОГО ПРОИСХОЖДЕНИЯ", dict(сословие="башкиры")),
]
SKIP = re.compile(
    r"НАШИ ФАМИЛИИ|Соответствие копий|Сводка|Шапка|Техническ|Описание документа|Замечание|ИТОГОВАЯ"
    r"|Что учесть|Отчество самого|Почему не нашлось|Два вывода|Как читалось|побайтовые дубликаты"
    r"|не относится|пустой лист",
    re.I,
)
AGE = r"(?:\d{1,3}½?|½|нов\w*\.?|новорожд\w*\.?|рожд\w*\.?)"
DEATH = re.compile(r"(?:†\s*|(?:умер|умерла|помер|померла)\w*\s+в\s+)(\d{4})")
FEMALE = re.compile(r"\b(жена|жёны|жены|дочь|дочери|мать|сестра|сёстры|сестры|вдова|невестка|сноха|внучка)\b", re.I)
MALE = re.compile(r"\b(сын|сыновья|брат|братья|внук|внуки|племянник|отец|муж)\b", re.I)
RELATION_WORDS = {"брат", "сын", "сыновья", "жена", "жёны", "мать", "дочь", "дочери", "племянник", "от", "его",
                  "глава", "третий", "четвёртый", "другой", "внук", "сестра", "сёстры", "братья", "вдова"}


def clean(s: str) -> str:
    s = re.sub(r"\*+", "", s).replace("⚠️", "").replace("⭐", "")
    s = re.sub(r"\b[Нн]е наш\w*\b\.?", "", s)
    # transcribers wrote some names in capitals to make them stand out; the source does not
    s = re.sub(r"\b[А-ЯЁІѢ]{3,}[ЪЬ]?\b", lambda m: m.group(0).capitalize(), s)
    return re.sub(r"\s+", " ", s).strip(" .")


def ages(t: str) -> tuple[str, str, str]:
    died = (m.group(1) if (m := DEATH.search(t)) else "")
    if m := re.search(rf"({AGE})\s*(?:\(\?\))?\s*→\s*(\d{{1,3}}½?)", t):
        prev = m.group(1)
        return ("нов." if not prev[:1].isdigit() and prev != "½" else prev), m.group(2), died
    nums = re.findall(r"(?<![\d/№-])(\d{1,3}½?)(?:\(\?\))?(?![\d/-])", DEATH.sub("", t))
    if not nums:
        return ("нов." if re.search(r"\bнов|новорожд|\bрожд", t) else ""), "", died
    # a lone number before a death note is the age at the previous revision
    return (nums[0], "", died) if died else ("", nums[-1], died)


def split_name(t: str) -> tuple[str, str]:
    """(relation, name) from the part of the entry before the age."""
    cut = re.search(rf"(?:—\s*)?(?<![А-Яа-яЁёѢѣІі])(?:{AGE}(?![А-Яа-яЁё-])|†|~|умер|помер|умерла|померла)", t)
    pre = t[: cut.start()] if cut else t
    pre = pre.strip(" —,.")
    if " — " in pre:
        head, _, tail = pre.rpartition(" — ")
        if tail[:1].isupper() or tail.startswith("["):
            rel, pre = head, tail
        else:
            rel, pre = tail, head
    else:
        rel = ""
    if "," in pre:
        pre = pre.split(",")[0]
    toks, name = pre.split(), []
    for tok in reversed(toks):
        if tok[:1].isupper() or tok[:1] in "[…/(" or tok == "/":
            name.insert(0, tok)
        else:
            break
    while name and name[0].strip(":").lower() in RELATION_WORDS:
        name.pop(0)
    rel = " ".join([rel, " ".join(toks[: len(toks) - len(name)])]).strip(" :—,")
    return rel, " ".join(name).strip(" ,")


def sex_of(rel: str, default: str) -> str:
    rel = re.sub(r"от\s+(?:\S+\s+)?жен[ыё]\w*", "", rel)
    hits = [(m.start(), "ж") for m in FEMALE.finditer(rel)] + [(m.start(), "м") for m in MALE.finditer(rel)]
    return max(hits)[1] if hits else default


def entries(line: str):
    """One transcribed line may list several people: 'сыновья: А 3 → 11 · Б нов. → 6'."""
    parts = re.split(r"\s+·\s+|;\s+|(?<=\d)[.,]\s+(?=[А-ЯЁ])", line)
    prefix = ""
    for i, p in enumerate(parts):
        p = p.strip(" ;")
        if not p:
            continue
        if i == 0 and ":" in p and not p.startswith("("):
            prefix = p.split(":")[0]
            p = p.split(":", 1)[1].strip()
            if not p:
                continue
        yield prefix, p


COMMENT = re.compile(r"провер|читается|вероятн|\bсм\.|скан|кадр|копи[ия]|разреша|снимает вопрос|не отсканир", re.I)
NOT_PERSON = re.compile(r"^«|^Отметка|тот же человек|проверено|Всего|составил|сказкосоставитель|руку приложил|\bд\.\s?\d|снимает вопрос|^ниже —")
STRUCT = re.compile(r"^\s*(?:[-#|>]|\*\*\d|\*?Итого|Ж\.:|Глава:|Мужчин|Женщин|\*\*Мужской|\*\*Женский|\()")


def parse(path: Path, base: dict):
    ctx = dict(год="", дело="", деревня="", юрта="", кантон="", сословие="башкиры", кадр="", двор="")
    ctx |= base
    skip, skip_level, sex = True, 0, "м"
    lines = path.read_text().splitlines()
    rows: list[dict] = []

    def emit(text: str, female_line: bool = False):
        for prefix, body in entries(text):
            body_c, prefix_c = clean(body), clean(prefix)
            if not re.search(r"[А-ЯЁ\[]", body_c) and not re.search(r"\d", body_c):
                continue
            if COMMENT.search(body_c) and not re.search(r"→|†|\b\d{1,3}\b", body_c):
                continue
            if NOT_PERSON.search(body_c):
                continue
            rel, name = split_name(body_c)
            rel = " ".join(x for x in (prefix_c, rel) if x)
            prev, now, died = ages(body_c)
            s = "ж" if female_line else sex_of(rel or body_c.split(" — ")[0], sex)
            rows.append(dict(ctx, пол=s, кто_в_семье=rel, имя=name, возраст_прежний=prev,
                             возраст=now, год_смерти=died, запись=clean((prefix + ": " if prefix else "") + body)))

    buf: list[str] = []
    buf_female = False

    def flush():
        nonlocal buf
        if buf:
            emit(re.sub(r" ?; ", "; ", " ".join(buf)), buf_female)
        buf = []

    for raw in lines:
        line = raw.rstrip()
        if m := re.match(r"^(#{1,4})\s+(.*)", line):
            flush()
            level, title = len(m.group(1)), m.group(2)
            if not skip or level <= skip_level:
                skip = False
            for pat, sets in SECTIONS:
                if re.search(pat, title):
                    ctx |= sets
                    skip = False
            if SKIP.search(title):
                skip, skip_level = True, level
                continue
            if skip:
                continue
            if mm := re.search(r"(?:Лист|Л\.|л\.)\s*([\d/]+)?\s*(?:\((?:скан-)?([\w-]+)\)|— файл (\d+))?", title):
                sheet, scan, file_ = mm.groups()
                if "скан-" in title and not scan:
                    scan = re.search(r"скан-0*(\d+)", title).group(1)
                frame = scan or file_ or (sheet if sheet and len(sheet) == 4 else "")
                if frame.isdigit():
                    frame = frame.lstrip("0") or "0"
                if mm2 := re.search(r"л\. (\d{4}(?:–\d{4})?)\s*$", title):
                    frame = mm2.group(1)
                ctx["кадр"] = re.sub(r"\b0+(\d)", r"\1", frame)
            if "в деревне Ишмуратовой" in title:
                ctx["деревня"] = "Ишмуратова"
            if md := re.search(r"Двор\s+(?:№\s*)?(.+?)(?:\s+—|$)", title):
                label = md.group(1)
                label = re.sub(r"\s*/\s*№\s*", "/", label).replace(" (предыдущая деревня)", "").replace(" (продолжение)", "").replace(" (перепроверен)", "")
                ctx["двор"] = label.strip()
                if "предыдущая деревня" in title:
                    ctx["деревня"] = "неизвестна (лист 39 не отсканирован)"
                elif ctx["дело"] == "170" and ctx["деревня"].startswith("неизвестна"):
                    ctx["деревня"] = "Бикбердина"
                sex = "м"
            continue
        if skip:
            continue
        s = line.strip()
        if "/Users/" in s or "`" in s or s.startswith("Файлы:") or re.match(r"^(?:- )?\(", s):
            continue  # transcriber's own file paths
        if not s:
            flush()
            continue
        if re.match(r"^\*?Итого|^Итого|^\|", s) or s.startswith(">"):
            flush()
            continue
        if re.match(r"^\*\*Мужской пол|^Мужчины", s):
            flush(); sex = "м"; continue  # noqa: E702
        if re.match(r"^\*\*Женский пол|^Женщины", s):
            flush(); sex = "ж"; continue  # noqa: E702
        if s.startswith("**") and (mh := re.match(
                r"^(—?\d*/?\d+(?: \(\d+/\d+\))?)(\s*\(продолжение\))?(?:\s*—\s*двор .*)?\s*(.*)",
                s.replace("**", ""))):
            flush()
            if not mh.group(2):
                ctx["двор"], sex = mh.group(1), "м"
            if mh.group(3):
                buf, buf_female = [mh.group(3)], False
            continue
        if s.startswith("Глава:"):
            flush(); buf, buf_female = [s[6:].strip()], False; continue  # noqa: E702
        if s.startswith("Ж.:"):
            flush(); buf, buf_female = [s[3:].strip()], True; continue  # noqa: E702
        if s.startswith("- ") or s.startswith("-\t"):
            flush(); buf, buf_female = [s[2:].strip()], False; continue  # noqa: E702
        if buf and not STRUCT.match(raw):
            # wrapped continuation; a line that ends on an age closes an entry, so start a new one
            buf.append(("; " if re.search(r"[\d)]$", buf[-1]) else "") + s)
            continue
        flush()
    flush()
    return rows


COLS = ["год", "дело", "деревня", "юрта", "кантон", "сословие", "кадр", "двор", "пол", "имя", "кто_в_семье",
        "возраст_прежний", "возраст", "год_смерти", "запись", "карточка"]


def main():
    src = Path(sys.argv[1])
    out = csv.DictWriter(sys.stdout, COLS, extrasaction="ignore", lineterminator="\n")
    out.writeheader()
    for name, base in FILES.items():
        for r in parse(src / name, base):
            card = CARDS.get((r["дело"], r["деревня"]))
            r["карточка"] = SITE + card + "/" if card else ""
            out.writerow(r)


if __name__ == "__main__":
    main()
