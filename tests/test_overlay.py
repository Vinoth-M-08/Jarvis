# tests/test_overlay.py — the floating Jarvis under the camera
from jarvis.overlay import Overlay, state_for


def test_events_drive_the_state():
    s = "idle"
    for event, want in [({"type": "status", "message": "Listening..."}, "listening"),
                        ({"type": "user", "text": "open notepad"}, "thinking"),
                        ({"type": "tool", "name": "win_app"}, "working"),
                        ({"type": "tool_result", "name": "win_app"}, "thinking"),
                        ({"type": "confirm_request"}, "approval"),
                        ({"type": "confirm_resolved"}, "thinking"),
                        ({"type": "status", "message": "Speaking..."}, "speaking"),
                        ({"type": "status", "message": "Sleeping."}, "sleeping"),
                        ({"type": "status", "message": "Locked — not listening."}, "idle"),
                        ({"type": "usage"}, "idle")]:
        s = state_for(event, s)
        assert s == want, event


def test_shows_only_when_no_dashboard_is_in_front_and_never_when_locked():
    locked = [False]
    o = Overlay(locked=lambda: locked[0])
    assert o.wanted                                  # no dashboard open
    o.dashboard_focus(1, True)
    assert not o.wanted                              # you're on the dashboard
    o.dashboard_focus(2, False)
    assert not o.wanted                              # another tab in the background doesn't matter
    o.dashboard_focus(1, False)
    assert o.wanted                                  # switched away
    o.dashboard_focus(1, True); o.dashboard_focus(1, None)
    assert o.wanted                                  # dashboard closed
    locked[0] = True
    assert not o.wanted


def test_speaking_shows_while_audio_plays():
    o = Overlay(speaking=lambda: True)
    o.state = "thinking"
    assert o.shown_state() == "speaking"
    o.state = "approval"
    assert o.shown_state() == "approval"             # a pending OK matters more


def test_frame_has_the_right_size_and_transparent_corners_only():
    o = Overlay(width=210, height=40)
    for state in ["listening", "thinking", "working", "speaking", "approval", "sleeping", "idle"]:
        o.state = state
        img = o.render(1.0, scale=1.5)
        assert img.size == (315, 60)
        px = img.load()
        assert px[0, 0] == (1, 1, 1)                 # corner: see-through
        inside = [px[x, y] for x in range(30, 285) for y in range(15, 45)]
        assert (1, 1, 1) not in inside               # no holes in the pill
