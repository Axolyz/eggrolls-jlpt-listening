# /// script
# requires-python = ">=3.12"
# dependencies = ["anki==26.9.4"]
# ///
"""Build a clean full deck and an audit from an egg rolls v3.5 source checkout."""

from argparse import ArgumentParser
from collections import Counter
from itertools import batched
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from anki.collection import AddNoteRequest, Collection

from card_templates import CardTemplates
from deck_rules import CardRules
from preview import export_deck, install_media, install_model, make_note, source_rows


DECK_NAME = "JLPT 例句听力 · 和色版"
MODEL_NAME = f"{DECK_NAME}（例句优先）"
GUID_NAMESPACE = "anki-jlpt-listening:full:v1"
EDITORIAL = Path(__file__).with_name("editorial.json")


def build(upstream, output, audit_file):
    source = upstream / "deck-source"
    rows = list(source_rows(source / "notes.csv"))
    note_ids = {note["NoteID"] for _, note, _ in rows}
    if "" in note_ids or len(note_ids) != len(rows):
        raise ValueError("Upstream NoteIDs must be nonempty and unique")
    editor = json.loads(EDITORIAL.read_text(encoding="utf-8"))
    rules = CardRules(rows, editor, source / "medias")
    selected = rules.select(rows)
    layout = CardTemplates(source)
    print(f"Selected cards for {len(selected):,} notes, {len(editor)} editorial entries", flush=True)
    with TemporaryDirectory(prefix="jlpt-full-") as directory:
        collection = Collection(str(Path(directory) / "full.anki2"))
        try:
            root = collection.decks.id(DECK_NAME)
            decks = {}
            for original in dict.fromkeys(deck for deck, _, _ in rows):
                _, separator, levels = original.partition("::")
                if not separator:
                    raise ValueError(f"Unexpected upstream deck: {original}")
                decks[original] = collection.decks.id(f"{DECK_NAME}::{levels}")
            deck = collection.decks.get(root)
            deck["desc"] = (
                '基于 egg rolls 的日语 JLPT 词汇牌组，针对例句听力调整开卡、排版与完形。'
                '<br><a href="https://github.com/5mdld/anki-jlpt-decks">原牌组与 CC BY-NC 4.0 授权</a>'
            )
            collection.decks.save(deck)
            filenames = install_media(collection, source, layout, (note for _, note, _ in selected))
            print(f"Registered {len(filenames):,} media references", flush=True)
            model = install_model(collection, layout, MODEL_NAME, filenames)
            for group in batched(selected, 500):
                collection.add_notes([
                    AddNoteRequest(make_note(collection, model, note, tags, filenames, GUID_NAMESPACE), decks[original])
                    for original, note, tags in group
                ])
            counts = dict(collection.db.all("select ord, count(*) from cards group by ord"))
            templates = {item["name"]: counts.get(item["ord"], 0) for item in model["tmpls"]}
            if collection.note_count() != len(rows) or collection.db.scalar(
                "select count(distinct nid) from cards where ord between 0 and 4"
            ) != len(rows):
                raise ValueError("A source note or its primary listening card is missing")
            if collection.get_empty_cards().notes:
                raise ValueError("The generated deck contains empty cards")
            if collection.db.scalar("select count(*) from revlog"):
                raise ValueError("A public package must not contain review progress")
            print(f"Exporting {sum(counts.values()):,} cards to {output}", flush=True)
            export_deck(collection, upstream, root, output)
        finally:
            collection.close()
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(json.dumps({
        "notes": len(rows), "cards": sum(templates.values()), "templates": templates,
        "editorial_notes": len(editor), "media": len(filenames),
        "issue_counts": dict(Counter(record["issue"] for record in rules.audit)), "checks": rules.audit,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Audit: {audit_file}")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("dist/jlpt-listening.apkg"))
    parser.add_argument("--audit", type=Path, default=Path("dist/deck-audit.json"))
    args = parser.parse_args()
    build(args.upstream.resolve(), args.out.resolve(), args.audit.resolve())
