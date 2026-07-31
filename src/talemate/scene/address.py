"""
Direct-address detection.

Answers one question cheaply, with no LLM call: did that message just speak to,
ask something of, or act upon the player's character?

The scene loop uses this to decide when to hand control back. It is deliberately
biased toward false negatives — a miss is caught by the `max_ai_turns` backstop
and reads as the story being a little slow to notice you, whereas a false
positive hands control back constantly, which is the behaviour this whole track
exists to remove.

Messages are stored as ``"Name: body"``. The ``movie_script`` conversation
format is a prompt-rendering concern only and does not change stored shape.
"""

import re
from typing import TYPE_CHECKING

__all__ = ["addresses_player"]

if TYPE_CHECKING:
    from talemate.scene_message import CharacterMessage


# "you", but not "your" used possessively about a third party — kept simple on
# purpose; the possessive still implies the listener is being spoken to.
SECOND_PERSON = re.compile(
    r"\b(you|your|yours|you're|youre|yourself)\b",
    re.IGNORECASE,
)

DIALOGUE = re.compile(r'"([^"]*)"')
ACTION = re.compile(r"\*([^*]*)\*")


def _vocative(text: str, name: str) -> bool:
    """True when `name` is being spoken to rather than spoken about.

    Vocatives sit at a clause boundary next to a comma, or stand alone:
    "Rennick, look at this", "That was reckless, Rennick.", "Rennick!"
    """
    escaped = re.escape(name)
    patterns = (
        rf"^\s*{escaped}\s*[,!?:]",  # leading vocative
        rf",\s*{escaped}\s*[.!?,]?\s*$",  # trailing vocative
        rf"^\s*{escaped}\s*$",  # the name alone
    )
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE) is not None


def addresses_player(
    message: "CharacterMessage",
    player_name: str,
    other_names: list[str] | None = None,
) -> bool:
    """Whether `message` addresses or acts upon the player's character.

    Args:
        message: the message to inspect.
        player_name: the player character's name.
        other_names: other active character names, used to resolve who a
            second-person pronoun is aimed at.

    Returns:
        True only when the player is plausibly the addressee or the target of an
        action. Ambiguity resolves to False.
    """
    if not player_name:
        return False

    others = [n for n in (other_names or []) if n and n != player_name]

    body = message.message
    if ":" in body:
        speaker, _, body = body.partition(":")
        # The player's own line never counts as addressing the player.
        if speaker.strip().lower() == player_name.strip().lower():
            return False

    dialogue = " ".join(DIALOGUE.findall(body))
    actions = " ".join(ACTION.findall(body))

    # An action performed upon the player, named or in second person.
    if actions and (_mentions(actions, player_name) or SECOND_PERSON.search(actions)):
        return True

    if not dialogue:
        return False

    # Spoken to by name.
    if _vocative(dialogue, player_name):
        return True

    # Second person: only the player if nobody else is being addressed instead.
    if SECOND_PERSON.search(dialogue):
        if any(_vocative(dialogue, other) for other in others):
            return False
        if _mentions(dialogue, player_name):
            return True
        # Nobody named at all — the player is the plausible listener, unless
        # another character is mentioned and could just as easily be it.
        if not any(_mentions(dialogue, other) for other in others):
            return True

    # Named inside dialogue without a vocative or second person is talk *about*
    # the player, not to them.
    return False
