"""Read the JLPT card's playback setting for Anki Desktop's native player."""

import re

from aqt import gui_hooks, mw, sound
from aqt.mpv import MPVCommandError

RATE = re.compile(r"--jlpt-replay-rate\s*:\s*(\d+(?:\.\d+)?|\.\d+)\s*[;}]")
_previous_speed = None


def requested_speed(css):
    match = RATE.search(css)
    speed = float(match[1]) if match else 1.0
    return speed if 0.5 <= speed <= 2 else 1.0


def apply_native_speed(_tag):
    global _previous_speed
    player = sound.mpvManager
    if player is None or sound.av_player.current_player is not player:
        return
    card = mw.reviewer.card if mw.state == "review" else None
    speed = requested_speed(card.note_type()["css"]) if card else 1.0
    try:
        if speed != 1.0:
            if _previous_speed is None:
                _previous_speed = player.get_property("speed")
            player.set_property("speed", speed)
        elif _previous_speed is not None:
            player.set_property("speed", _previous_speed)
            _previous_speed = None
    except (MPVCommandError, OSError):
        # Playback should still proceed if mpv can't set its rate.
        pass


gui_hooks.av_player_will_play.append(apply_native_speed)
