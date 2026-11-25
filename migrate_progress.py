# /// script
# requires-python = ">=3.12"
# dependencies = ["anki==26.9.4"]
# ///
"""Import the full deck and copy progress only between equivalent review prompts."""

from argparse import ArgumentParser
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from time import time
import unicodedata

from anki.collection import Collection
from anki.import_export_pb2 import ImportAnkiPackageOptions, ImportAnkiPackageRequest

from build_deck import DECK_NAME, MODEL_NAME
from deck_rules import example, japanese, plain, reading, sound_names
from preview import CLOZE


LEGACY_MODEL = "eggrolls-JLPT10k-v3"
LEGACY_NAMES = (
    ["例句1形->音", "例句1形->意", "例句1音->意"]
    + [f"例句{slot}{kind}" for slot in (2, 3, 4) for kind in ("形->意", "音->意")]
    + ["1Cloze1", "1Cloze2", "2Cloze1", "2Cloze2", "自他动词"]
)
SCHEDULE = "type queue due ivl factor reps lapses left odue odid flags data".split()


@dataclass
class ReviewCard:
    card_id: int
    template: str
    fields: dict
    state: dict
    reviews: list

    @property
    def studied(self):
        return bool(self.reviews or self.state["type"] or self.state["reps"])

    @property
    def has_state(self):
        return self.studied or self.state["queue"] == -1 or bool(self.state["flags"])


@dataclass(frozen=True)
class Prompt:
    kind: str
    cue: str
    answer: str
    hint: str = ""
    audio: tuple = ()


def compact_answer(markup):
    return "".join(char for char in unicodedata.normalize("NFKC", plain(markup))
                   if not char.isspace() and not unicodedata.category(char).startswith("P"))


def cloze_answer(markup, number):
    answers = []

    def replace(match):
        target, *hint = match[2].split("::", 1)
        if int(match[1]) != number:
            return target
        answers.append(japanese(target))
        return f"⟦{plain(hint[0]) if hint else ''}⟧"

    masked = japanese(CLOZE.sub(replace, markup))
    return masked, "／".join(answers)


@cache
def digest_file(path):
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def audio_signature(media, values):
    return tuple(digest_file(media / name) for name in sound_names(values))


def prompt_for(card, media, legacy):
    note = card.fields
    name = card.template
    if legacy and "音->意" in name:
        slot = int(name[2])
        if note[f"SentType{slot}"]:
            return Prompt("听词", japanese(note["VocabKanji"]), plain(note["VocabDefSC"]), audio=audio_signature(media, note["VocabAudio"]))
        return Prompt("听句", japanese(example(note, slot)), plain(note[f"SentDefSC{slot}"]), audio=audio_signature(media, note[f"SentAudio{slot}"]))
    if not legacy and name.endswith("音→意"):
        if name.startswith("单词"):
            return Prompt("听词", japanese(note["VocabKanji"]), plain(note["VocabDefSC"]), plain(note["WordHintSC"]), audio_signature(media, note["VocabAudio"]))
        slot = int(name.split()[1])
        return Prompt("听句", japanese(example(note, slot)), plain(note[f"SentDefSC{slot}"]), plain(note[f"ListeningHintSC{slot}"]), audio_signature(media, note[f"SentAudio{slot}"]))
    if name in {"例句1形->音", "形→音"}:
        if not legacy:
            return Prompt("读音", japanese(note["ReadingPrompt"]), reading(note["ReadingAnswer"]))
        shown = note["VocabKanji"] if note["SentType1"] else note["SentFurigana1"]
        return Prompt("读音", japanese(shown), reading(note["VocabFurigana"]))
    if "形->意" in name or "形→意" in name:
        if legacy:
            slot = int(name[2])
            shown = note["VocabKanji"] if note[f"SentType{slot}"] else note[f"SentFurigana{slot}"]
            if note["SwitchtoAllSent"] or slot != 1:
                return Prompt("看句译意", japanese(shown), plain(note[f"SentDefSC{slot}"]))
            return Prompt("看词", japanese(shown), plain(note["VocabDefSC"]))
        if name.startswith("单词"):
            return Prompt("看词", japanese(note["VocabKanji"]), plain(note["VocabDefSC"]))
        slot = int(name.split()[1])
        return Prompt("看词", japanese(example(note, slot)), plain(note[f"TargetDefSC{slot}"] or note["VocabDefSC"]))
    if match := re.fullmatch(r"(\d)Cloze(\d)|完形 (\d) · c(\d)", name):
        group, number = map(int, (match[1], match[2]) if legacy else (match[3], match[4]))
        body = note[f"{group}Cloze"] if legacy else note[f"ClozeText{group}"]
        hint = " ".join(filter(None, (plain(note[f"{group}ClozeDef"]), plain(note[f"{group}ClozeRemark"])))) if legacy else plain(note[f"ClozeHint{group}"])
        shown, answer = cloze_answer(body, number)
        return Prompt("完形", shown, answer, hint)
    return Prompt(name, "", "")


def equivalent(old, new):
    return (
        old.kind == new.kind and bool(old.cue and old.answer)
        and old.cue == new.cue and compact_answer(old.answer) == compact_answer(new.answer)
        and compact_answer(old.hint) == compact_answer(new.hint)
        and old.audio == new.audio
        and (not old.kind.startswith("听") or (bool(old.audio) and all(old.audio)))
    )


def model_rows(db, model_name):
    models = [mid for mid, name in db.execute("select id, name from notetypes") if name == model_name]
    if len(models) != 1:
        raise ValueError(f"Expected one note type named {model_name!r}, found {len(models)}")
    return models[0]


def load_cards(db, model_name):
    mid = model_rows(db, model_name)
    names = [name for (name,) in db.execute("select name from fields where ntid=? order by ord", (mid,))]
    templates = dict(db.execute("select ord,name from templates where ntid=? order by ord", (mid,)))
    if model_name == LEGACY_MODEL and list(templates.values()) != LEGACY_NAMES:
        raise ValueError("Legacy note type does not match the audited layout")
    note_map = {nid: dict(zip(names, flds.split("\x1f"), strict=True))
                for nid, flds in db.execute("select id,flds from notes where mid=?", (mid,))}
    review_map = defaultdict(list)
    for event in db.execute("""
        select r.id,r.cid,r.ease,r.ivl,r.lastIvl,r.factor,r.time,r.type
        from revlog r join cards c on c.id=r.cid join notes n on n.id=c.nid
        where n.mid=? order by r.id
    """, (mid,)):
        review_map[event[1]].append(event)
    cards = []
    state_cols = ",".join(f"c.{column}" for column in SCHEDULE)
    for card_id, nid, ordinal, *state in db.execute(f"""
        select c.id,c.nid,c.ord,{state_cols}
        from cards c join notes n on c.nid=n.id where n.mid=? order by c.id
    """, (mid,)):
        cards.append(ReviewCard(card_id, templates[ordinal], note_map[nid], dict(zip(SCHEDULE, state, strict=True)), review_map[card_id]))
    return cards


def reason_for(old, candidates):
    if not candidates:
        return "新版缺少同 NoteID"
    same_type = [prompt for _, prompt in candidates if prompt.kind == old.kind]
    if not same_type:
        return "新版无相同考点的卡型"
    same_cue = [prompt for prompt in same_type if prompt.cue == old.cue]
    if not same_cue:
        return "例句、词形或挖空位置不同"
    same_answer = [prompt for prompt in same_cue if compact_answer(prompt.answer) == compact_answer(old.answer)]
    if not same_answer:
        return "答案或译文有变动"
    if all(compact_answer(prompt.hint) != compact_answer(old.hint) for prompt in same_answer):
        return "题面提示有变动"
    return "录音有变动或无法核验"


def match_cards(old_cards, new_cards, old_media, new_media):
    candidates_by_id = defaultdict(list)
    tentative = []
    pending = []
    for card in new_cards:
        candidates_by_id[card.fields["NoteID"]].append((card, prompt_for(card, new_media, False)))
    for card in old_cards:
        if not card.has_state:
            continue
        old = prompt_for(card, old_media, True)
        candidates = candidates_by_id[card.fields["NoteID"]]
        matches = [item for item, new in candidates if equivalent(old, new)]
        if len(matches) == 1 and not card.state["odid"] and not matches[0].has_state:
            tentative.append((card, matches[0], old, candidates))
        else:
            reason = reason_for(old, candidates)
            if len(matches) > 1:
                reason = "多个新版候选都符合"
            elif card.state["odid"]:
                reason = "旧卡正在筛选牌组"
            elif matches:
                reason = "新版卡已有进度"
            pending.append((card, old, candidates, reason))
    counts = Counter(new.card_id for _, new, _, _ in tentative)
    matched = []
    for old, new, prompt, candidates in tentative:
        if counts[new.card_id] == 1:
            matched.append((old, new))
        else:
            pending.append((old, prompt, candidates, "多张旧卡命中同一张新卡"))
    return matched, pending


def copy_progress(db, matched):
    taken = {row[0] for row in db.execute("select id from revlog")}
    events = []
    updates = []
    for old, new in matched:
        state = old.state.copy()
        if not old.studied:
            state["due"] = new.state["due"]
        updates.append((*[state[field] for field in SCHEDULE], int(time()), new.card_id))
        for event_id, _, *review in old.reviews:
            copied_id = event_id + 1
            while copied_id in taken:
                copied_id += 1
            if copied_id - event_id > 1000:
                raise ValueError(f"Too many duplicate review ids near {event_id}")
            taken.add(copied_id)
            events.append((copied_id, new.card_id, *review))
    assignments = ",".join(f"{key}=?" for key in SCHEDULE)
    db.executemany(f"update cards set {assignments},mod=?,usn=-1 where id=?", updates)
    db.executemany("insert into revlog (id,cid,usn,ease,ivl,lastIvl,factor,time,type) values (?,?,-1,?,?,?,?,?,?)", events)
    return len(events)


def open_sqlite(path, readonly=False):
    database = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)
    if readonly:
        # A closed Anki collection may use WAL with no sidecar files yet.
        database.execute("pragma query_only=on")
    database.create_collation("unicase", lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
    return database


def backup(source, output):
    with closing(open_sqlite(source, readonly=True)) as original, output.open("xb"):
        output.chmod(0o600)
        with closing(open_sqlite(output)) as saved:
            original.backup(saved)
            if saved.execute("pragma quick_check").fetchone() != ("ok",):
                raise ValueError("Collection backup failed its integrity check")


def legacy_fingerprint(db, model):
    mid = model_rows(db, model)
    sha = sha256()
    for query in (
        "select * from notes where mid=? order by id",
        "select c.* from cards c join notes n on n.id=c.nid where n.mid=? order by c.id",
        "select r.* from revlog r join cards c on c.id=r.cid join notes n on n.id=c.nid where n.mid=? order by r.id",
    ):
        for row in db.execute(query, (mid,)):
            sha.update(repr(row).encode() + b"\n")
    return sha.hexdigest()


def make_report(folder, summary, matched, pending):
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    pending_rows = []
    for old, prompt, candidates, reason in pending:
        pending_rows.append({
            "note_id": old.fields["NoteID"], "word": japanese(old.fields["VocabKanji"]),
            "old": old.card_id, "type": old.template, "reviews": len(old.reviews), "reps": old.state["reps"],
            "studied": old.studied, "suspended": old.state["queue"] == -1, "reason": reason,
            "prompt": prompt.cue, "answer": prompt.answer, "hint": prompt.hint,
            "candidates": [{"id": card.card_id, "type": card.template, "prompt": new.cue,
                            "answer": new.answer, "hint": new.hint} for card, new in candidates],
        })
    pending_rows.sort(key=lambda row: (not row["studied"], -row["reviews"], row["word"], row["old"]))
    details = {**summary, "matches": [{"old": old.card_id, "new": new.card_id} for old, new in matched], "pending": pending_rows}
    (folder / "migration.json").write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Anki 迁移裁决", "", f"目标牌组为「{DECK_NAME}」。", "",
        f"已匹配 {summary['matched']} 张卡，复制 {summary['reviews_copied']} 条复习记录。",
        f"待裁决 {len(pending_rows)} 张，其中有学习记录或排程的 {sum(row['studied'] for row in pending_rows)} 张。",
        "同一份复习历史暂时同时出现在新旧卡上。保留旧牌组期间，全库统计会计入这两份记录。",
        "在 Anki 浏览器里用下面的 `cid:` 搜索定位旧卡和候选卡。", "",
    ]
    for row in pending_rows:
        display = f"{row['word']} · {row['type']} · cid:{row['old']}"
        status = "暂停" if row["suspended"] else f"复习记录 {row['reviews']} 条"
        lines.extend((f"### {display}", "", f"{row['reason']}。{status}。", "",
                      f"旧题：{row['prompt']} → {row['answer']}" + (f"（提示：{row['hint']}）" if row["hint"] else ""), ""))
        for new in row["candidates"]:
            lines.append(f"- `{new['type']}` · `cid:{new['id']}` · {new['prompt']} → {new['answer']}" + (f"（提示：{new['hint']}）" if new["hint"] else ""))
        lines.append("")
    (folder / "MIGRATION-REVIEW.md").write_text("\n".join(lines), encoding="utf-8")

    entries = []
    for row in pending_rows:
        attrs = 'data-study="1"' if row["studied"] else 'data-study="0" hidden'
        old_title = escape(f"{row['word']} · {row['type']} · {row['reason']}")
        old_prompt = escape(f"{row['prompt']} → {row['answer']} {row['hint']}")
        options = "".join(
            f"<li><code>cid:{new['id']}</code> {escape(new['type'])} · "
            f"{escape(new['prompt'])} → {escape(new['answer'])} {escape(new['hint'])}</li>"
            for new in row["candidates"]
        )
        entries.append(
            f"<details {attrs}><summary>{old_title} · {row['reviews']} 条复习记录</summary>"
            f"<p>旧卡 <code>cid:{row['old']}</code> · {old_prompt}</p>"
            f"<ul>{options}</ul></details>"
        )
    page = f"""<!doctype html><html lang="zh-Hans"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Anki 迁移裁决</title>
<style>
  :root {{ color-scheme: light dark; font: 16px/1.55 system-ui, sans-serif; }}
  body {{ max-width: 74rem; margin: 2rem auto; padding: 0 1rem; }}
  input[type=search] {{ padding: .4rem .5rem; width: min(24rem,100%); font: inherit; }}
  label {{ display: inline-block; margin: .5rem; }}
  details {{ margin: .4rem 0; padding: .55rem .7rem; border: 1px solid GrayText; border-radius: .4rem; }}
  summary {{ cursor: pointer; }}
</style>
<h1>Anki 迁移裁决</h1>
<p>待裁决 {summary['pending']} 张，其中 {summary['studied_pending']} 张有学习记录或排程。复制卡片 ID 到 Anki 浏览器搜索即可定位。</p>
<input id="filter" type="search" aria-label="筛选卡片" placeholder="词条、卡型、原因或卡片 ID">
<label><input type="checkbox" id="unstudied"> 包括没有学习记录的卡</label>
<p id="count" aria-live="polite"></p>
{''.join(entries)}
<script>
const filter = document.getElementById('filter');
const unstudied = document.getElementById('unstudied');
const count = document.getElementById('count');
const rows = document.querySelectorAll('details');
function show() {{
  const term = filter.value.trim().toLowerCase();
  let visible = 0;
  rows.forEach(row => {{
    row.hidden = (!unstudied.checked && row.dataset.study === '0') || !row.textContent.toLowerCase().includes(term);
    if (!row.hidden) visible++;
  }});
  count.textContent = `显示 ${{visible}} 张`;
}}
filter.addEventListener('input', show);
unstudied.addEventListener('change', show);
show();
</script></html>"""
    (folder / "migration-review.html").write_text(page, encoding="utf-8")


def import_package(database, package, legacy_model):
    collection = Collection(str(database))
    try:
        options = ImportAnkiPackageOptions(merge_notetypes=False, with_scheduling=False, with_deck_configs=False)
        collection.import_anki_package(ImportAnkiPackageRequest(package_path=str(package), options=options))
        legacy_root = next((deck for deck in collection.decks.all() if deck["name"] == legacy_model), None)
        if not legacy_root:
            return {}
        root_config = collection.decks.get_config(legacy_root["conf"])
        children = [collection.decks.get_config(deck["conf"]) for deck in collection.decks.all()
                    if deck["name"].startswith(legacy_model + "::") and not deck["dyn"]]
        active = [preset for preset in children if preset["new"]["perDay"] > 0]
        child_config = min(active, key=lambda preset: preset["new"]["perDay"], default=root_config)
        presets = {
            key: collection.decks.add_config_returning_id(f"{DECK_NAME} · {key}", deepcopy(config))
            for key, config in (("总牌组", root_config), ("子牌组", child_config))
        }
        for deck in collection.decks.all():
            if deck["name"] == DECK_NAME:
                collection.decks.set_config_id_for_deck_dict(deck, presets["总牌组"])
            elif deck["name"].startswith(DECK_NAME + "::"):
                collection.decks.set_config_id_for_deck_dict(deck, presets["子牌组"])
        return {key: {"name": collection.decks.get_config(preset)["name"],
                      "new_per_day": collection.decks.get_config(preset)["new"]["perDay"],
                      "retention": collection.decks.get_config(preset).get("desiredRetention")}
                for key, preset in presets.items()}
    finally:
        collection.close()


def migrate(original, database, package, report, legacy_model):
    with closing(open_sqlite(original, readonly=True)) as before:
        old_hash = legacy_fingerprint(before, legacy_model)
        old_cards = load_cards(before, legacy_model)
        if not old_cards or any(name == MODEL_NAME for (name,) in before.execute("select name from notetypes")):
            raise ValueError("Missing legacy cards or target deck already imported")
    presets = import_package(database, package, legacy_model)
    with closing(open_sqlite(database)) as after, after:
        new_cards = load_cards(after, MODEL_NAME)
        if any(card.has_state for card in new_cards):
            raise ValueError("Expected a fresh target deck")
        old_media = original.with_suffix(".media")
        new_media = database.with_suffix(".media")
        matched, pending = match_cards(old_cards, new_cards, old_media, new_media)
        copied = copy_progress(after, matched)
        if old_hash != legacy_fingerprint(after, legacy_model):
            raise ValueError("The source cards or history changed")
        if after.execute("pragma quick_check").fetchone() != ("ok",):
            raise ValueError("Post-migration integrity check failed")
        actual = load_cards(after, MODEL_NAME)
        actual_by_id = {card.card_id: card for card in actual}
        if not all(actual_by_id[new.card_id].state["reps"] == old.state["reps"]
                   and len(actual_by_id[new.card_id].reviews) == len(old.reviews) for old, new in matched):
            raise ValueError("Progress verification failed")
    summary = {
        "deck": DECK_NAME, "notes": len({card.fields['NoteID'] for card in new_cards}),
        "cards": len(new_cards), "matched": len(matched), "studied_matched": sum(old.studied for old, _ in matched),
        "reviews_copied": copied, "pending": len(pending),
        "studied_pending": sum(old.studied for old, *_ in pending),
        "pending_reasons": dict(Counter(reason for *_, reason in pending)),
        "local_presets": presets,
    }
    make_report(report, summary, matched, pending)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def require_closed_anki():
    processes = subprocess.run(["pgrep", "-x", "Anki"], capture_output=True, text=True, check=False)
    if processes.returncode == 0:
        raise RuntimeError("Close Anki before modifying the collection")
    if processes.returncode != 1:
        raise RuntimeError(f"Could not determine if Anki is running: {processes.stderr.strip()}")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--legacy-model", default=LEGACY_MODEL)
    parser.add_argument("--apply", action="store_true", help="Import and copy into the chosen collection instead of a temporary copy")
    args = parser.parse_args()
    original = args.collection.resolve(strict=True)
    report = args.report.resolve()
    package = args.package.resolve(strict=True)
    os.umask(0o077)
    if args.apply:
        require_closed_anki()
        report.mkdir(mode=0o700, parents=True, exist_ok=True)
        saved = report / f"collection-before-{datetime.now():%Y%m%d-%H%M%S}.anki2"
        backup(original, saved)
        print(f"Backup: {saved}", flush=True)
        migrate(original, original, package, report, args.legacy_model)
    else:
        with TemporaryDirectory(prefix="jlpt-migration-") as folder:
            database = Path(folder) / "collection.anki2"
            backup(original, database)
            migrate(original, database, package, report, args.legacy_model)
