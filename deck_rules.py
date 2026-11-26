"""Select cards using upstream examples and verified editorial exceptions."""

from collections import defaultdict
from functools import cache
from hashlib import sha256
from html import escape, unescape
import re
import unicodedata

from cloze_hints import load_cloze_reviews, reviewed_hints


SLOTS = range(1, 5)
KANJI = r"[\u3400-\u4dbf\u4e00-\u9fff々〆〇]"
HIRAGANA = r"[ぁ-ゖゝゞ]"
READING = re.compile(r"[ぁ-ゖゝゞァ-ヺヽヾー]+(?:[・／/、][ぁ-ゖゝゞァ-ヺヽヾー]+)*")
SOUND = re.compile(r"\[sound:([^\]]+)\]")
HIGHLIGHT = re.compile(r"<(?:b|strong)\b[^>]*>(.*?)</(?:b|strong)>", re.I | re.S)
POS_LABEL = re.compile(r"［(自動|他動|自他動)］")
RUBY = re.compile(r"\[[^\[\]]+\]")
KANJI_RUBY = re.compile(rf"({KANJI}+)\[({HIRAGANA}+)\]")
KANA_AROUND_KANJI = re.compile(rf"({HIRAGANA}*)({KANJI}+)({HIRAGANA}*)")
PARENS = dict(zip("（(〔[【「『〈《", "）)〕]】」』〉》", strict=True))


def plain(markup):
    markup = re.sub(r"<rt\b[^>]*>.*?</rt>", "", markup, flags=re.I | re.S)
    return unescape(re.sub(r"<[^>]*>", "", markup)).strip()


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def japanese(markup):
    return compact(RUBY.sub("", plain(markup)))


def reading(markup):
    return japanese(markup).strip("〜～")


def written_kanji(text):
    return "".join(re.findall(KANJI, text))


def reading_eligible(answer, tags):
    return READING.fullmatch(answer) and not any(tag.split("::")[-1] in {"外", "カタカナ語"} for tag in tags)


def reading_path(word, answer, known):
    """Match the full reading with visible kana and proven readings of written parts."""
    @cache
    def match(start, offset):
        if start == len(word):
            return () if offset == len(answer) else None
        char = word[start]
        if re.fullmatch(HIRAGANA, char) and answer.startswith(char, offset):
            if (tail := match(start + 1, offset + 1)) is not None:
                return tail
        for end in range(len(word), start, -1):
            part = word[start:end]
            for sound, source in known.get(part, {}).items():
                if answer.startswith(sound, offset):
                    tail = match(end, offset + len(sound))
                    if tail is not None:
                        return ((part, sound, source), *tail)
        return None

    return match(0, 0)


def sound_names(markup):
    return tuple(SOUND.findall(markup))


def example(note, slot):
    return note[f"SentFurigana{slot}"] or note[f"SentKanji{slot}"]


def ordinary_slots(note):
    return [slot for slot in SLOTS if not note[f"SentType{slot}"].strip() and japanese(example(note, slot))]


def reading_examples(note, slots):
    for slot in slots:
        prompt = example(note, slot)
        targets = [japanese(mark) for mark in HIGHLIGHT.findall(prompt)]
        if any(map(written_kanji, targets)):
            yield slot, prompt, targets


def senses(markup):
    parts = [[]]
    closing = []
    for char in plain(markup):
        if char in PARENS:
            closing.append(PARENS[char])
        elif closing and char == closing[-1]:
            closing.pop()
        elif char == "；" and not closing:
            parts.append([])
            continue
        parts[-1].append(char)
    return ["".join(part).strip() for part in parts if "".join(part).strip()]


def label(part_of_speech):
    match = re.search(r"自他動|自動|他動", plain(part_of_speech))
    return match[0] if match else ""


class CardRules:
    def __init__(self, rows, editor, media_dir):
        self.editor = editor
        self.cloze_reviews = load_cloze_reviews()
        self.media_dir = media_dir
        self.digests = {}
        self.audit = []
        self.words = defaultdict(list)
        self.pronunciations = defaultdict(set)
        for _, note, _ in rows:
            head = japanese(note["VocabKanji"])
            self.words[head].append(note)
            if READING.fullmatch(reading(note["VocabFurigana"])):
                self.pronunciations[reading(note["VocabFurigana"])].add(head)

    def record(self, note, issue, **details):
        self.audit.append({"id": note["NoteID"], "word": japanese(note["VocabKanji"]), "issue": issue, **details})

    def audio_key(self, markup):
        names = sound_names(markup)
        for name in names:
            if name not in self.digests:
                path = self.media_dir / name
                self.digests[name] = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        hashes = tuple(self.digests[name] for name in names)
        return hashes if hashes and all(hashes) else ()

    def listening(self, note, result, ordinary, edits):
        slots = []
        for slot in ordinary:
            if not self.audio_key(note[f"SentAudio{slot}"]):
                self.record(note, "listening-missing-audio", slot=slot)
                continue
            slots.append(slot)
            missing = [lang for lang in ("SC", "TC") if not plain(note[f"SentDef{lang}{slot}"])]
            if missing:
                self.record(note, "listening-missing-translation", slot=slot, languages=missing)
        glosses = senses(note["VocabDefSC"])
        if "listen_gloss" in edits and glosses != edits["listen_gloss"]:
            raise ValueError(f"Listening glosses changed for {note['NoteID']}")
        dropped = set(edits.get("listen_drop", ()))
        if not dropped <= set(slots) or (slots and slots[0] in dropped):
            raise ValueError(f"Invalid listening override for {note['NoteID']}")
        for slot, expected in edits.get("listen_examples", {}).items():
            if japanese(example(note, int(slot))) != expected:
                raise ValueError(f"Listening example changed for {note['NoteID']}: {slot}")
        for slot, expected in edits.get("listen_translations", {}).items():
            if plain(note[f"SentDefSC{slot}"]) != expected:
                raise ValueError(f"Listening translation changed for {note['NoteID']}: {slot}")
        if not slots:
            if not self.audio_key(note["VocabAudio"]):
                raise ValueError(f"No playable audio for {note['NoteID']}")
            result["WordListening"] = "1"
            others = self.pronunciations[reading(note["VocabFurigana"])] - {japanese(note["VocabKanji"])}
            self.record(note, "word-audio", homophones=sorted(others), hinted=bool(edits.get("word_hint")))
            if hint := edits.get("word_hint"):
                result.update(WordHintSC=escape(hint[0]), WordHintTC=escape(hint[1]))
        else:
            seen_audio, seen_prompts = set(), set()
            for slot in slots:
                if slot in dropped:
                    continue
                digest = self.audio_key(note[f"SentAudio{slot}"])
                prompt = japanese(example(note, slot)), compact(plain(note[f"SentDefSC{slot}"])).rstrip("。！？!?．.")
                if digest in seen_audio or prompt in seen_prompts:
                    self.record(note, "duplicate-example", slot=slot)
                    continue
                seen_audio.add(digest)
                seen_prompts.add(prompt)
                result[f"Listening{slot}"] = "1"
            if len(glosses) > len(seen_audio) and len(glosses) > 1:
                self.record(note, "less-audio-than-glosses", glosses=len(glosses), examples=len(seen_audio))
        if len(glosses) != len(senses(note["VocabDefTC"])):
            self.record(note, "translation-splits-differ")

    def reading_card(self, note, tags, result, ordinary, edits):
        head = reading(note["VocabKanji"])
        answer = reading(note["VocabFurigana"])
        if not reading_eligible(answer, tags):
            if "read_example" in edits:
                raise ValueError(f"Approved reading card no longer eligible for {note['NoteID']}")
            return
        included = bool(re.fullmatch(rf"{KANJI}+|{KANJI}{HIRAGANA}{{1,2}}", head))
        if not included and "[" in note["VocabKanji"] and re.match(KANJI, head):
            included = True
        if not included and "々" in head and re.fullmatch(rf"(?:{KANJI}|{HIRAGANA})+", head):
            included = True
        if not edits.get("read", included):
            return
        alternatives = {reading(item["VocabFurigana"]) for item in self.words[japanese(note["VocabKanji"])]}
        candidates = list(reading_examples(note, ordinary))
        if candidates:
            kanji = written_kanji(head)
            exact = next((entry for entry in candidates
                          if kanji and any(written_kanji(target) == kanji for target in entry[2])), None)
            slot, prompt, targets = candidates[0] if "read_example" in edits else (exact or candidates[0])
            if "read_example" in edits and [japanese(prompt), answer, targets] != edits["read_example"]:
                raise ValueError(f"Reading example or answer changed for {note['NoteID']}")
            if exact is None and "read_example" not in edits:
                self.record(note, "reading-alternate-spelling", slot=slot, targets=targets, answer=answer)
            other_readings = sorted(value for value in alternatives - {answer} if READING.fullmatch(value))
            if other_readings:
                self.record(note, "reading-homograph-in-example", slot=slot, answer=answer, alternatives=other_readings)
            result.update(ReadingPrompt=prompt, ReadingAnswer=escape(answer))
            if slot != ordinary[0]:
                self.record(note, "reading-later-example", slot=slot)
            return
        if "read_example" in edits:
            raise ValueError(f"Reading example missing for {note['NoteID']}")
        if ordinary:
            self.record(note, "reading-kana-in-all-examples")
        elif len(alternatives) == 1:
            result.update(ReadingPrompt=f'<b>{note["VocabKanji"]}</b>', ReadingAnswer=escape(answer))
        else:
            self.record(note, "reading-homograph-without-example")

    def record_reading_candidates(self, selected):
        known = defaultdict(dict)
        for _, note, _ in selected:
            if not (prompt := note.get("ReadingPrompt")):
                continue
            head = reading(note["VocabKanji"])
            answer = reading(note["VocabFurigana"])
            kanji = written_kanji(head)
            marks = [mark for mark in HIGHLIGHT.findall(prompt) if written_kanji(japanese(mark)) == kanji]
            if not kanji or not marks or not re.fullmatch(rf"{HIRAGANA}+", answer):
                continue
            known[head].setdefault(answer, head)
            if match := KANA_AROUND_KANJI.fullmatch(head):
                before, chars, after = match.groups()
                if answer.startswith(before) and answer.endswith(after) and len(answer) > len(before) + len(after):
                    known[chars].setdefault(answer[len(before):len(answer) - len(after)], head)
            for mark in marks:
                for chars, sound in KANJI_RUBY.findall(plain(mark)):
                    if chars in head:
                        known[chars].setdefault(sound, head)

        for _, note, tags in selected:
            head = reading(note["VocabKanji"])
            answer = reading(note["VocabFurigana"])
            if note.get("ReadingPrompt") or "read" in self.editor.get(note["NoteID"], {}):
                continue
            if not reading_eligible(answer, tags):
                continue
            kanji = written_kanji(head)
            targets = {slot: marks for slot, _, marks in reading_examples(note, ordinary_slots(note))}
            if not targets:
                continue
            matching = kanji and any(written_kanji(mark) == kanji for marks in targets.values() for mark in marks)
            evidence = reading_path(head, answer, known) if matching else None
            if evidence:
                self.record(note, "reading-covered-by-existing", answer=answer, parts=evidence)
            else:
                self.record(note, "reading-needs-review", answer=answer, targets=targets)

    def meaning(self, note, result, ordinary, edits):
        if edits.get("word_meaning"):
            if ordinary:
                raise ValueError(f"A sentence is available for word-meaning override {note['NoteID']}")
            result["WordMeaning"] = "1"
            return
        if "meaning" in edits:
            for item in edits["meaning"]:
                slot = item["slot"]
                if slot not in ordinary or not HIGHLIGHT.search(example(note, slot)):
                    raise ValueError(f"Invalid meaning example for {note['NoteID']}: {slot}")
                if item.get("example") and japanese(example(note, slot)) != item["example"]:
                    raise ValueError(f"Meaning example changed for {note['NoteID']}: {slot}")
                result[f"Meaning{slot}"] = "1"
                result[f"TargetDefSC{slot}"] = escape(item["sc"])
                result[f"TargetDefTC{slot}"] = escape(item["tc"])
            return

    def paired_verbs(self, note):
        expected = {"自動": {"他動", "自他動"}, "他動": {"自動", "自他動"}, "自他動": {"自動", "他動"}}
        role = label(note["VocabPoS"])
        opposites = expected.get(role, set())
        single_role = re.fullmatch(r"(?:自動|他動)[12]?", plain(note["VocabPoS"]))
        head = japanese(note["VocabKanji"])
        kanji = written_kanji(head)
        pairs = set()
        linked_only = set()
        annotated = False
        for slot in (2, 3, 4):
            related_roles = POS_LABEL.findall(plain(note[f"SentDefSC{slot}"]))
            if not related_roles:
                continue
            annotated = True
            if not note[f"SentType{slot}"] or related_roles[0] not in opposites:
                continue
            related = japanese(example(note, slot))
            for word in re.split(r"[・、／]", related):
                if any(label(other["VocabPoS"]) in opposites and other is not note for other in self.words.get(word, ())):
                    pairs.add(word)
            if (note[f"SentType{slot}"] == "関" and single_role and related_roles in (["自動"], ["他動"])
                    and related != head and kanji and written_kanji(related) == kanji
                    and re.fullmatch(rf"(?:{KANJI}|{HIRAGANA})+", related) and related not in self.words):
                linked_only.add(related)
        if not pairs and len(linked_only) == 1:
            pairs = linked_only
        return annotated, sorted(pairs)

    def apply_cloze_review(self, note, result, group, prompt):
        review = self.cloze_reviews.get((note["NoteID"], group))
        if not review or review["prompt"] != prompt:
            self.record(note, "cloze-review-needed", group=group, prompt=japanese(prompt))
            return
        if not review["hint1"] and not review["hint2"]:
            self.record(note, "cloze-review-excluded", group=group, reason=review["reason"])
            return
        result[f"ClozeHint{group}"] = reviewed_hints(prompt, review, japanese)
        result[f"ClozeText{group}"] = prompt
        for number, ordinal in enumerate(("First", "Second"), start=1):
            if review[f"hint{number}"]:
                result[f"Cloze{group}{ordinal}"] = "1"

    def cloze(self, note, result, ordinary, edits):
        if "cloze" in edits:
            for group, item in enumerate(edits["cloze"], start=1):
                self.apply_cloze_review(note, result, group, item["text"])
            return
        annotated, pairs = self.paired_verbs(note)
        if authored_pairs := edits.get("pairs"):
            if not all(any(pair in japanese(example(note, slot)) for slot in (2, 3, 4)) for pair in authored_pairs):
                raise ValueError(f"Cloze pair changed for {note['NoteID']}")
            pairs = authored_pairs
        if not annotated:
            return
        if not pairs or not ordinary:
            self.record(note, "pair-not-confirmed", candidates=pairs)
            return
        review = self.cloze_reviews.get((note["NoteID"], 1))
        slot = int(review["slot"]) if review and review["slot"] else ordinary[0]
        if slot not in ordinary:
            self.record(note, "cloze-review-needed", group=1, slot=slot)
            return
        text = example(note, slot)
        if len(list(HIGHLIGHT.finditer(text))) != 1 or not plain(note[f"SentDefSC{slot}"]):
            self.record(note, "cloze-needs-unique-target", slot=slot, pairs=pairs)
            return
        mark = HIGHLIGHT.search(text)
        head = japanese(note["VocabKanji"])
        target = japanese(mark[1])
        if not target or (not target.startswith(head[:1]) and target != edits.get("cloze_surface")) or "<" in mark[1]:
            self.record(note, "cloze-unverified-target", slot=slot, target=target, pairs=pairs)
            return
        prompt = text[:mark.start()] + f"((c1::{mark[1].strip()}))" + text[mark.end():]
        self.apply_cloze_review(note, result, 1, prompt)
        if not authored_pairs and len(pairs) == 1 and pairs[0] not in self.words:
            self.record(note, "pair-from-related-entry", pair=pairs[0], slot=slot, example=japanese(text), target=target)

    def select(self, rows):
        output = []
        sightings = defaultdict(list)
        applied = set()
        for deck, note, tags in rows:
            note_id = note["NoteID"]
            edits = self.editor.get(note_id, {})
            if edits:
                if edits.get("head", japanese(note["VocabKanji"])) != japanese(note["VocabKanji"]):
                    raise ValueError(f"Editorial headword changed for {note_id}")
                applied.add(note_id)
            ordinary = ordinary_slots(note)
            result = dict(note, Alt1="", Alt2="")
            self.listening(note, result, ordinary, edits)
            self.reading_card(note, tags, result, ordinary, edits)
            self.meaning(note, result, ordinary, edits)
            self.cloze(note, result, ordinary, edits)
            for slot in ordinary:
                if result.get(f"Listening{slot}"):
                    sightings[self.audio_key(note[f"SentAudio{slot}"])].append((result, slot))
            output.append((deck, result, tags))
        if unapplied := self.editor.keys() - applied:
            raise ValueError(f"Editorial notes missing upstream: {sorted(unapplied)}")
        self.record_reading_candidates(output)
        self.record_shared_audio(sightings)
        return output

    def record_shared_audio(self, sightings):
        for used in sightings.values():
            if len(used) < 2:
                continue
            peers = [japanese(note["VocabKanji"]) for note, _ in used]
            for note, slot in used:
                self.record(note, "shared-listening-audio", slot=slot, peers=peers)
