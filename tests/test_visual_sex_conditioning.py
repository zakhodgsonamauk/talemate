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


def test_nudity_negatives_are_separate_from_sex_negatives():
    """They are conditional on the subject being dressed, so they cannot be merged."""
    for negatives in SEX_NEGATIVES.values():
        assert not set(NUDITY_NEGATIVES) & set(negatives)
