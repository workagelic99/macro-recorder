#!/usr/bin/env python3
"""
Test Input mode: the three outcomes, and the stale evidence it must refuse.

A passive "listener is running" light is worthless here, and this project has
the scar to prove it: every shipped proof was green while the owner's hotkeys
did nothing, because listener.running was True the whole time. The listener WAS
running. It just never received the keys his keyboard actually emits.

So Test Input is a PROMPT, not an indicator. Opening one snapshots the callback
sequence number and the clock, clears the readout, and then counts only events
that arrive AFTER that snapshot, inside that prompt's own window. It reports
exactly one of three things:

  a key was seen and hotkeys can use it
  a media or brightness key was seen but this app cannot read it
  nothing was seen at all

The middle outcome is the one that matters, because it is the honest answer to
what this Mac does with a bare F7: the key is being pressed, the event exists,
and the layer the tool binds on never receives it. An indicator that only knew
"running" would report health while the user got nothing.

The positive raw branch is proved by POSTING the exact event the measurement
recorded from Gelo's own keyboard, an NSSystemDefined subtype 8 carrying
decoded key type 20 REWIND, which the installed pynput cannot deliver.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TMP_ROOT = Path(tempfile.mkdtemp(prefix="macro_testinput_"))
TMP_RECORDINGS = TMP_ROOT / "recordings"
TMP_RECORDINGS.mkdir(parents=True)
TMP_CONFIG = TMP_ROOT / "config.json"

import macro  # noqa: E402
macro.RECORDINGS_DIR = TMP_RECORDINGS

import gui  # noqa: E402
import mainthread  # noqa: E402
import rawtap  # noqa: E402
from AppKit import (NSApplication,  # noqa: E402
                    NSApplicationActivationPolicyRegular)
from Foundation import NSTimer  # noqa: E402
from pynput import keyboard  # noqa: E402

REWIND = 20            # measured from the physical F7 on this Mac
results = []
state = {}


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("   %-58s %s%s" % (name, "PASS" if ok else "FAIL",
                             ("  " + detail) if detail else ""))
    sys.stdout.flush()


def tap_key(name):
    kbd = keyboard.Controller()
    key = getattr(keyboard.Key, name)
    kbd.press(key)
    time.sleep(0.05)
    kbd.release(key)


def outcome(win):
    return str(win.test_outcome.stringValue())


def build_steps(g_app, win, tool):
    s = []
    # how many idle steps to burn to exceed the observation window
    idle = int(gui.TEST_INPUT_SECONDS / 0.9) + 3

    # ---------- 1. opening a prompt snapshots and clears
    def open_first():
        state["seq_before"] = tool.input_seq()
        win.btn_test.performClick_(None)
        check("Test Input opened through its real button",
              win.test_window is not None and win.test_window.isVisible())
        check("the prompt snapshotted the CURRENT callback sequence",
              win.test_seq == state["seq_before"],
              "snapshot=%s seq=%s" % (win.test_seq, state["seq_before"]))
        check("the prompt cleared its readout on open",
              outcome(win).lower().startswith("waiting"),
              "outcome=%r" % outcome(win))
        health = str(win.test_health.stringValue())
        state["health"] = health
        check("the health surface names the app the grants belong to",
              "Permissions are granted to:" in health)
        check("the health surface reports all three permissions",
              all(x in health for x in ("Accessibility", "Input Monitoring",
                                        "Screen Recording")))
        check("the health surface reports the media key watcher",
              "Media key watcher" in health)
    s.append(open_first)

    # ---------- 2. OUTCOME ONE: an ordinary key
    s.append(lambda: tap_key("f19"))

    def ordinary():
        text = outcome(win)
        check("OUTCOME 1, an ordinary key is reported as seen and usable",
              text.startswith("Key seen"), "outcome=%r" % text)
        check("the ordinary outcome names the key",
              "f19" in text.lower(), "outcome=%r" % text)
        state["decided_text"] = text
    s.append(ordinary)

    def sticky():
        check("a decided prompt does not keep rewriting itself",
              outcome(win) == state["decided_text"],
              "outcome=%r" % outcome(win))
    s.append(lambda: tap_key("f18"))
    s.append(sticky)

    # ---------- 3. OUTCOME TWO: the raw key pynput cannot see
    def reopen_for_raw():
        state["seq_before_raw"] = tool.input_seq()
        win.onTestAgain_(None)
        check("a second prompt re-snapshotted and cleared",
              win.test_seq == state["seq_before_raw"]
              and outcome(win).lower().startswith("waiting"),
              "snapshot=%s outcome=%r" % (win.test_seq, outcome(win)))
    s.append(reopen_for_raw)

    def post_raw():
        check("the raw key watcher is available in this process",
              tool.raw_tap_status()[0], "reason=%r" % tool.raw_tap_status()[1])
        check("pynput genuinely cannot map REWIND, so this is a real gap",
              not rawtap.is_mapped_by_pynput(REWIND))
        ok_down = rawtap.post_system_event(REWIND, True)
        time.sleep(0.05)
        ok_up = rawtap.post_system_event(REWIND, False)
        check("posted the measured NSSystemDefined REWIND event",
              ok_down and ok_up)
    s.append(post_raw)

    def raw_outcome():
        text = outcome(win)
        check("OUTCOME 2, the raw key is reported as SEEN BUT UNUSABLE",
              text.startswith("Seen, but unusable"), "outcome=%r" % text)
        check("outcome 2 is NOT reported as an ordinary key",
              not text.startswith("Key seen"), "outcome=%r" % text)
        check("outcome 2 names the media key it saw",
              "rewind" in text.lower(), "outcome=%r" % text)
        last = tool.last_input()
        check("the engine classified it as raw_unmapped",
              last and last["kind"] == "raw_unmapped", "last=%s" % last)
    s.append(raw_outcome)

    # ---------- 4. OUTCOME THREE: nothing at all, right after a real event
    #
    # The media key watcher is stopped FIRST, and for a measured reason. On a
    # first run of this proof an ambient BRIGHTNESS_UP arrived inside the empty
    # window and the prompt reported it. That was the mode working, not
    # failing: this Mac really does emit brightness key events on its own, the
    # same ambient traffic the physical measurement session recorded. But a
    # window that cannot be kept empty cannot prove the empty outcome, so the
    # raw layer is stopped here and the keyboard layer is left running with
    # nothing injected into it.
    def stop_raw_for_quiet():
        if tool.raw_tap is not None:
            tool.raw_tap.stop()
        check("media key watcher stopped so the empty window is really empty",
              not tool.raw_tap_status()[0])

    s.append(stop_raw_for_quiet)

    def reopen_empty():
        state["seq_before_empty"] = tool.input_seq()
        win.onTestAgain_(None)
        check("a later EMPTY prompt cleared the previous outcome",
              outcome(win).lower().startswith("waiting"),
              "outcome=%r" % outcome(win))
    s.append(reopen_empty)
    s.extend([("idle", lambda: None)[1] for _ in range(idle)])

    def empty_outcome():
        text = outcome(win)
        check("OUTCOME 3, an empty prompt reports nothing seen",
              text.startswith("Nothing seen"), "outcome=%r" % text)
        check("the empty prompt was NOT satisfied by the earlier real events",
              "rewind" not in text.lower() and "f19" not in text.lower(),
              "outcome=%r" % text)
    s.append(empty_outcome)

    # ---------- 5. THE STALE EVIDENCE REGRESSION
    # Seed a real event, then FAULT the listener, then open a fresh prompt and
    # inject nothing. A prompt that reported that seeded event would be exactly
    # the false health signal this whole mode exists to remove.
    def seed_then_fault():
        tap_key("f19")
        time.sleep(0.4)
        state["seeded_seq"] = tool.input_seq()
        check("an event was seeded before the listener was stopped",
              state["seeded_seq"] > state["seq_before_empty"],
              "seq=%s" % state["seeded_seq"])
        tool.stop_listeners()
        time.sleep(0.3)
        check("the listener is now genuinely stopped",
              not tool.kbd_listener.running,
              "running=%s" % tool.kbd_listener.running)
    s.append(seed_then_fault)

    def open_after_fault():
        win.onTestAgain_(None)
        check("the post-fault prompt snapshotted PAST the seeded event",
              win.test_seq >= state["seeded_seq"],
              "snapshot=%s seeded=%s" % (win.test_seq, state["seeded_seq"]))
        state["health_after"] = str(win.test_health.stringValue())
        row = [r for r in state["health_after"].splitlines()
               if "Media key watcher" in r]
        check("the health surface now reports the media key watcher as OFF",
              bool(row) and "OFF" in row[0], "row=%r" % (row[0] if row else None))
    s.append(open_after_fault)

    def try_inject_into_dead_listener():
        tap_key("f19")          # goes to the OS, but nothing is listening now
    s.append(try_inject_into_dead_listener)
    s.extend([("idle", lambda: None)[1] for _ in range(idle)])

    def stale_outcome():
        text = outcome(win)
        check("STALE EVIDENCE REFUSED: a dead listener reports nothing seen",
              text.startswith("Nothing seen"), "outcome=%r" % text)
    s.append(stale_outcome)
    return s


def main():
    print("TEST INPUT MODE: three outcomes plus the stale evidence regression")
    print("  recordings dir : %s (injected)" % TMP_RECORDINGS)
    print("  observation win: %.1fs" % gui.TEST_INPUT_SECONDS)

    tool = macro.MacroTool("all", verbose=False, config_path=TMP_CONFIG)
    tool.start_listeners()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    tool.start_raw_tap()
    win = gui.MacroWindow.alloc().initWithTool_(tool)

    steps = build_steps(app, win, tool)

    def finish():
        ok, line = driver.thread_verdict()
        check("every step ran on the main thread", ok, line)
        print()
        print("HEALTH SURFACE AS RENDERED IN THE PROMPT:")
        for row in (state.get("health") or "").splitlines():
            print("   | %s" % row)
        print()
        print("=" * 78)
        passed = sum(1 for _, o, _ in results if o)
        for name, o, detail in results:
            if not o:
                print("FAILED: %s %s" % (name, detail))
        print("TEST INPUT: %d of %d assertions passed" % (passed, len(results)))
        print("VERDICT: %s" % ("PASS" if passed == len(results) else "FAIL"))
        print("=" * 78)
        sys.stdout.flush()

    driver = mainthread.Steps(app, [("step", fn) for fn in steps],
                              on_finish=finish)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        gui.POLL_SECONDS, win, "tick:", None, True)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.9, driver, "tick:", None, True)
    app.run()
    return 0 if results and all(o for _, o, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
