# /// script
# requires-python = ">=3.12"
# dependencies = ["anki==26.9.4"]
# ///
"""Build an importable card-template preview from an upstream checkout."""

from argparse import ArgumentParser
from collections import Counter, defaultdict
import csv
from hashlib import sha256
from html import escape, unescape
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from anki.collection import Collection, DeckIdLimit
from anki.import_export_pb2 import ExportAnkiPackageOptions

from card_fonts import FONT_ASSETS, FONT_LICENSE, font_asset
from card_templates import CardTemplates, CLOZE_ORDINALS, EXTRA_FIELDS, SLOTS, TEMPLATES
from deck_rules import SOUND, japanese
from cloze_hints import CLOZE, load_cloze_reviews, reviewed_hints


UPSTREAM_FIELDS = (
    "NoteID VocabKanji VocabPitch VocabPoS VocabFurigana VocabDefSC VocabDefTC "
    "VocabPlus VocabAudio"
).split() + [
    f"Sent{field}{slot}"
    for slot in SLOTS
    for field in ("Type", "Kanji", "Furigana", "DefSC", "DefTC", "Audio")
] + ["Order", "Alt1", "Alt2"]

PREVIEW_NAME = "egg rolls JLPT · 例句听力预览"

# Explicit preview examples exercise the template inputs independently of the
# full-deck generation rules.
SAMPLES = {
    "適当": {
        "Listening1": "1", "Listening3": "1", "Meaning3": "1", "ReadExample": 1,
        "TargetDefSC3": "随便，敷衍", "TargetDefTC3": "隨便，敷衍",
    },
    "要る": {"Listening1": "1", "ReadExample": 2},
    "極[ごく]": {
        "Listening1": "1", "Meaning1": "1",
        "TargetDefSC1": "极其，非常", "TargetDefTC1": "極其，非常",
    },
    "電話": {
        "Listening1": "1", "Listening2": "1",
        "Cloze1First": "1", "Cloze1Second": "1",
        "Cloze2First": "1", "Cloze2Second": "1",
    },
    "校長": {"WordListening": "1", "WordMeaning": "1", "WordHintSC": "学校职务", "WordHintTC": "學校職務"},
    "図書館": {"Listening1": "1", "ReadExample": 1, "Alt2": "1"},
    "落ちる": {
        "Listening1": "1", "Cloze1First": "1",
    },
    "きちんと": {"Listening1": "1", "Listening3": "1", "Listening4": "1"},
    "サービス": {
        "Listening1": "1", "Meaning2": "1",
        "TargetDefSC2": "免费赠送，不收费", "TargetDefTC2": "免費贈送，不收費",
    },
    "夏休み": {"Listening1": "1"},
    "掛ける": {
        "Listening1": "1", "Meaning4": "1",
        "TargetDefSC4": "坐，坐下", "TargetDefTC4": "坐，坐下",
    },
}
CSS_MEDIA = re.compile(r"url\('([^']+)'\)")


def set_cloze_answers(values):
    for group in (1, 2):
        answers = defaultdict(list)
        for number, content in CLOZE.findall(values.get(f"ClozeText{group}", "")):
            parts = content.split("::")
            expression = parts[0] if len(parts) == 2 else content
            expression = re.sub(r"<rt\b[^>]*>.*?</rt>", "", expression, flags=re.DOTALL | re.IGNORECASE)
            expression = re.sub(r"\[[^\[\]]+\]", "", expression)
            answer = unescape(re.sub(r"<[^>]+>", "", expression)).strip()
            if answer:
                answers[int(number)].append(answer)
        for number, ordinal in enumerate(CLOZE_ORDINALS, start=1):
            gate = f"Cloze{group}{ordinal}"
            if not values.get(gate):
                continue
            if not answers[number]:
                raise ValueError(f"{values['VocabKanji']}: {gate} has no matching answer")
            values[f"{gate}Answer"] = escape("／".join(answers[number]), quote=False)


def set_preview_hints(values):
    if not values.get("Cloze1First"):
        return
    reviews = load_cloze_reviews()
    for group in (1, 2):
        if not values.get(f"Cloze{group}First"):
            continue
        review = reviews[(values["NoteID"], group)]
        values[f"ClozeText{group}"] = review["prompt"]
        values[f"ClozeHint{group}"] = reviewed_hints(review["prompt"], review, japanese)


def source_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if stream.readline().strip() != "#separator:Tab":
            raise ValueError("Expected the upstream tab-separated Anki export")
        for row in csv.reader((line for line in stream if not line.startswith("#")), delimiter="\t"):
            if len(row) != len(UPSTREAM_FIELDS) + 3:
                raise ValueError("Unexpected upstream note column count")
            values = dict(zip(UPSTREAM_FIELDS, row[2:-1], strict=True))
            yield row[1], values, row[-1].split()


def sample_rows(path):
    selected = {}
    for _, values, tags in source_rows(path):
        key = values["VocabKanji"]
        if key in SAMPLES and key not in selected:
            selected[key] = (values, tags)
    missing = SAMPLES.keys() - selected.keys()
    if missing:
        raise ValueError(f"Preview notes missing upstream: {', '.join(sorted(missing))}")
    return selected


def install_media(collection, source, layout, notes):
    media = set(CSS_MEDIA.findall(layout.css))
    for values in notes:
        for name in ("VocabAudio", *(f"SentAudio{i}" for i in SLOTS)):
            media.update(SOUND.findall(values[name]))
    filenames = {}
    for name in sorted(media):
        path = font_asset(name) if name in FONT_ASSETS else source / "medias" / name
        filenames[name] = collection.media.write_data(name, path.read_bytes())
    return filenames


def install_model(collection, layout, name, filenames):
    model = collection.models.new(name)
    for field_name in UPSTREAM_FIELDS + EXTRA_FIELDS:
        collection.models.add_field(model, collection.models.new_field(field_name))
    for definition in layout.all():
        template = collection.models.new_template(definition["name"])
        template.update(definition)
        collection.models.add_template(model, template)
    model["css"] = CSS_MEDIA.sub(lambda match: f"url('{filenames[match[1]]}')", layout.css)
    model["sortf"] = UPSTREAM_FIELDS.index("VocabKanji")
    return collection.models.get(collection.models.add_dict(model).id)


def make_note(collection, model, values, tags, filenames, namespace):
    values = values.copy()
    set_cloze_answers(values)
    entry = collection.new_note(model)
    entry.guid = sha256(f"{namespace}:{values['NoteID']}".encode()).hexdigest()[:16]
    entry.tags = tags
    if slot := values.pop("ReadExample", None):
        values["ReadingPrompt"] = values[f"SentFurigana{slot}"] or values[f"SentKanji{slot}"]
        values["ReadingAnswer"] = values["VocabFurigana"]
    for original, field in [
        ("VocabAudio", "WordAudioFile"),
        *((f"SentAudio{i}", f"SentAudioFile{i}") for i in SLOTS),
    ]:
        files = SOUND.findall(values[original])
        if files:
            values[original] = SOUND.sub(lambda match: f"[sound:{filenames[match[1]]}]", values[original])
            values[field] = escape(filenames[files[0]], quote=True)
    for field_name, value in values.items():
        entry[field_name] = value
    return entry


def export_deck(collection, upstream, deck_id, output, *, with_deck_configs=False):
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".jlpt-export-", dir=output.parent) as stage:
        package_path = Path(stage) / "deck.apkg"
        collection.export_anki_package(
            out_path=str(package_path),
            options=ExportAnkiPackageOptions(with_media=True, with_deck_configs=with_deck_configs),
            limit=DeckIdLimit(deck_id),
        )
        with ZipFile(package_path, "a") as package:
            package.write(upstream / "LICENSE", "licenses/eggrolls-LICENSE.txt")
            package.write(font_asset(FONT_LICENSE), "licenses/genwan-OFL-1.1.txt")
            for filename in ("Apache-2.0.txt", "NOTICE.txt"):
                package.write(TEMPLATES / "vendor" / filename, f"licenses/cloze-anything-{filename}")
            package.writestr(
                "licenses/ATTRIBUTION.txt",
                "Modified from the egg rolls JLPT deck under CC BY-NC 4.0.\n"
                "https://github.com/5mdld/anki-jlpt-decks\n\n"
                "Cloze Anything v0.3 by Matthew Hayes, Apache-2.0.\n"
                "https://github.com/matthayes/anki_cloze_anything/releases/tag/v0.3\n\n"
                "GenWan Serif v2.100 by ButTaiwan, SIL Open Font License 1.1.\n"
                "https://github.com/ButTaiwan/genwan-font/releases/tag/v2.100\n",
            )
        package_path.replace(output)


def build(upstream, output):
    source = upstream / "deck-source"
    layout = CardTemplates(source)
    samples = sample_rows(source / "notes.csv")
    with TemporaryDirectory(prefix="jlpt-preview-") as directory:
        collection = Collection(str(Path(directory) / "preview.anki2"))
        try:
            deck_id = collection.decks.id(PREVIEW_NAME)
            filenames = install_media(collection, source, layout, (values for values, _ in samples.values()))
            model = install_model(collection, layout, PREVIEW_NAME, filenames)

            for key, custom in SAMPLES.items():
                values, tags = samples[key]
                values = values | custom
                values["Alt2"] = custom.get("Alt2", "")
                set_preview_hints(values)
                entry = make_note(collection, model, values, tags, filenames, "jlpt-listening-preview")
                collection.add_note(entry, deck_id)

            results = Counter(str(card[0]) for card in collection.db.all("SELECT ord FROM cards WHERE did=?", deck_id))
            missing_templates = [item["name"] for i, item in enumerate(model["tmpls"]) if str(i) not in results]
            if missing_templates:
                raise RuntimeError(f"Preview did not exercise: {', '.join(missing_templates)}")
            if collection.get_empty_cards().notes:
                raise RuntimeError("The generated preview contains empty cards")

            export_deck(collection, upstream, deck_id, output)
            print(f"{len(SAMPLES)} notes, {sum(results.values())} cards, {len(results)} templates: {output}")
        finally:
            collection.close()


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path, help="checkout of 5mdld/anki-jlpt-decks")
    parser.add_argument("--out", type=Path, default=Path("dist/jlpt-listening-preview.apkg"))
    args = parser.parse_args()
    build(args.upstream.resolve(), args.out.resolve())
