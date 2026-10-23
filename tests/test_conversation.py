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


def test_always_listening_phrases():
    from jarvis.conversation import after_name, is_sleep_command, is_wake_only, mentions_name
    assert is_sleep_command("Stop listening.") and is_sleep_command("go to sleep")
    assert not is_sleep_command("stop the music")
    assert mentions_name("Okay Jervis, wake up") and mentions_name("JARVIS") and not mentions_name("travis scott")
    assert after_name("okay so Jarvis, open Spotify") == "open Spotify"
    assert is_wake_only(after_name("Jarvis, wake up!")) and is_wake_only(after_name("Hey Jarvis"))
    assert not is_wake_only("open spotify")
