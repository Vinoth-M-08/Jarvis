# tests/test_conversation.py
from jarvis.conversation import is_filler, is_goodbye, strip_wake_words


def test_wake_words_are_stripped_from_requests():
    assert strip_wake_words("Hey Jarvis, what time is it?") == "what time is it?"
    assert strip_wake_words("jarvis open spotify") == "open spotify"
    assert strip_wake_words("OK Jarvis.") == ""
    assert strip_wake_words("Ask Jarvis-like questions") == "Ask Jarvis-like questions"   # only at the start


def test_goodbyes_end_the_conversation_but_answers_do_not():
    for text in ["Thanks!", "that's all", "Stop listening.", "Goodbye, Jarvis."]:
        assert is_goodbye(text), text
    for text in ["No.", "Nothing", "Thanks, now open Chrome", "yes"]:
        assert not is_goodbye(text), text   # replies to Jarvis's own questions keep going


def test_fillers_and_whisper_noise_are_ignored():
    assert is_filler("Um.") and is_filler("you") and is_filler(" ")
    assert not is_filler("yes") and not is_filler("what's the weather")
