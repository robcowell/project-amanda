"""Tests for the text Piper is given, as against the text that is displayed.

Piper's phonemiser ends a sentence at any full stop, so "Dr. Patel" was spoken
as two sentences with a pause between -- heard, then measured at 110ms.
"""

from __future__ import annotations

from amanda.audio.piper_provider import spoken_text


def test_a_title_before_a_name_is_spelled_out():
    assert spoken_text("Dr. Patel said the walk did him good.") == (
        "Doctor Patel said the walk did him good."
    )
    assert spoken_text("Ask Mrs. Hughes and Prof. Lee.") == "Ask Missus Hughes and Professor Lee."


def test_a_real_sentence_end_is_left_alone():
    """No name after it, so it may be a full stop doing its job."""
    assert spoken_text("Call the dr. then.") == "Call the dr. then."
    assert spoken_text("It was Dr.") == "It was Dr."


def test_a_title_ending_a_sentence_before_a_capital_is_the_known_cost():
    """Indistinguishable from a title by this rule, so the sentence break is
    lost. Accepted: far rarer in a reply than "Dr. Patel" is."""
    assert spoken_text("She wanted to be a Dr. It never happened.") == (
        "She wanted to be a Doctor It never happened."
    )


def test_ambiguous_abbreviations_are_not_guessed():
    """Saint or Street: better a pause than the wrong word."""
    assert spoken_text("St. Paul's is on Baker St. Honestly.") == (
        "St. Paul's is on Baker St. Honestly."
    )


def test_numbers_and_ordinary_words_are_untouched():
    text = "The reading was 3.14 exactly. Mr Jones, Drake and Ms Smith came."
    assert spoken_text(text) == text
