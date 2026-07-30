#!/usr/bin/env python3
"""
The chord resolver, including the path that decides whether chord bindings are
safe to use while recording.

A hotkey that is a chord creates a problem an unmodified hotkey does not. The
modifier arrives BEFORE the tool can know whether a hotkey is coming. Write it
into the recording immediately and every Control+F9 press leaves a stray
Control in the user's macro. Throw it away and every ordinary Control+C the
user records loses its Control. Neither is acceptable, so the resolver holds
the modifier back and then either drops it (the chord completed, so it was the
tool's key) or puts it back AT ITS ORIGINAL TIMESTAMP the moment the chord
becomes impossible.

Putting it back at the END would be a different bug: playback replays file
order, so a modifier reinserted after the key it modified would replay in the
wrong order. The reinsertion is therefore IN TIME ORDER, before any event that
happened while the resolver was waiting, and that is the assertion this file
exists for.

These proofs drive the listener callbacks directly and construct key objects
exactly as the macOS backend does (KeyCode.from_char with its vk, so physical
identity is real), because what is under test is ordering and recording
CONTENT, which needs deterministic sequencing rather than injected timing.
The listener-driven end to end chord proof lives in gate_a.py case ii.

Isolation: an injected temporary recordings directory. Nothing here can reach
your recordings or your config.
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP_ROOT = Path(tempfile.mkdtemp(prefix="macro_chord_"))
TMP_RECORDINGS = TMP_ROOT / "recordings"
TMP_RECORDINGS.mkdir(parents=True)
TMP_CONFIG = TMP_ROOT / "config.json"

import macro  # noqa: E402
macro.RECORDINGS_DIR = TMP_RECORDINGS

import bindings  # noqa: E402
from pynput import keyboard, mouse  # noqa: E402

results = []

CTRL_L = keyboard.Key.ctrl_l
CTRL_R = keyboard.Key.ctrl_r
SHIFT_L = keyboard.Key.shift
F9 = keyboard.Key.f9
# built the way the macOS backend builds it: a character AND its key code
C_KEY = keyboard.KeyCode.from_char("c", vk=8)

# On macOS pynput aliases ctrl_l onto plain ctrl, so the name that reaches a
# recording is "ctrl". Read it off the key rather than assuming, since that
# aliasing is a platform detail and not something to hardcode.
CL = CTRL_L.name
CR = CTRL_R.name


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("   %-58s %s%s" % (name, "PASS" if ok else "FAIL",
                             ("  " + detail) if detail else ""))
    sys.stdout.flush()


def gap():
    """Enough real time that two events cannot share a timestamp."""
    time.sleep(0.012)


def chord_map():
    """record on Control+F9, play and autoclick left on plain keys."""
    return {
        "record": bindings.make_binding(["ctrl"],
                                        {"kind": "named", "name": "f9"}),
        "play": bindings.make_binding([], {"kind": "named", "name": "f7"}),
        "autoclick": bindings.make_binding([], {"kind": "named",
                                                "name": "f8"}),
    }


def shared_trigger_map():
    """Plain F9 and Control+F9 both bound, to prove they stay distinct."""
    return {
        "record": bindings.make_binding(["ctrl"],
                                        {"kind": "named", "name": "f9"}),
        "play": bindings.make_binding([], {"kind": "named", "name": "f9"}),
        "autoclick": bindings.make_binding([], {"kind": "named",
                                                "name": "f8"}),
    }


def new_tool(mapping):
    return macro.MacroTool("all", verbose=False, binding_map=mapping,
                           config_path=TMP_CONFIG)


def spy_tool(mapping):
    """A tool whose actions are recorded instead of performed."""
    tool = new_tool(mapping)
    fired = []
    tool.handle_hotkey = lambda action: fired.append(action)
    return tool, fired


def summary(events):
    """A readable shape for assertions: (pressed, key name) in file order."""
    out = []
    for e in events:
        if e["type"] != "key":
            out.append((e["type"], None))
            continue
        name = e.get("name") or e.get("char") or ("vk%s" % e.get("vk"))
        out.append(("down" if e["pressed"] else "up", name))
    return out


def times_nondecreasing(events):
    ts = [e["t"] for e in events]
    return all(a <= b for a, b in zip(ts, ts[1:])), ts


# ---------------------------------------------------------------------------

def test_nonmatch_reinsertion_in_time_order():
    """
    THE nonmatch path. A held-back Control, an ordinary click that happens
    while the resolver is waiting, then a key that proves no chord is coming.
    The Control must land BEFORE the click, not appended after it.
    """
    print("\n1. nonmatch reinsertion lands in TIME ORDER, before the "
          "intervening event")
    tool = new_tool(chord_map())
    tool.start_record("chord_nonmatch")
    gap()
    tool.on_key_press(CTRL_L)
    check("Control held back, not written into the recording",
          len(tool.events) == 0, "events=%s" % summary(tool.events))
    gap()
    tool.on_click(400, 300, mouse.Button.left, True)
    check("the intervening click IS recorded while we wait",
          summary(tool.events) == [("click", None)],
          "events=%s" % summary(tool.events))
    gap()
    tool.on_key_press(C_KEY)         # no chord is coming
    got = summary(tool.events)
    check("Control reinserted BEFORE the click, in time order",
          got == [("down", CL), ("click", None), ("down", "c")],
          "events=%s" % got)
    ordered, ts = times_nondecreasing(tool.events)
    check("recorded timestamps stay non-decreasing", ordered, "t=%s" % ts)
    tool.on_key_release(C_KEY)
    tool.on_key_release(CTRL_L)
    got = summary(tool.events)
    check("Control release recorded too, so the pair is intact",
          got[-1] == ("up", CL) and ("up", "c") in got,
          "events=%s" % got)
    tool.stop_record()


def test_control_c_roundtrip():
    print("\n2. an ordinary Control+C the user records keeps its Control")
    tool = new_tool(chord_map())
    tool.start_record("chord_ctrl_c")
    gap()
    tool.on_key_press(CTRL_L)
    gap()
    tool.on_key_press(C_KEY)
    gap()
    tool.on_key_release(C_KEY)
    gap()
    tool.on_key_release(CTRL_L)
    got = summary(tool.events)
    check("Control+C round-trips as four events in order",
          got == [("down", CL), ("down", "c"),
                  ("up", "c"), ("up", CL)],
          "events=%s" % got)
    ordered, _ = times_nondecreasing(tool.events)
    check("timestamps still non-decreasing", ordered)
    tool.stop_record()


def test_modifier_tap_alone():
    print("\n3. a modifier tapped on its own is still recorded")
    tool = new_tool(chord_map())
    tool.start_record("chord_tap")
    gap()
    tool.on_key_press(CTRL_L)
    gap()
    tool.on_key_release(CTRL_L)
    got = summary(tool.events)
    check("a lone Control tap round-trips",
          got == [("down", CL), ("up", CL)], "events=%s" % got)
    tool.stop_record()


def test_near_miss_released():
    print("\n4. a near-match released without activation loses nothing")
    tool, fired = spy_tool(chord_map())
    tool.state = "recording"
    tool.events = []
    tool.record_start = time.perf_counter()
    gap()
    tool.on_key_press(CTRL_L)          # Control+F9 is a binding, so held back
    gap()
    tool.on_key_release(CTRL_L)        # released without ever pressing F9
    got = summary(tool.events)
    check("no action fired", fired == [], "fired=%s" % fired)
    check("both Control events survive",
          got == [("down", CL), ("up", CL)], "events=%s" % got)


def test_overlapping_left_and_right():
    print("\n5. overlapping left and right Control keep every event")
    tool = new_tool(chord_map())
    tool.start_record("chord_overlap")
    gap()
    tool.on_key_press(CTRL_L)
    gap()
    tool.on_key_press(CTRL_R)
    gap()
    tool.on_key_release(CTRL_L)
    gap()
    tool.on_key_release(CTRL_R)
    got = summary(tool.events)
    check("all four events recorded in order",
          got == [("down", CL), ("down", CR),
                  ("up", CL), ("up", CR)], "events=%s" % got)
    ordered, _ = times_nondecreasing(tool.events)
    check("timestamps still non-decreasing", ordered)
    tool.stop_record()


def test_chord_hotkey_leaves_no_trace():
    print("\n6. the chord hotkey itself stores none of its own events")
    tool = new_tool(chord_map())
    # the window would have pushed a name in; do the same through the real
    # context API rather than reaching into the engine's fields
    tool.set_context(name="chord_trace")
    # start via the chord
    tool.on_key_press(CTRL_L)
    tool.on_key_press(F9)
    check("recording started by Control+F9", tool.state == "recording",
          "state=%s" % tool.state)
    tool.on_key_release(F9)
    tool.on_key_release(CTRL_L)
    check("no modifier still held after the chord",
          tool.held_modifiers() == frozenset(),
          "held=%s" % set(tool.held_modifiers()))
    check("nothing from the chord landed in the recording",
          tool.events == [], "events=%s" % summary(tool.events))
    gap()
    tool.on_key_press(C_KEY)          # the user's actual payload
    gap()
    tool.on_key_release(C_KEY)
    gap()
    # stop via the chord again
    tool.on_key_press(CTRL_L)
    tool.on_key_press(F9)
    got = summary(tool.events)
    check("only the user's own keystroke was stored",
          got == [("down", "c"), ("up", "c")], "events=%s" % got)
    check("recording stopped by Control+F9", tool.state == "idle",
          "state=%s" % tool.state)
    tool.on_key_release(F9)
    tool.on_key_release(CTRL_L)
    check("still no modifier held after the stop chord",
          tool.held_modifiers() == frozenset(),
          "held=%s" % set(tool.held_modifiers()))


def test_shared_trigger_exact_match():
    print("\n7. plain F9 and Control+F9 are different bindings")
    tool, fired = spy_tool(shared_trigger_map())
    tool.on_key_press(F9)
    tool.on_key_release(F9)
    check("plain F9 fired only play", fired == ["play"], "fired=%s" % fired)
    fired.clear()
    tool.on_key_press(CTRL_L)
    tool.on_key_press(F9)
    tool.on_key_release(F9)
    tool.on_key_release(CTRL_L)
    check("Control+F9 fired only record", fired == ["record"],
          "fired=%s" % fired)
    fired.clear()
    tool.on_key_press(SHIFT_L)
    tool.on_key_press(CTRL_L)
    tool.on_key_press(F9)
    check("Shift+Control+F9 matches nothing, an extra modifier means no match",
          fired == [], "fired=%s" % fired)
    tool.on_key_release(F9)
    tool.on_key_release(CTRL_L)
    tool.on_key_release(SHIFT_L)


def test_autorepeat_debounce():
    print("\n8. one press cycle requests exactly one action")
    tool, fired = spy_tool(chord_map())
    tool.on_key_press(CTRL_L)
    for _ in range(5):
        tool.on_key_press(F9)          # auto repeat while still held
    check("five repeats requested one action", fired == ["record"],
          "fired=%s" % fired)
    tool.on_key_release(F9)
    tool.on_key_release(CTRL_L)
    fired.clear()
    tool.on_key_press(CTRL_L)
    tool.on_key_press(F9)
    check("a fresh press cycle arms again", fired == ["record"],
          "fired=%s" % fired)
    tool.on_key_release(F9)
    tool.on_key_release(CTRL_L)


def test_unconfigured_shortcut_never_fires():
    print("\n9. an unconfigured shortcut never triggers the tool")
    tool, fired = spy_tool(chord_map())
    tool.on_key_press(CTRL_L)
    tool.on_key_press(C_KEY)
    tool.on_key_release(C_KEY)
    tool.on_key_release(CTRL_L)
    check("Control+C fired nothing", fired == [], "fired=%s" % fired)


def test_release_of_unmatched_trigger_is_recorded():
    """
    The regression that made this rewrite necessary: the old code suppressed
    the RELEASE of any key that was a trigger anywhere, even when its press had
    not matched. With Control+F9 bound, a bare F9 the user records used to lose
    its release and replay as a stuck key.
    """
    print("\n10. a bare F9 press that matched nothing keeps its release")
    tool = new_tool(chord_map())
    tool.start_record("chord_bare_trigger")
    gap()
    tool.on_key_press(F9)              # no Control, so no match
    gap()
    tool.on_key_release(F9)
    got = summary(tool.events)
    check("bare F9 round-trips as press and release",
          got == [("down", "f9"), ("up", "f9")], "events=%s" % got)
    tool.stop_record()


def main():
    print("CHORD RESOLVER, both paths")
    print("  recordings dir : %s (injected)" % TMP_RECORDINGS)
    print("  record binding : Control + F9")
    for fn in (test_nonmatch_reinsertion_in_time_order,
               test_control_c_roundtrip,
               test_modifier_tap_alone,
               test_near_miss_released,
               test_overlapping_left_and_right,
               test_chord_hotkey_leaves_no_trace,
               test_shared_trigger_exact_match,
               test_autorepeat_debounce,
               test_unconfigured_shortcut_never_fires,
               test_release_of_unmatched_trigger_is_recorded):
        try:
            fn()
        except Exception as exc:
            check("%s raised" % fn.__name__, False, repr(exc))
    print()
    print("=" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        if not ok:
            print("FAILED: %s %s" % (name, detail))
    print("CHORD RESOLVER: %d of %d assertions passed" % (passed, len(results)))
    verdict = passed == len(results)
    print("VERDICT: %s" % ("PASS" if verdict else "FAIL"))
    print("=" * 78)
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
