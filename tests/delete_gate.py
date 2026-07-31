#!/usr/bin/env python3
"""
THE DELETE GATE. Deleting is the one action in this tool that destroys
something, so it gets the strictest proof in the project.

Same shape as the editor gate: every press goes through the REAL Cocoa button,
on the main thread, in a fresh process. Nothing calls the handler directly,
because a proof that bypasses the button cannot tell you the button is wired to
the right recording.

What has to be true, and each one is checked against the DISK, not against what
the window says happened:

  arming deletes nothing. The first press only asks.
  Cancel deletes nothing.
  confirming deletes EXACTLY the named file and no other. Every other
  recording is checksummed before and after.
  no selection refuses, with an explanation on the status line.
  a busy tool refuses, with an explanation, even when a row IS selected.
  the list refreshes, so the deleted name is gone from the table.
  an armed Delete disarms itself if the selection moves, so the second press
  can never land on a recording the user did not aim at.

Isolation: an injected temporary recordings directory and config path. Your own
recordings are never in reach, and the test asserts the real directory is
untouched before it finishes.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TMP_ROOT = Path(tempfile.mkdtemp(prefix="macro_delete_"))
TMP_RECORDINGS = TMP_ROOT / "recordings"
TMP_RECORDINGS.mkdir(parents=True)
TMP_CONFIG = TMP_ROOT / "config.json"

import macro  # noqa: E402
macro.RECORDINGS_DIR = TMP_RECORDINGS

import bindings  # noqa: E402
import gui  # noqa: E402
import mainthread  # noqa: E402
from AppKit import (NSApplication,  # noqa: E402
                    NSApplicationActivationPolicyRegular)
from Foundation import NSIndexSet, NSTimer  # noqa: E402
from pynput import keyboard  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_RECORDINGS = ROOT / "recordings"

KEEP_A = "keep_me_one"
KEEP_B = "keep_me_two"
DOOMED = "delete_me"
LONG_PLAY = "long_fixture"

results = []
state = {}


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("   %-60s %s%s" % (name, "PASS" if ok else "FAIL",
                             ("  " + detail) if detail else ""))
    sys.stdout.flush()


def plant(name, seconds=1.0):
    events, t = [], 0.0
    while t < seconds:
        events.append({"type": "key", "kind": "special", "name": "f15",
                       "pressed": True, "t": round(t, 6)})
        events.append({"type": "key", "kind": "special", "name": "f15",
                       "pressed": False, "t": round(t + 0.02, 6)})
        t += 0.4
    (TMP_RECORDINGS / (name + ".json")).write_text(
        json.dumps({"version": macro.FORMAT_VERSION, "name": name,
                    "created": "planted-by-delete-gate",
                    "screen": macro.screen_size(),
                    "capture_moves": False, "events": events}, indent=2),
        encoding="utf-8")


def digests():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(TMP_RECORDINGS.glob("*.json"))}


def exists(name):
    return (TMP_RECORDINGS / (name + ".json")).exists()


def inject_async(binding):
    def go():
        kbd = keyboard.Controller()
        mods = [{"ctrl": keyboard.Key.ctrl, "alt": keyboard.Key.alt,
                 "shift": keyboard.Key.shift,
                 "cmd": keyboard.Key.cmd}[m] for m in binding["modifiers"]]
        for m in mods:
            kbd.press(m)
            time.sleep(0.02)
        key = getattr(keyboard.Key, binding["trigger"]["name"])
        kbd.press(key)
        time.sleep(0.05)
        kbd.release(key)
        for m in reversed(mods):
            time.sleep(0.02)
            kbd.release(m)
    threading.Thread(target=go, daemon=True).start()


def build_steps(win, tool):
    """
    Every step that PRESSES something is followed by a separate step that
    checks the result.

    That separation is not tidiness, it is required. The window renders the
    status line and pushes the field values into the engine on its own timer,
    every 0.15s. A check written in the same step as the press reads the
    PREVIOUS tick's text and the engine acts on the PREVIOUS tick's context, so
    the first version of this gate had it both ways: status assertions that
    were one step stale, and a playback that started on the recording selected
    before the one under test. The driver forces a real tick before each step,
    so what the next step reads is what a user would actually see.
    """
    s = []

    def select(name):
        names = [n for n, _ in win.names]
        if name not in names:
            win.refresh_list()
            names = [n for n, _ in win.names]
        if name in names:
            win.table.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(names.index(name)), False)
            return True
        return False

    def status():
        return str(win.status.stringValue())

    def wait_state(want, seconds=4.0):
        """
        Wait for the ENGINE to reach a state, ticking the window as we go.

        A synthesized keypress goes out through the OS and comes back on the
        listener thread, and how long that round trip takes is not ours to
        decide. Asserting the transition exactly one step after the injection
        made this gate report a lost keypress that had simply not landed yet.
        The claim being tested is that the press causes the transition, not
        that it causes it inside 0.9 seconds.
        """
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if tool.state == want:
                break
            time.sleep(0.05)
        win.tick_(None)
        return tool.state == want

    # ---------- 0. the Delete control exists and is usable
    def present():
        state["all_before"] = digests()
        check("four recordings planted", len(state["all_before"]) == 4,
              "files=%s" % sorted(state["all_before"]))
        check("Delete button is visible and enabled",
              not win.btn_delete.isHidden() and win.btn_delete.isEnabled())
        check("Delete starts disarmed, Cancel starts disabled",
              str(win.btn_delete.title()) == "Delete"
              and not win.btn_delete_cancel.isEnabled(),
              "title=%r" % str(win.btn_delete.title()))
    s.append(present)

    # ---------- 1. NO SELECTION refuses
    def clear_selection():
        win.table.deselectAll_(None)
        check("selection cleared for the no-selection case",
              win.selected_name() is None,
              "selected=%r" % win.selected_name())
        win.btn_delete.performClick_(None)
    s.append(clear_selection)

    def no_selection_result():
        check("no selection: Delete stayed disarmed",
              win.pending_delete is None
              and str(win.btn_delete.title()) == "Delete")
        check("no selection: the status line explains why",
              "pick a recording" in status().lower(), "status=%r" % status())
        check("no selection: nothing was deleted",
              digests() == state["all_before"])
    s.append(no_selection_result)

    # ---------- 2. ARMING deletes nothing
    def arm():
        check("selected the doomed recording", select(DOOMED))
        win.btn_delete.performClick_(None)
    s.append(arm)

    def armed_result():
        check("first press ARMED delete on the selected row",
              win.pending_delete == DOOMED,
              "pending=%r" % win.pending_delete)
        check("the armed button NAMES the recording",
              DOOMED in str(win.btn_delete.title()),
              "title=%r" % str(win.btn_delete.title()))
        check("the status line names the recording and warns",
              DOOMED in status() and "cannot be undone" in status().lower(),
              "status=%r" % status())
        check("Cancel became available", win.btn_delete_cancel.isEnabled())
        check("ARMING DELETED NOTHING", digests() == state["all_before"])
    s.append(armed_result)

    # ---------- 3. CANCEL deletes nothing
    s.append(lambda: win.btn_delete_cancel.performClick_(None))

    def cancel_result():
        check("Cancel disarmed delete",
              win.pending_delete is None
              and str(win.btn_delete.title()) == "Delete")
        check("Cancel disabled itself again",
              not win.btn_delete_cancel.isEnabled())
        check("Cancel said the recording was kept",
              DOOMED in status() and "kept" in status().lower(),
              "status=%r" % status())
        check("CANCEL DELETED NOTHING", digests() == state["all_before"])
        check("the doomed recording is still on disk", exists(DOOMED))
    s.append(cancel_result)

    # ---------- 4. armed, then the SELECTION MOVES
    def arm_then_move():
        select(DOOMED)
        win.btn_delete.performClick_(None)
        check("re-armed on the doomed recording",
              win.pending_delete == DOOMED)
        select(KEEP_A)
    s.append(arm_then_move)

    def moved_disarms():
        check("moving the selection DISARMED the pending delete",
              win.pending_delete is None,
              "pending=%r" % win.pending_delete)
        check("the button went back to its resting label",
              str(win.btn_delete.title()) == "Delete")
        check("moving the selection deleted nothing",
              digests() == state["all_before"])
    s.append(moved_disarms)

    # ---------- 5. BUSY refuses, even with a row selected
    def choose_long_fixture():
        check("selected the long fixture for playback", select(LONG_PLAY))
        win.forever.setState_(1)
        win.sync_loop_field()
    s.append(choose_long_fixture)

    # a tick happens here, so the engine has the selection AND Loop forever
    s.append(lambda: inject_async(tool.binding_map()["play"]))

    def press_while_busy():
        check("playback is running, so the tool is not idle",
              wait_state("playing"), "state=%s" % tool.state)
        check("playback is on the LONG fixture, so it cannot end by itself",
              tool.name == LONG_PLAY and tool.loops == 0,
              "name=%r loops=%r" % (tool.name, tool.loops))
        check("a row IS selected, so only busy can be the reason to refuse",
              win.selected_name() == LONG_PLAY,
              "selected=%r" % win.selected_name())
        check("busy: the Delete button stays ENABLED so it can explain itself",
              win.btn_delete.isEnabled())
        win.btn_delete.performClick_(None)
    s.append(press_while_busy)

    def busy_result():
        check("busy: Delete stayed disarmed", win.pending_delete is None)
        check("busy: the status line explains why",
              "busy" in status().lower() and "stop" in status().lower(),
              "status=%r" % status())
        check("busy: nothing was deleted", digests() == state["all_before"])
    s.append(busy_result)

    s.append(lambda: inject_async(tool.binding_map()["play"]))

    def stopped():
        check("playback stopped, tool is idle again", wait_state("idle"),
              "state=%s" % tool.state)
        check("Delete is usable again once idle", win.btn_delete.isEnabled())
    s.append(stopped)

    # ---------- 6. CONFIRM deletes exactly one file
    def arm_for_real():
        check("selected the doomed recording again", select(DOOMED))
        state["rows_before"] = win.table.numberOfRows()
        win.btn_delete.performClick_(None)
        check("armed for the real deletion", win.pending_delete == DOOMED,
              "pending=%r" % win.pending_delete)
    s.append(arm_for_real)

    s.append(lambda: win.btn_delete.performClick_(None))

    def confirm_result():
        after = digests()
        check("the doomed recording is GONE from disk", not exists(DOOMED),
              "still there" if exists(DOOMED) else "")
        expected = {k: v for k, v in state["all_before"].items()
                    if k != DOOMED + ".json"}
        check("EXACTLY ONE file went, and every other is byte identical",
              after == expected,
              "after=%s expected=%s" % (sorted(after), sorted(expected)))
        check("the other recordings' checksums are unchanged",
              all(after.get(k) == v for k, v in expected.items()))
        check("the list refreshed and the name is gone from the table",
              DOOMED not in [n for n, _ in win.names]
              and win.table.numberOfRows() == state["rows_before"] - 1,
              "rows=%s names=%s" % (win.table.numberOfRows(),
                                    [n for n, _ in win.names]))
        check("the status line confirms what was deleted",
              DOOMED in status() and "deleted" in status().lower(),
              "status=%r" % status())
        check("Delete disarmed itself after the deletion",
              win.pending_delete is None
              and str(win.btn_delete.title()) == "Delete"
              and not win.btn_delete_cancel.isEnabled())
    s.append(confirm_result)

    # ---------- 7. a file that vanishes underneath fails softly
    def arm_then_vanish():
        select(KEEP_A)
        win.btn_delete.performClick_(None)
        check("armed on a recording that is about to vanish",
              win.pending_delete == KEEP_A,
              "pending=%r" % win.pending_delete)
        (TMP_RECORDINGS / (KEEP_A + ".json")).unlink()
    s.append(arm_then_vanish)

    s.append(lambda: win.btn_delete.performClick_(None))

    def vanished_result():
        check("a file that vanished underneath is reported, not a crash",
              "could not delete" in status().lower()
              or "deleted" in status().lower(), "status=%r" % status())
        check("the window is still alive after the failure",
              win.window.isVisible())
        check("the remaining recordings are intact",
              exists(KEEP_B) and exists(LONG_PLAY))
    s.append(vanished_result)

    # ---------- 8. the Stop and Save label
    def stop_label_idle():
        check("Stop reads plain 'Stop' while idle",
              str(win.btn_stop.title()) == "Stop",
              "title=%r" % str(win.btn_stop.title()))
        win.name_field.setStringValue_("delete_gate_take")
    s.append(stop_label_idle)

    s.append(lambda: inject_async(tool.binding_map()["record"]))

    def stop_label_recording():
        check("recording started through the listener",
              wait_state("recording"), "state=%s" % tool.state)
        check("Stop promises the save ONLY while recording",
              str(win.btn_stop.title()) == "Stop & Save",
              "title=%r" % str(win.btn_stop.title()))
    s.append(stop_label_recording)

    s.append(lambda: inject_async(tool.binding_map()["record"]))

    def stop_label_back():
        check("Stop drops the promise once idle again",
              wait_state("idle") and str(win.btn_stop.title()) == "Stop",
              "state=%s title=%r" % (tool.state, str(win.btn_stop.title())))
        check("and the recording it saved really is on disk",
              exists("delete_gate_take"))
    s.append(stop_label_back)

    return s


def main():
    print("DELETE GATE: every press through the real button, checked on disk")
    print("  recordings dir : %s (injected)" % TMP_RECORDINGS)
    real_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(REAL_RECORDINGS.glob("*.json"))}

    for name in (KEEP_A, KEEP_B, DOOMED):
        plant(name)
    plant(LONG_PLAY, seconds=60.0)

    tool = macro.MacroTool("all", verbose=False, config_path=TMP_CONFIG)
    tool.start_listeners()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    tool.start_raw_tap()
    win = gui.MacroWindow.alloc().initWithTool_(tool)
    print("  bindings       : %s"
          % {a: bindings.format_binding(b)
             for a, b in tool.binding_map().items()})

    steps = build_steps(win, tool)

    def finish():
        ok, line = driver.thread_verdict()
        check("every step ran on the main thread", ok, line)
        real_after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(REAL_RECORDINGS.glob("*.json"))}
        check("your REAL recordings directory was never touched",
              real_after == real_before,
              "before=%s after=%s" % (sorted(real_before), sorted(real_after)))
        print()
        print("=" * 78)
        passed = sum(1 for _, o, _ in results if o)
        for nm, o, detail in results:
            if not o:
                print("FAILED: %s %s" % (nm, detail))
        print("DELETE GATE: %d of %d assertions passed" % (passed,
                                                           len(results)))
        print("VERDICT: %s" % ("PASS" if passed == len(results) else "FAIL"))
        print("=" * 78)
        sys.stdout.flush()

    def with_tick(fn):
        # A real tick, on the main thread, through the window's own timer
        # method: renders the status line and pushes the field values into the
        # engine, exactly as it does in normal use.
        def go():
            win.tick_(None)
            fn()
        return go

    driver = mainthread.Steps(app, [("step", with_tick(fn)) for fn in steps],
                              on_finish=finish)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        gui.POLL_SECONDS, win, "tick:", None, True)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.9, driver, "tick:", None, True)
    app.run()
    tool.shutdown()
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    return 0 if results and all(o for _, o, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
