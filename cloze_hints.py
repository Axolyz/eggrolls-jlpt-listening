"""Constrain the written target of each enabled vocabulary cloze."""

from html import escape, unescape
import re

CLOZE = re.compile(r"\(\(c(\d+)::([\s\S]+?\)*)\)\)")
KANA = re.compile(r"[ぁ-ゖァ-ヺ]")
INITIALS = {
    char: initial
    for initial, kana in (
        ("k", "かきくけこ"), ("g", "がぎぐげご"), ("s", "さすせそ"), ("sh", "し"),
        ("z", "ざずぜぞづ"), ("j", "じぢ"), ("t", "たてと"), ("ch", "ち"), ("ts", "つ"),
        ("d", "だでど"), ("n", "なにぬねの"), ("h", "はひへほ"), ("f", "ふ"),
        ("b", "ばびぶべぼ"), ("p", "ぱぴぷぺぽ"), ("m", "まみむめも"),
        ("y", "やゆよ"), ("r", "らりるれろ"), ("w", "わ"), ("v", "ゔ"),
    )
    for char in kana
}
HINT_PART = re.compile(r'<span data-jlpt-answer-hint="([12])">(.*?)</span>', re.S)
AUTHOR_CUES = {
    "固い": "填搭配「結束」的形容词，用表示「牢固」的汉字加一个送假名，读音首辅音 k",
    "かける": "填「拨出电话」的固定搭配，三个平假名，首辅音 k",
    "出る": "填「接听电话」的固定搭配，辞书形读音首辅音 d",
    "渋滞": "这里借「堵车」形容工序堵住，填可接后面动词的双汉字名词，读音首辅音 j",
    "見る": "填「照料」搭配用的动词辞书形，汉字对应中文的「见」，读音首辅音 m",
    "つく": "填表示「有了着落」的自动词，两个平假名，首辅音 ts",
    "利かない": "填表示「不起作用、行不通」的动词否定形，汉字取「利益」的第一个字，辞书形读音首辅音 k",
    "拾う": "填与「捡到」同一个动词的辞书形，汉字加一个送假名，读音首辅音 h",
    "明ける": "填也用于「天亮」的自动词辞书形，读音从「あ」开始",
    "寄る": "填表示「顺路停一下」的自动词辞书形，读音首辅音 y",
    "ない": "填表示不存在的常体非过去否定形式，两个平假名",
    "取り消す": "填字面由「拿取」和「消除」组成的四字复合动词，两个汉字分别带送假名，读音首辅音 t",
}
PARTICLES = {
    "に": "用标示动作的指向、对象或匹配目标的格助词，首辅音 n",
    "を": "用标示动词直接对象的格助词",
}
PAIR_EXAMPLES = {
    "休む": ("自动词", ""),
    "終わる": ("自动词", ""),
    "出る": ("自动词", "今天早上很早就离开了家"),
    "進む": ("自动词的命令形", ""),
    "動く": ("自动词", ""),
    "変わる": ("自动词", ""),
    "向ける": ("表示朝某地出发的固定接续", ""),
    "終える": ("他动词", ""),
    "積もる": ("自动词", ""),
    "澄ます": ("他动词", ""),
    "直る": ("自动词", "文章里的错处得到修正"),
}
# These lexical links cross senses. The cue should target the use in the
# example, without presenting the linked word as a strict transitivity pair.
CROSS_SENSES = {
    "晴れる", "晴らす", "過ぎる", "過ごす", "逃れる", "逃す", "切らす", "もむ", "もめる",
}
VISIBLE_HINT_CSS = '''
.JLPTSurface .JLPTHint[data-cloze-hint="1"] [data-jlpt-answer-hint="2"],
.JLPTSurface .JLPTHint[data-cloze-hint="2"] [data-jlpt-answer-hint="1"] {
  display: none;
}
'''


def reading_initial(kana):
    first = kana[:1]
    if not first or not KANA.fullmatch(first):
        return ""
    if "ァ" <= first <= "ヶ":
        first = chr(ord(first) - 96)
    if initial := INITIALS.get(first):
        return f"读音首辅音 {initial}"
    return f"读音从「{first}」开始"


def cloze_parts(body, normalize):
    groups = {}
    for match in CLOZE.finditer(body):
        number, content = match.groups()
        raw = content.split("::", 1)[0]
        target = normalize(raw)
        if target:
            groups.setdefault(int(number), []).append((target, raw, normalize(body[match.end():])))
    return groups


def focus_spelling(target, reading):
    if len(target) == 1:
        script = "汉字" if not KANA.search(target) else "假名"
        length = f"填一个{script}"
    elif all(KANA.fullmatch(char) for char in target):
        length = f"填 {len(target)} 个假名"
    else:
        length = f"填 {len(target)} 字，按原句用汉字写法"
    initial = reading_initial(reading)
    if len(target) == 1 and KANA.search(target) and target in initial:
        initial = ""
    return "，".join(filter(None, (length, initial)))


def form_in_sentence(suffix):
    for verb_tail, detail in (
        ("なく", "接题面已有的否定词尾"),
        ("ない", "接题面已有的否定词尾"),
        ("なけれ", "接题面已有的否定条件词尾"),
        ("ました", "接题面已有的礼貌过去词尾"),
        ("ません", "接题面已有的礼貌否定词尾"),
        ("ます", "接题面已有的礼貌词尾"),
        ("て", "接题面已有的连接形词尾"),
        ("で", "接题面已有的连接形词尾"),
        ("た", "接题面已有的过去词尾"),
        ("だ", "接题面已有的过去词尾"),
    ):
        if suffix.startswith(verb_tail):
            return detail
    return ""


def author_hint(prompt, target):
    if target in PARTICLES:
        return PARTICLES[target]
    if target in AUTHOR_CUES:
        return AUTHOR_CUES[target]
    raise ValueError(f"{prompt}: no audited cue for the authored answer {target!r}")


def pack_hints(groups, base, make_cue):
    output = []
    for number, parts in groups.items():
        cues = [make_cue(target, raw, after) for target, raw, after in parts]
        if len(cues) > 1:
            cues = [f"第 {index} 空 {cue}" for index, cue in enumerate(cues, start=1)]
        hint = "。".join(filter(None, (base.rstrip("。"), *cues))) + "。"
        for target, _, _ in parts:
            if target in hint and (len(target) > 1 or target in PARTICLES):
                raise ValueError(f"Cloze cue gives away {target!r}: {hint}")
        output.append(f'<span data-jlpt-answer-hint="{number}">{escape(hint)}</span>')
    return "".join(output)


def pair_hint(groups, base, *, head, role, alternatives, reading):
    example_role, translation = PAIR_EXAMPLES.get(head, (role, ""))
    role = {"自動": "自动词", "他動": "他动词"}.get(example_role, example_role)
    if role not in {"自动词", "他动词", "自动词的命令形", "表示朝某地出发的固定接续"}:
        raise ValueError(f"{head}: unknown role in the cloze example")

    def cue(target, raw, suffix):
        reference = next((word for word in alternatives if target not in word), "")
        if reference:
            relation = "参照关联词" if head in CROSS_SENSES else "参照配对词"
            anchor = f"{relation}「{reference}」，填{role}"
        else:
            anchor = f"填{role}"
        spelling = focus_spelling(target, reading)
        if head in {"混ざる", "混じる"}:
            first_kana = KANA.search(target)[0]
            spelling += f"，首个假名为「{first_kana}」，汉字取「交错」的第一个字"
        elif reference and target[0] == reference[0] and not KANA.match(target):
            spelling += "，沿用参照词开头的汉字"
        if head == "向ける":
            anchor = "这里表示朝某地出发，填该固定接续里的动词"
        surface = form_in_sentence(suffix) if reference == "" or head == "向ける" else ""
        return "。".join(filter(None, (anchor, spelling, surface)))

    return pack_hints(groups, translation or base, cue)


def hint_text_for(markup, number):
    return next((unescape(value) for n, value in HINT_PART.findall(markup) if int(n) == number), markup)
