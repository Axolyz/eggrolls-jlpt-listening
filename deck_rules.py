"""Select cards using upstream examples and verified editorial exceptions."""

from collections import defaultdict
from hashlib import sha256
from html import escape, unescape
import re
import unicodedata

from cloze_hints import author_hint, cloze_parts, pack_hints, pair_hint


SLOTS = range(1, 5)
KANJI = r"[\u3400-\u4dbf\u4e00-\u9fff々〆〇]"
HIRAGANA = r"[ぁ-ゖゝゞ]"
READING = re.compile(r"[ぁ-ゖゝゞァ-ヺヽヾー]+(?:[・／/、][ぁ-ゖゝゞァ-ヺヽヾー]+)*")
SOUND = re.compile(r"\[sound:([^\]]+)\]")
HIGHLIGHT = re.compile(r"<(?:b|strong)\b[^>]*>(.*?)</(?:b|strong)>", re.I | re.S)
POS_LABEL = re.compile(r"［(自動|他動|自他動)］")
RUBY = re.compile(r"\[[^\[\]]+\]")
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


def sound_names(markup):
    return tuple(SOUND.findall(markup))


def example(note, slot):
    return note[f"SentFurigana{slot}"] or note[f"SentKanji{slot}"]


def ordinary_slots(note):
    return [slot for slot in SLOTS if not note[f"SentType{slot}"].strip() and japanese(example(note, slot))]


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
        slots = [slot for slot in ordinary if self.audio_key(note[f"SentAudio{slot}"])]
        glosses = senses(note["VocabDefSC"])
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
            added = set(edits.get("listen_add", ()))
            dropped = set(edits.get("listen_drop", ()))
            if not (added | dropped) <= set(slots) or slots[0] in dropped:
                raise ValueError(f"Invalid listening override for {note['NoteID']}")
            for slot, expected in edits.get("listen_examples", {}).items():
                if japanese(example(note, int(slot))) != expected:
                    raise ValueError(f"Listening example changed for {note['NoteID']}: {slot}")
            for slot in slots:
                if slot != slots[0] and (slot in dropped or (len(glosses) < 2 and slot not in added)):
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
        head = japanese(note["VocabKanji"]).strip("〜～")
        answer = reading(note["VocabFurigana"])
        if not READING.fullmatch(answer) or any(tag.split("::")[-1] in {"外", "カタカナ語"} for tag in tags):
            return
        included = bool(re.fullmatch(rf"{KANJI}+|{KANJI}{HIRAGANA}{{1,2}}", head))
        if not included and "[" in note["VocabKanji"] and re.match(KANJI, head):
            included = True
        if not included and "々" in head and re.fullmatch(rf"(?:{KANJI}|{HIRAGANA})+", head):
            included = True
        if not edits.get("read", included):
            return
        for slot in ordinary:
            prompt = example(note, slot)
            if any(re.search(KANJI, japanese(mark)) for mark in HIGHLIGHT.findall(prompt)):
                result.update(ReadingPrompt=prompt, ReadingAnswer=escape(answer))
                if slot != ordinary[0]:
                    self.record(note, "reading-later-example", slot=slot)
                return
        if ordinary:
            self.record(note, "reading-kana-in-all-examples")
        elif len({reading(item["VocabFurigana"]) for item in self.words[japanese(note["VocabKanji"])]}) == 1:
            result.update(ReadingPrompt=f'<b>{note["VocabKanji"]}</b>', ReadingAnswer=escape(answer))
        else:
            self.record(note, "reading-homograph-without-example")

    def meaning(self, note, tags, result, ordinary, edits):
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
        head = japanese(note["VocabKanji"])
        if not (re.match(HIRAGANA, head) and re.search(KANJI, head)):
            return
        if any(tag.split("::")[-1] in {"外", "カタカナ語"} for tag in tags):
            return
        if not ordinary:
            if len(senses(note["VocabDefSC"])) == 1:
                result["WordMeaning"] = "1"
            return
        slot = ordinary[0]
        if not HIGHLIGHT.search(example(note, slot)):
            self.record(note, "meaning-missing-target", slot=slot)
            return
        if len(senses(note["VocabDefSC"])) > 1:
            if compact(plain(note[f"SentDefSC{slot}"])) in {compact(gloss) for gloss in senses(note["VocabDefSC"])}:
                result[f"TargetDefSC{slot}"] = note[f"SentDefSC{slot}"]
                result[f"TargetDefTC{slot}"] = note[f"SentDefTC{slot}"]
            else:
                self.record(note, "mixed-writing-needs-target-gloss", slot=slot)
                return
        result[f"Meaning{slot}"] = "1"

    def paired_verbs(self, note):
        expected = {"自動": {"他動", "自他動"}, "他動": {"自動", "自他動"}, "自他動": {"自動", "他動"}}
        opposites = expected.get(label(note["VocabPoS"]), set())
        pairs = set()
        annotated = False
        for slot in (2, 3, 4):
            part = POS_LABEL.search(plain(note[f"SentDefSC{slot}"]))
            if not part:
                continue
            annotated = True
            if not note[f"SentType{slot}"] or part[1] not in opposites:
                continue
            associated = re.split(r"[・、／]", japanese(example(note, slot)))
            for head in associated:
                if any(label(other["VocabPoS"]) in opposites and other is not note for other in self.words[head]):
                    pairs.add(head)
        return annotated, sorted(pairs)

    def cloze(self, note, result, ordinary, edits):
        if "cloze" in edits:
            for group, item in enumerate(edits["cloze"], start=1):
                result[f"ClozeText{group}"] = item["text"]
                groups = cloze_parts(item["text"], japanese)
                result[f"ClozeHint{group}"] = pack_hints(
                    groups, item["hint"],
                    lambda target, raw, after: author_hint(item["text"], target),
                )
                for number, ordinal in enumerate(("First", "Second"), start=1):
                    if f"((c{number}::" in item["text"]:
                        result[f"Cloze{group}{ordinal}"] = "1"
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
        slot = ordinary[0]
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
        result["ClozeText1"] = text[:mark.start()] + f"((c1::{mark[1].strip()}))" + text[mark.end():]
        result["ClozeHint1"] = pair_hint(
            cloze_parts(result["ClozeText1"], japanese), plain(note[f"SentDefSC{slot}"]),
            head=head, role=label(note["VocabPoS"]), alternatives=pairs, reading=reading(note["VocabFurigana"]),
        )
        result["Cloze1First"] = "1"

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
            self.meaning(note, tags, result, ordinary, edits)
            self.cloze(note, result, ordinary, edits)
            for slot in ordinary:
                if result.get(f"Listening{slot}"):
                    sightings[self.audio_key(note[f"SentAudio{slot}"])].append((result, slot))
            if edits.get("remark"):
                result["RemarkSC"], result["RemarkTC"] = map(escape, edits["remark"])
            output.append((deck, result, tags))
        if unapplied := self.editor.keys() - applied:
            raise ValueError(f"Editorial notes missing upstream: {sorted(unapplied)}")
        self.record_shared_audio(sightings)
        return output

    def record_shared_audio(self, sightings):
        for used in sightings.values():
            if len(used) < 2:
                continue
            peers = [japanese(note["VocabKanji"]) for note, _ in used]
            for note, slot in used:
                self.record(note, "shared-listening-audio", slot=slot, peers=peers)
