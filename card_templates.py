"""Derive the listening note type from the upstream Japanese–Chinese layout."""

from pathlib import Path
import re


TEMPLATES = Path(__file__).parent / "templates"
SLOTS = range(1, 5)
CLOZE_ORDINALS = ("First", "Second")
REPLAY_ICON = (
    '<button type="button" class="replay-button soundLink" aria-label="播放音频">'
    '<svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="29"/>'
    '<path d="M56.502,32.301l-37.502,20.101l0.329,-40.804l37.173,20.703Z"/></svg></button>'
)

EXTRA_FIELDS = [
    "ReadingPrompt", "ReadingAnswer", "RemarkSC", "RemarkTC", "WordAudioFile",
    "WordListening", "WordMeaning", "WordHintSC", "WordHintTC",
    "ClozeText1", "ClozeHint1", "ClozeText2", "ClozeHint2",
    *(
        f"Cloze{group}{ordinal}{suffix}"
        for group in (1, 2)
        for ordinal in CLOZE_ORDINALS
        for suffix in ("", "Answer")
    ),
    *(
        f"{field}{slot}"
        for slot in SLOTS
        for field in ("Listening", "ListeningHintSC", "ListeningHintTC", "Meaning", "TargetDefSC", "TargetDefTC", "SentAudioFile")
    ),
]


def field(name):
    return "{{" + name + "}}"


def section(name, body, *, unless=False):
    return field(("^" if unless else "#") + name) + body + field("/" + name)


def bilingual(sc, tc):
    return (
        '<span lang="zh-Hans" data-jlpt-hans>' + field(sc) + '</span>'
        + section(tc, '<span lang="zh-Hant" data-jlpt-hant hidden>' + field(tc) + '</span>')
    )


def wrap_side(side, body):
    return (
        f'<main id="{side}Side" class="CardSide JLPTCard" data-jlpt-card>\n'
        f'<div class="JLPTSurface">\n{body}\n</div>\n</main>'
    )


def prompt(body, cue=""):
    label = f'<p class="JLPTCue">{cue}</p>' if cue else ""
    classes = "Question JLPTQuestion" if cue else "Question"
    return f'<header class="{classes}">{label}{body}</header>'


def typed_input(answer, fallback=None):
    control = field(f"type:{answer}")
    if fallback:
        control = section(answer, control) + section(answer, field(f"type:{fallback}"), unless=True)
    return f'<div class="JLPTInput">{control}</div>'


def replay(sound, filename, *, native=True):
    if native:
        icon = field(sound) + f'<template>{REPLAY_ICON}</template>'
        attributes = ' data-jlpt-native'
        gate = sound
    else:
        icon = REPLAY_ICON
        attributes = ''
        gate = filename
    return section(gate, (
        f'<span class="JLPTReplay" data-jlpt-audio="{field(filename)}"{attributes}>'
        + icon + '<span data-jlpt-audio-error role="status" hidden>此音频无法播放</span></span>'
    ))


def text_prompt(slot):
    readings = f"SentFurigana{slot}"
    return section(readings, field(f"kanji:{readings}")) + section(
        readings, field(f"kanji:SentKanji{slot}"), unless=True
    )


def word_prompt():
    return field("kanji:VocabKanji")


def read_asset(folder, suffix):
    paths = sorted(folder.glob("*-" + suffix))
    if len(paths) != 1:
        raise ValueError(f"Expected one upstream *-{suffix} in {folder}, found {len(paths)}")
    return paths[0].read_text(encoding="utf-8")


def require_match(pattern, source, description):
    match = re.search(pattern, source, re.DOTALL)
    if not match:
        raise ValueError(f"The upstream template changed around {description}")
    return match


class CardTemplates:
    def __init__(self, upstream_source):
        folder = upstream_source / "templates"
        front = read_asset(folder, "日-中-front.html")
        back = read_asset(folder, "日-中-back.html")
        settings = (
            ":root {\n"
            "  --jlpt-language: zh-Hans; /* zh-Hans 简体 | zh-Hant 繁体 */\n"
            "  --jlpt-input-display: block; /* block 显示输入框 | none 隐藏 */\n"
            "  --jlpt-replay-rate: 1; /* 手动播放倍速 */\n"
            "  --jlpt-day-theme: shinsyu; /* shinsyu 真朱 | sumi-paper 墨と紙 */\n"
            "  --jlpt-night-theme: kon; /* 夜间主题代号见下方 */\n"
            "}\n\n"
            "/* 夜间主题\n"
            "   kon 紺 × 珊瑚朱 · tea 黒鳶 × 梔子 · koubai 紅梅 · aoyamabuki 青山吹\n"
            "   ink 墨 × 洗朱 · ink-byakugun 墨 × 白群 · ink-fuji 墨 × 藤 · ink-yamabukicha 墨 × 山吹茶\n"
            "   shikon 紫紺 × 退紅 · tetsuonando 鉄御納戸 × 薄香 · aikobicha 藍媚茶 × 鳥の子\n"
            "   verdigris 緑青 · ukiyoe 浮世絵\n"
            "*/\n\n"
        )
        self.css = "\n".join((
            settings,
            read_asset(folder, "styles.css"),
            (TEMPLATES / "palette.css").read_text(encoding="utf-8"),
            (TEMPLATES / "additions.css").read_text(encoding="utf-8"),
        ))
        # Reuse upstream platform dictionary mappings. Per-card display and playback
        # use their own controls, so upstream update notices and settings are omitted.
        config = require_match(r"(    const dictOptions = \{.*?\n    \})", front, "dictionary mappings")
        self.script = "<script>\n(() => {\n" + config[1] + "\n" + (TEMPLATES / "card.js").read_text() + "\n})();\n</script>"
        cloze = (TEMPLATES / "vendor" / "cloze-anything.js").read_text()
        self.cloze_script = "<script>\n(() => {\n" + cloze + "\n})();\n</script>"

        self.answer_section = require_match(
            r'(<section class="Answer">.*?</section>)', back, "word answer"
        )[1]
        self.answer_section = (
            self.answer_section.replace(field("VocabDefSC"), bilingual("VocabDefSC", "VocabDefTC"))
            .replace('<h3 class="VocabPoS LabelIndent2">', '<h3 class="VocabPoS LabelIndent2"><em lang="ja">［{{VocabPoS}}］</em>')
            .replace(field("furigana:VocabPlus"), '<em lang="ja">［補］</em>' + field("furigana:VocabPlus"))
        )
        self.reverse_front = self.reverse_question(read_asset(folder, "中-日-front.html"))
        self.sentences = {}
        for slot in SLOTS:
            sentence = require_match(
                rf'(\{{\{{#SentKanji{slot}\}}\}}.*?\{{\{{/SentKanji{slot}\}}\}})',
                back,
                f"example {slot}",
            )[1]
            self.sentences[slot] = sentence.replace(
                '<li class="Sentence">', f'<li class="Sentence" data-jlpt-slot="{slot}" data-jlpt-type="{field(f"SentType{slot}")}">'
            )

    def reverse_question(self, source):
        layout = require_match(r'<main id="FrontSide"[^>]*>(.*?)</main>', source, "Chinese–Japanese question")[1]
        answer = self.answer_section.replace('class="VocabFurigana"', 'class="VocabFurigana blur"')
        answer = answer.replace('class="VocabPlus LabelIndent"', 'class="VocabPlus LabelIndent blur"')
        original_answer = require_match(r'(<section class="Answer">.*?</section>)', layout, "Chinese–Japanese cues")[1]
        layout = layout.replace(original_answer, answer)
        layout = re.sub(r'\n +<a[^\n]*class="(?:Feedback|Search)">[^\n]*</a>', "", layout)
        layout = layout.replace('class="VocabKanji"', 'class="VocabKanji blur"')
        layout = layout.replace('class="SentKanji LabelIndent"', 'class="SentKanji LabelIndent blur"')
        return wrap_side("Front", layout.strip())

    def example(self, slot, *, focus=False, meaning=False):
        sound = f"SentAudio{slot}"
        sc = f"SentDefSC{slot}"
        translation = section(sc, '<em lang="ja">［訳］</em>' + bilingual(sc, f"SentDefTC{slot}"))
        if meaning:
            target = f"TargetDefSC{slot}"
            gloss = section(target, '<em lang="ja">［義］</em>' + bilingual(target, f"TargetDefTC{slot}"))
            translation += section(sc, gloss, unless=True)
        return (
            self.sentences[slot].replace(field(sc), translation)
            .replace(field(sound), replay(sound, f"SentAudioFile{slot}", native=focus))
        )

    def answer(self, *, slot=None, listening=False, meaning=False, lead=""):
        other_examples = "\n".join(self.example(n) for n in SLOTS if n != slot)
        word_audio_markup = replay("VocabAudio", "WordAudioFile", native=not listening)
        word = (
            '<header class="Question">'
            '<a class="Search" aria-label="查词" href="#">🔍</a>'
            f'<h1 class="VocabKanji"><span lang="ja">{field("furigana:VocabKanji")}</span></h1>'
            f'<div class="VocabAudio">{word_audio_markup}</div></header>'
        )
        note = section("RemarkSC", f'<h3 class="VocabPlus LabelIndent"><em lang="ja">［注］</em>{bilingual("RemarkSC", "RemarkTC")}</h3>')
        definitions = self.answer_section.replace("</section>", note + "</section>")
        focus = ""
        if slot:
            sentence = self.example(slot, focus=listening, meaning=meaning)
            focus = f'<ul class="SentenceList" data-jlpt-focused>{sentence}</ul>'
        return wrap_side(
            "Back",
            lead + focus + word + definitions
            + f'<ul class="SentenceList JLPTExamples">{other_examples}</ul>',
        )

    def make(self, name, gate, front, back, *, required=None, cloze=False):
        scripts = (self.cloze_script if cloze else "") + self.script
        question, answer = front + scripts, back + scripts
        if required:
            question = section(required, question)
            answer = section(required, answer)
        return {
            "name": name,
            "qfmt": section(gate, question),
            "afmt": section(gate, answer),
        }

    def all(self):
        results = []
        for slot in SLOTS:
            sound = f"SentAudio{slot}"
            audio = replay(sound, f"SentAudioFile{slot}")
            hint = f"ListeningHintSC{slot}"
            results.append(self.make(
                f"例句 {slot} 音→意", f"Listening{slot}",
                wrap_side("Front", prompt(audio) + section(hint, f'<p class="JLPTHint">{bilingual(hint, f"ListeningHintTC{slot}")}</p>')),
                self.answer(slot=slot, listening=True), required=sound,
            ))
        results.append(self.make(
            "单词 音→意", "WordListening",
            wrap_side("Front", prompt(replay("VocabAudio", "WordAudioFile"), "听音回想词义")
                      + section("WordHintSC", f'<p class="JLPTHint">{bilingual("WordHintSC", "WordHintTC")}</p>')),
            self.answer(), required="VocabAudio",
        ))

        typed = typed_input("ReadingAnswer")
        reading_example = prompt('<div class="JLPTPrompt" lang="ja">{{furigana:ReadingPrompt}}</div>')
        results.append(self.make(
            "形→音", "ReadingPrompt",
            wrap_side("Front", prompt('<div class="JLPTPrompt" lang="ja">{{kanji:ReadingPrompt}}</div>', "读出目标词（辞书形）") + typed),
            self.answer(lead=reading_example + typed), required="ReadingAnswer",
        ))

        for slot in SLOTS:
            typed = typed_input(f"TargetDefSC{slot}", "VocabDefSC")
            results.append(self.make(
                f"例句 {slot} 形→意", f"Meaning{slot}",
                wrap_side("Front", prompt(f'<div class="JLPTPrompt" lang="ja">{text_prompt(slot)}</div>', "回想高亮词在句中的意思") + typed),
                self.answer(slot=slot, meaning=True, lead=typed),
            ))
        typed = typed_input("VocabDefSC")
        results.append(self.make(
            "单词 形→意", "WordMeaning",
            wrap_side("Front", prompt(f'<div class="JLPTPrompt" lang="ja">{word_prompt()}</div>', "回想词义") + typed),
            self.answer(lead=typed),
        ))

        for group in (1, 2):
            for number, ordinal in enumerate(CLOZE_ORDINALS, start=1):
                gate = f"Cloze{group}{ordinal}"
                expression = (
                    '<div class="JLPTPrompt" id="cloze" lang="ja" '
                    f'data-card="Cloze {number}">{field(f"kanji:ClozeText{group}")}</div>'
                )
                hint = section(f"ClozeHint{group}", f'<p class="JLPTHint">{field(f"ClozeHint{group}")}</p>')
                answer = f"{gate}Answer"
                typed = section(answer, typed_input(answer))
                results.append(self.make(
                    f"完形 {group} · c{number}", gate,
                    wrap_side("Front", prompt(expression) + hint + typed),
                    self.answer(lead='<span id="back" hidden></span>' + prompt(expression) + typed),
                    required=f"ClozeText{group}", cloze=True,
                ))

        results.append(self.make("中→日", "Alt2", self.reverse_front, self.answer()))
        return results
