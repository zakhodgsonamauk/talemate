"""
Tests for sex tags and sex negatives in the image prompt.

Why this exists: a male character was rendered as a nude female. The prompt said
"human male" in natural language, which a booru-trained Pony checkpoint barely
registers, while `solo` and `looking at viewer` sat near the front pulling toward
the checkpoint's default subject. Nothing in the negative prompt pushed back —
`_add_species_negatives` returns early for humans, so a human subject had no
correction at all.
"""

import pytest

from talemate.agents.visual.generation import (
    NUDITY_NEGATIVES,
    SEX_NEGATIVES,
    SEX_TAGS,
    normalise_sex,
)


# === normalise_sex ===
#
# The substring trap: "female" contains "male" and "woman" contains "man". A naive
# `"male" in value` test inverts the sex, which would put `1boy` on a woman.


@pytest.mark.parametrize(
    "value,expected",
    [
        ("male", "male"),
        ("Male", "male"),
        ("man", "male"),
        ("a man in his forties", "male"),
        ("boy", "male"),
        ("masculine", "male"),
        ("female", "female"),
        ("Female", "female"),
        ("woman", "female"),
        ("a woman, tall", "female"),
        ("girl", "female"),
        ("feminine", "female"),
    ],
)
def test_normalise_sex_resolves_clear_values(value, expected):
    assert normalise_sex(value) == expected


def test_female_is_not_read_as_male():
    """`"male" in "female"` is True — word boundaries are what prevent the inversion."""
    assert normalise_sex("female") == "female"


def test_woman_is_not_read_as_man():
    assert normalise_sex("woman") == "female"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "non-binary",
        "unspecified",
        "android",
        "male and female twins",  # genuinely ambiguous
        "she is a man-eater",  # both signals present
    ],
)
def test_normalise_sex_declines_to_guess(value):
    """No tag is better than a wrong tag: a wrong one actively fights the prompt."""
    assert normalise_sex(value) is None


# === tag and negative tables ===


def test_male_gets_booru_tags_not_natural_language():
    assert "1boy" in SEX_TAGS["male"]
    assert "male focus" in SEX_TAGS["male"]
    assert "human male" not in SEX_TAGS["male"]


def test_female_gets_booru_tag():
    assert "1girl" in SEX_TAGS["female"]


def test_negatives_oppose_the_subject_sex():
    """The default the checkpoint would otherwise reach for."""
    assert "1girl" in SEX_NEGATIVES["male"]
    assert "breasts" in SEX_NEGATIVES["male"]
    assert "1boy" in SEX_NEGATIVES["female"]


def test_negatives_never_contradict_their_own_positive_tags():
    """A tag must not appear on both sides of the same generation."""
    for sex, tags in SEX_TAGS.items():
        overlap = set(tags) & set(SEX_NEGATIVES[sex])
        assert not overlap, f"{sex}: {overlap} is both positive and negative"


def test_no_tag_is_both_positive_and_negative_including_nudity():
    """The invariant that matters: nothing may be asked for and forbidden at once.

    Overlap between the sex and nudity negatives is fine and deliberate — `nipples` is
    negated unconditionally for a male subject and only conditionally for a dressed
    female one — and duplicates are filtered when the negatives are assembled.
    """
    for sex, tags in SEX_TAGS.items():
        forbidden = set(SEX_NEGATIVES[sex]) | set(NUDITY_NEGATIVES)
        overlap = set(tags) & forbidden
        assert not overlap, f"{sex}: {overlap} is both requested and forbidden"


def test_negatives_are_not_duplicated_when_the_sets_overlap():
    from talemate.agents.visual.generation import SEX_NEGATIVES as sn

    combined = list(sn["male"]) + list(NUDITY_NEGATIVES)
    # The assembly filters against what is already present; this pins that the overlap
    # exists and so the filtering is load-bearing rather than incidental.
    assert len(combined) != len(set(combined))


# === character_sex — reading the sex from whatever evidence exists ===
#
# Observed live: sex conditioning silently did nothing for Zak. His attributes were
# generated during play and arrived Title-Cased with no `gender` key at all -
# Age/Appearance/Background/Current Status/Name/Personality/Skills/Weaknesses - so
# `Character.gender` returned "" and no tags were added. Characters authored from a
# character card have a lowercase `gender`, which is why the gap went unnoticed.


class _Character:
    def __init__(self, base_attributes=None, description=""):
        self.base_attributes = base_attributes or {}
        self.description = description


def test_explicit_lowercase_gender_attribute():
    from talemate.agents.visual.generation import character_sex

    assert character_sex(_Character({"gender": "female"})) == "female"


def test_title_cased_gender_attribute_is_still_found():
    """Attribute casing varies by how the character was created."""
    from talemate.agents.visual.generation import character_sex

    assert character_sex(_Character({"Gender": "male"})) == "male"


def test_sex_attribute_is_accepted_as_well_as_gender():
    from talemate.agents.visual.generation import character_sex

    assert character_sex(_Character({"Sex": "female"})) == "female"


def test_falls_back_to_appearance_prose_when_no_gender_attribute():
    """The exact shape that failed: no gender key, but the appearance says so."""
    from talemate.agents.visual.generation import character_sex

    zak = _Character(
        {
            "Name": "Zak",
            "Age": "Twenty-nine",
            "Appearance": (
                "Medium height, approximately 5'10\", with an athletic build. His beard "
                "is full and neatly trimmed, a darker shade than his sun-bleached hair."
            ),
        }
    )
    assert character_sex(zak) == "male"


def test_falls_back_to_description_when_attributes_say_nothing():
    from talemate.agents.visual.generation import character_sex

    assert (
        character_sex(_Character({}, description="She keeps her helmet on.")) == "female"
    )


def test_explicit_attribute_beats_prose():
    """A stated gender is authoritative; prose is only consulted when it is missing."""
    from talemate.agents.visual.generation import character_sex

    c = _Character(
        {"gender": "female", "Appearance": "his coat, his boots, his gloves"},
    )
    assert character_sex(c) == "female"


def test_prose_tolerates_a_mention_of_someone_else():
    """Prose about one person routinely names another; a single word must not veto."""
    from talemate.agents.visual.generation import dominant_sex

    assert dominant_sex("She wears her brother's coat, the one he left behind") == "female"


def test_prose_with_no_clear_majority_decides_nothing():
    from talemate.agents.visual.generation import dominant_sex

    assert dominant_sex("he and she, his and hers") is None
    assert dominant_sex("") is None
    assert dominant_sex(None) is None


def test_missing_character_is_handled():
    from talemate.agents.visual.generation import character_sex

    assert character_sex(None) is None
    assert character_sex(_Character()) is None


def test_a_stated_non_binary_gender_is_not_overruled_by_prose():
    """A stated gender that reads as neither is an answer, not a gap.

    Appearance prose is full of gendered words; letting it overrule an explicit
    "non-binary" would misgender a character the author described deliberately.
    """
    from talemate.agents.visual.generation import character_sex

    c = _Character(
        {"gender": "non-binary", "Appearance": "alien woman, deep violet skin, she waits"}
    )
    assert character_sex(c) is None


# === nudity negatives: dressed-and-exposed is its own failure mode ===
#
# Observed live: a male subject wearing jeans and a tank top was rendered with the
# trousers open and genitals exposed. "nude, naked, topless" describe none of that —
# he was not nude, he was dressed and exposed.


def test_nudity_negatives_cover_dressed_but_exposed():
    from talemate.agents.visual.generation import NUDITY_NEGATIVES

    for tag in ("penis", "exposed genitals", "pubic hair", "unzipped", "open pants"):
        assert tag in NUDITY_NEGATIVES, f"{tag} missing — the observed failure survives"


def test_undress_intent_words_do_not_overlap_clothing_words():
    """`unzipped` must read as intent, not as a garment, or the check contradicts itself."""
    from talemate.agents.visual.generation import (
        _CLOTHING_WORDS,
        _UNDRESS_INTENT_WORDS,
    )

    assert not _CLOTHING_WORDS & _UNDRESS_INTENT_WORDS
