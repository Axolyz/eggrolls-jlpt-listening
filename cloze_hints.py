"""Load and format the reviewed vocabulary cloze prompts."""

import csv
from html import escape
from pathlib import Path
import re

CLOZE = re.compile(r"\(\(c(\d+)::([\s\S]+?\)*)\)\)")
VISIBLE_HINT_CSS = '''
.JLPTSurface .JLPTHint[data-cloze-hint="1"] [data-jlpt-answer-hint="2"],
.JLPTSurface .JLPTHint[data-cloze-hint="2"] [data-jlpt-answer-hint="1"] {
  display: none;
}
'''



def load_cloze_reviews():
    reviews = {}
    path = Path(__file__).with_name("cloze-review.tsv")
    with path.open(encoding="utf-8", newline="") as stream:
        for review in csv.DictReader(stream, delimiter="\t"):
            key = (review["note_id"], int(review["group"]))
            if key in reviews or not review["reason"]:
                raise ValueError(f"Invalid cloze review for {key}")
            reviews[key] = review
    return reviews


def reviewed_hints(prompt, review, normalize):
    numbers = set()
    for number, content in CLOZE.findall(prompt):
        numbers.add(number)
        target = normalize(content.split("::", 1)[0])
        hint = review.get(f"hint{number}", "")
        if target in hint:
            raise ValueError(f"Cloze cue gives away {target!r}: {hint}")
    hints = {number: review[f"hint{number}"] for number in ("1", "2") if review[f"hint{number}"]}
    if not hints or hints.keys() != numbers:
        raise ValueError(f"Missing reviewed cue for {review['head']!r}")
    return "".join(
        f'<span data-jlpt-answer-hint="{number}">{escape(hint.rstrip("。") + "。")}</span>'
        for number, hint in hints.items()
    )
