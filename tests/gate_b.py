#!/usr/bin/env python3
"""
GATE B: the physical gate. Gelo's own fingers, on his own keyboard.

Everything else in this project proves the tool works when a key event is
SYNTHESIZED. That is not the same claim. A synthesized keyDown already carries
the F-key code, which bypasses the top row translation entirely, and that one
gap is why every proof was green while every hotkey was dead. Only a real
physical press exercises the layer that failed.

  ./.venv/bin/python tests/gate_b.py              the real gate, with fingers
  ./.venv/bin/python tests/gate_b.py --synthetic  self-check of THIS harness

--synthetic proves the harness, its prompts, its watchdogs, its isolation and
its restore path. It proves NOTHING about the keyboard, which is the entire
point of the real run, and it says so in its own verdict.

HARMLESS BY CONSTRUCTION, all of it set up before a single key is asked for:

  a throwaway recordings directory and a throwaway config file, so the real
  ones are never opened. The real recordings directory is hashed before and
  after and the hashes are printed either way, including after a timeout.

  the playback fixture is keys only, F13 to F15, which macOS binds to nothing.
  No clicks, so nothing can be pressed anywhere by accident.

  the autoclicker runs against the borderless window from safe_target.py with
  the cursor parked inside it, so its clicks land on a window this harness owns
  and nowhere else.

  every prompt has a watchdog. A prompt nobody answers is recorded INCOMPLETE
  and the run moves on. A guaranteed stop and listener cleanup runs at the end
  whatever happens, and the watchdog is never the passing path for a stop.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SYNTHETIC = "--synthetic" in sys.argv
PROMPT_SECONDS = 6.0 if SYNTHETIC else 25.0

TMP_ROOT = Path(tempfile.mkdtemp(prefix="macro_gate_b_"))
TMP_RECORDINGS = TMP_ROOT / "recordings"
TMP_RECORDINGS.mkdir(parents=True)
TMP_CONFIG = TMP_ROOT / "config.json"

import macro  # noqa: E402
macro.RECORDINGS_DIR = TMP_RECORDINGS

import bindings  # noqa: E402
import gui  # noqa: E402
import health  # noqa: E402
import mainthread  # noqa: E402
import rawtap  # noqa: E402
from AppKit import (NSApplication,  # noqa: E402
                    NSApplicationActivationPolicyRegular)
from Foundation import NSIndexSet, NSTimer  # noqa: E402
from pynput import keyboard, mouse  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_RECORDINGS = ROOT / "recordings"
REAL_CONFIG = Path(os.path.expanduser("~/.macro-recorder.json"))
SAFE_TARGET = Path(__file__).resolve().parent / "safe_target.py"
VENV_PY = ROOT / ".venv" / "bin" / "python"

FIXTURE = "gate_b_harmless"
REWIND = 20                       # measured: what a bare F7 sends on this Mac

results = []
notes = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    mark = {True: "PASS", False: "FAIL", None: "INCOMPLETE"}[ok]
    print("      %-46s %s%s" % (name, mark, ("  " + detail) if detail else ""))
    sys.stdout.flush()


def say(text=""):
    print(text, flush=True)


def hash_dir(path):
    out = {}
    if not path.exists():
        return out
    for f in sorted(path.glob("*.json")):
        out[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def plant_fixture():
    """Keys only, long enough that it cannot finish inside the interaction."""
    events, t = [], 0.0
    while t < 120.0:
        for name in ("f13", "f14", "f15"):
            events.append({"type": "key", "kind": "special", "name": name,
                           "pressed": True, "t": round(t, 6)})
            events.append({"type": "key", "kind": "special", "name": name,
                           "pressed": False, "t": round(t + 0.02, 6)})
            t += 0.5
    path = TMP_RECORDINGS / (FIXTURE + ".json")
    path.write_text(json.dumps({"version": macro.FORMAT_VERSION,
                                "name": FIXTURE, "created": "gate-b-fixture",
                                "screen": macro.screen_size(),
                                "capture_moves": False, "events": events},
                               indent=2), encoding="utf-8")


def synth(action_binding):
    """Only used by --synthetic. Never runs in the real gate."""
    def go():
        time.sleep(1.2)
        kbd = keyboard.Controller()
        mods = [{"ctrl": keyboard.Key.ctrl, "alt": keyboard.Key.alt,
                 "shift": keyboard.Key.shift, "cmd": keyboard.Key.cmd}[m]
                for m in action_binding["modifiers"]]
        for m in mods:
            kbd.press(m)
            time.sleep(0.02)
        trigger = action_binding["trigger"]
        if trigger["kind"] == "raw":
            rawtap.post_system_event(trigger["key_type"], True)
            time.sleep(0.05)
            rawtap.post_system_event(trigger["key_type"], False)
        else:
            key = getattr(keyboard.Key, trigger["name"])
            kbd.press(key)
            time.sleep(0.06)
            kbd.release(key)
        for m in reversed(mods):
            time.sleep(0.02)
            kbd.release(m)
    threading.Thread(target=go, daemon=True).start()


def synth_raw(key_type):
    def go():
        time.sleep(1.2)
        rawtap.post_system_event(key_type, True)
        time.sleep(0.06)
        rawtap.post_system_event(key_type, False)
    threading.Thread(target=go, daemon=True).start()


def synth_plain_key(name):
    def go():
        time.sleep(1.2)
        kbd = keyboard.Controller()
        key = getattr(keyboard.Key, name)
        kbd.press(key)
        time.sleep(0.06)
        kbd.release(key)
    threading.Thread(target=go, daemon=True).start()


class GateB:
    """
    One prompt at a time, driven from a main-thread timer.

    Each prompt is a condition plus a deadline. Nothing here blocks the run
    loop, so the window stays alive and the user can always see what it is
    doing.
    """

    def __init__(self, app, win, tool, target_proc):
        self.app = app
        self.win = win
        self.tool = tool
        self.target = target_proc
        self.queue = []
        self.current = None
        self.deadline = 0.0
        self.finished = False
        self.off_main = []

    def prompt(self, text, condition, on_pass=None, setup=None, expect=""):
        self.queue.append({"text": text, "condition": condition,
                           "on_pass": on_pass, "setup": setup,
                           "expect": expect})

    def step(self, text, fn):
        self.queue.append({"step": text, "fn": fn})

    def tick_(self, timer):
        if not mainthread.on_main():
            self.off_main.append(self.current["text"] if self.current else "?")
        if self.current is None:
            if not self.queue:
                if not self.finished:
                    self.finished = True
                    self.wrap_up()
                return
            item = self.queue.pop(0)
            if "step" in item:
                try:
                    item["fn"]()
                except Exception as exc:
                    record(item["step"], False, repr(exc))
                return
            self.current = item
            if item["setup"] is not None:
                try:
                    item["setup"]()
                except Exception as exc:
                    record(item["text"], False, "setup raised %r" % exc)
                    self.current = None
                    return
            say()
            say("   >>> %s" % item["text"])
            if item["expect"]:
                say("       you should see: %s" % item["expect"])
            self.deadline = time.monotonic() + PROMPT_SECONDS
            return
        try:
            done = self.current["condition"]()
        except Exception as exc:
            record(self.current["text"], False, repr(exc))
            self.current = None
            return
        if done:
            record(self.current["text"], True)
            if self.current["on_pass"] is not None:
                try:
                    self.current["on_pass"]()
                except Exception as exc:
                    record(self.current["text"] + " follow-up", False,
                           repr(exc))
            self.current = None
        elif time.monotonic() > self.deadline:
            record(self.current["text"], None,
                   "no result inside %ds" % PROMPT_SECONDS)
            self.current = None

    def wrap_up(self):
        # Guaranteed stop. This is cleanup, never the path a stop assertion
        # is allowed to pass through.
        self.tool.request_stop()
        time.sleep(0.4)
        self.tool.shutdown()
        if self.target is not None:
            self.target.kill()
        summarise(self.off_main)
        self.app.terminate_(None)


def build(g, tool, win):
    b = tool.binding_map()
    seen = {}

    # ---------- health, before anything is asked of him
    def show_health():
        say()
        say("   PERMISSIONS AND HEALTH, checked live in this process:")
        for line in health.report_lines(tool):
            say("      %s" % line)
        record("listeners are running",
               bool(tool.kbd_listener.running and tool.mouse_listener.running))
        record("media key watcher is running", tool.raw_tap_status()[0],
               "reason=%r" % tool.raw_tap_status()[1])
    g.step("health", show_health)

    # ---------- 1. an ordinary key reaches the tool
    def arm_ordinary():
        win.btn_test.performClick_(None)
        if SYNTHETIC:
            synth_plain_key("f19")
    g.prompt("PRESS AND RELEASE the letter A, once",
             lambda: str(win.test_outcome.stringValue()).startswith("Key seen"),
             setup=arm_ordinary,
             expect="Test Input says: Key seen")

    # ---------- 2. the measured defect, made visible
    def arm_raw():
        win.onTestAgain_(None)
        if SYNTHETIC:
            synth_raw(REWIND)
    g.prompt("PRESS AND RELEASE F7 ON ITS OWN, no fn key",
             lambda: str(win.test_outcome.stringValue()).startswith(
                 "Seen, but unusable"),
             setup=arm_raw,
             expect="Test Input says: Seen, but unusable, rewind (media key). "
                    "That is this Mac sending a media key, exactly as measured.")

    def close_test():
        if win.test_window is not None:
            win.test_window.orderOut_(None)
        win.name_field.setStringValue_("gate_b_take")
    g.step("close test window", close_test)

    # ---------- 3. RECORD, two full press and release cycles
    for cycle in (1, 2):
        def arm_rec_start(c=cycle):
            if SYNTHETIC:
                synth(b["record"])
        g.prompt("cycle %d of 2: HOLD fn AND PRESS F6 to START recording"
                 % cycle,
                 lambda: tool.state == "recording",
                 setup=arm_rec_start,
                 expect="the status line changes to Recording 'gate_b_take'")

        def arm_rec_stop(c=cycle):
            if SYNTHETIC:
                synth(b["record"])
        g.prompt("cycle %d of 2: HOLD fn AND PRESS F6 again to STOP" % cycle,
                 lambda: tool.state == "idle",
                 setup=arm_rec_stop,
                 expect="the status line goes back to Idle and says events "
                        "saved")

    # ---------- 4. PLAY, held open by Loop forever so it cannot self-finish
    def setup_play():
        names = [n for n, _ in win.names]
        if FIXTURE not in names:
            win.refresh_list()
            names = [n for n, _ in win.names]
        if FIXTURE in names:
            win.table.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(names.index(FIXTURE)), False)
        win.forever.setState_(1)
        win.sync_loop_field()
        record("playback armed on Loop forever so it cannot end by itself",
               win.forever.state() == 1)
    g.step("arm playback", setup_play)

    for cycle in (1, 2):
        def arm_play_start(c=cycle):
            if SYNTHETIC:
                synth(b["play"])
        g.prompt("cycle %d of 2: HOLD fn AND PRESS F7 to START playback"
                 % cycle,
                 lambda: tool.state == "playing",
                 on_pass=lambda: seen.update(
                     {"loops": tool.loops, "seq": tool.input_seq()}),
                 setup=arm_play_start,
                 expect="the status line changes to Playing 'gate_b_harmless', "
                        "repeating forever")

        def arm_play_stop(c=cycle):
            seen["seq_before_stop"] = tool.input_seq()
            if SYNTHETIC:
                synth(b["play"])

        def stopped():
            if tool.state != "idle":
                return False
            # The stop must be attributable to a KEYPRESS, and playback must
            # have been unable to end on its own. Loop forever guarantees the
            # second; a new callback sequence guarantees the first.
            seen["stopped_by_key"] = tool.input_seq() > seen.get(
                "seq_before_stop", 0)
            seen["was_forever"] = seen.get("loops") == 0
            return True

        def check_stop(c=cycle):
            record("cycle %d PLAY stop came from a physical press, not from "
                   "playback ending" % c,
                   bool(seen.get("stopped_by_key")) and bool(
                       seen.get("was_forever")),
                   "new_callbacks=%s loop_forever=%s"
                   % (seen.get("stopped_by_key"), seen.get("was_forever")))
        g.prompt("cycle %d of 2: HOLD fn AND PRESS F7 again to STOP" % cycle,
                 stopped, on_pass=check_stop, setup=arm_play_stop,
                 expect="the status line goes back to Idle")

    # ---------- 5. AUTOCLICK, against the harness's own window
    def park_cursor():
        if g.target is not None and getattr(g, "rect", None):
            r = g.rect
            mouse.Controller().position = (r["left"] + r["width"] // 2,
                                           r["top"] + r["height"] // 2)
            record("cursor parked inside the harness's own window", True)
        else:
            record("cursor parked inside the harness's own window", None,
                   "safe target window not available")
    g.step("park cursor", park_cursor)

    for cycle in (1, 2):
        def arm_auto_start(c=cycle):
            if SYNTHETIC:
                synth(b["autoclick"])
        g.prompt("cycle %d of 2: HOLD fn AND PRESS F8 to START the autoclicker"
                 % cycle,
                 lambda: tool.state == "autoclicking",
                 setup=arm_auto_start,
                 expect="the status line changes to Autoclicking, and the "
                        "count climbs")

        def arm_auto_stop(c=cycle):
            if SYNTHETIC:
                synth(b["autoclick"])
        g.prompt("cycle %d of 2: HOLD fn AND PRESS F8 again to STOP" % cycle,
                 lambda: tool.state == "idle",
                 setup=arm_auto_stop,
                 expect="the status line goes back to Idle")


def summarise(off_main):
    say()
    say("=" * 78)
    passed = sum(1 for _, ok, _ in results if ok is True)
    failed = [r for r in results if r[1] is False]
    incomplete = [r for r in results if r[1] is None]
    for name, ok, detail in results:
        if ok is False:
            say("FAILED:     %s %s" % (name, detail))
        elif ok is None:
            say("INCOMPLETE: %s %s" % (name, detail))
    say("GATE B: %d passed, %d failed, %d incomplete, of %d"
        % (passed, len(failed), len(incomplete), len(results)))
    if off_main:
        say("OFF MAIN THREAD: %s" % off_main)
    if SYNTHETIC:
        say("VERDICT: HARNESS SELF-CHECK %s. This proves the harness, the "
            "prompts, the watchdogs and the isolation. It proves NOTHING "
            "about the physical keyboard, which is the whole point of the "
            "real run." % ("PASS" if not failed and not incomplete else "FAIL"))
    elif failed:
        say("VERDICT: FAILED. The hotkey defect is NOT fixed end to end.")
    elif incomplete:
        say("VERDICT: INCOMPLETE. A prompt went unanswered, so the gate did "
            "not run. This blocks the push exactly as a failure would.")
    else:
        say("VERDICT: PASS. The hotkeys work under real fingers.")
    say("=" * 78)
    say()
    say("ISOLATION, verified after the run:")
    after = hash_dir(REAL_RECORDINGS)
    same = after == BEFORE_HASHES
    for name, digest in sorted(after.items()):
        say("   %s  %s" % (digest, name))
    say("   your real recordings unchanged: %s" % same)
    say("   your real config file %s: %s"
        % (REAL_CONFIG, "still absent" if not REAL_CONFIG.exists()
           else "present, restored from backup" if CONFIG_BACKUP
           else "PRESENT AND NOT EXPECTED"))
    restore_real_config()
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    sys.stdout.flush()


CONFIG_BACKUP = None


def backup_real_config():
    global CONFIG_BACKUP
    if REAL_CONFIG.exists():
        CONFIG_BACKUP = (REAL_CONFIG.read_bytes(),
                         hashlib.sha256(REAL_CONFIG.read_bytes()).hexdigest())


def restore_real_config():
    if CONFIG_BACKUP is None:
        return
    REAL_CONFIG.write_bytes(CONFIG_BACKUP[0])
    now = hashlib.sha256(REAL_CONFIG.read_bytes()).hexdigest()
    say("   config restored, hash matches: %s" % (now == CONFIG_BACKUP[1]))


BEFORE_HASHES = {}


def main():
    global BEFORE_HASHES
    say("GATE B: the physical gate%s" % (", HARNESS SELF-CHECK"
                                         if SYNTHETIC else ""))
    say("=" * 78)
    BEFORE_HASHES = hash_dir(REAL_RECORDINGS)
    backup_real_config()
    say("   your real recordings, hashed BEFORE anything runs:")
    for name, digest in sorted(BEFORE_HASHES.items()):
        say("      %s  %s" % (digest, name))
    say("   throwaway recordings : %s" % TMP_RECORDINGS)
    say("   throwaway config     : %s" % TMP_CONFIG)
    say("   your real config     : %s"
        % ("absent, and will stay absent" if not REAL_CONFIG.exists()
           else "backed up, restored at the end"))
    plant_fixture()

    target_proc, rect = None, None
    try:
        target_proc = subprocess.Popen(
            [str(VENV_PY), str(SAFE_TARGET)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        line = target_proc.stdout.readline()
        rect = json.loads(line)
        say("   autoclicker target   : the harness's own window at %s" % rect)
    except Exception as exc:
        say("   autoclicker target   : NOT AVAILABLE (%r). The autoclick "
            "prompts will be marked incomplete rather than clicking anywhere "
            "unowned." % (exc,))
        if target_proc is not None:
            target_proc.kill()
            target_proc = None

    tool = macro.MacroTool("all", verbose=False, config_path=TMP_CONFIG)
    tool.start_listeners()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    tool.start_raw_tap()
    win = gui.MacroWindow.alloc().initWithTool_(tool)

    say("   hotkeys under test   : %s"
        % {a: bindings.format_binding(bd)
           for a, bd in tool.binding_map().items()})
    say()
    say("   Each prompt waits %ds. Nothing is swallowed, nothing of yours is "
        "touched." % PROMPT_SECONDS)

    g = GateB(app, win, tool, target_proc)
    g.rect = rect
    build(g, tool, win)

    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        gui.POLL_SECONDS, win, "tick:", None, True)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.3, g, "tick:", None, True)
    app.run()
    bad = [r for r in results if r[1] is not True]
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
