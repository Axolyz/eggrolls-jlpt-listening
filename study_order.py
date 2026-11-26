"""Arrange first attempts without changing existing card and template identities."""

from itertools import batched

from anki.deck_config_pb2 import DeckConfig


def study_stage(template):
    if template.startswith("完形 "):
        return 2
    if template.endswith("音→意"):
        return 1
    return 0


def order_new_cards(collection, model):
    """Keep the existing word order, then sort each word's new cards by stage."""
    ranks = {template["ord"]: study_stage(template["name"]) for template in model["tmpls"]}
    rows = collection.db.all(
        """
        select c.id, c.nid, c.ord, c.due
        from cards c join notes n on n.id=c.nid
        where n.mid=? and c.type=0 and c.odid=0
        """,
        model["id"],
    )
    if not rows:
        return 0
    first_position = {}
    for _, nid, _, due in rows:
        first_position[nid] = min(due, first_position.get(nid, due))
    rows.sort(key=lambda row: (first_position[row[1]], row[1], ranks[row[2]], row[3], row[0]))
    start = min(first_position.values())
    changes = [(row[0], target) for target, row in enumerate(rows, start) if row[3] != target]
    for group in batched(changes, 500):
        cards = []
        for cid, position in group:
            card = collection.get_card(cid)
            card.due = position
            cards.append(card)
        collection.update_cards(cards, skip_undo_entry=True)
    next_position = start + len(rows)
    if next_position > collection.get_config("nextPos", 1):
        collection.set_config("nextPos", next_position)
    return len(changes)


def install_study_preset(collection, deck_ids):
    name = "egg rolls JLPT · 新卡顺序"
    preset_id = collection.decks.add_config_returning_id(name)
    preset = collection.decks.get_config(preset_id)
    options = DeckConfig.Config
    preset["newGatherPriority"] = options.NEW_CARD_GATHER_PRIORITY_DECK
    preset["newSortOrder"] = options.NEW_CARD_SORT_ORDER_NO_SORT
    preset["new"]["order"] = options.NEW_CARD_INSERT_ORDER_DUE
    preset["new"]["bury"] = True
    collection.decks.update_config(preset)
    for deck_id in deck_ids:
        collection.decks.set_config_id_for_deck_dict(collection.decks.get(deck_id), preset_id)
