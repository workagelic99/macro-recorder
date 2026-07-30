#!/usr/bin/env python3
"""
GATE A: every action fires through the REAL running listener, in a FRESH
process, with the REAL context the window holds.

  gate_a.py i                     absent config, the built-in defaults
  gate_a.py ii    [--config P]    a persisted custom config, plus the negative
                                  rule that the replaced default now matches
                                  nothing, plus the same rule after a live edit
  gate_a.py iii                   THE EDITOR GATE: bindings changed through the
                                  real GUI surface, from all three action rows
  gate_a.py relaunch --config P   fire every action of a config written by an
                                  earlier process, which is what proves an edit
                                  survived a cold restart

Rules every case obeys, because the old proof_gui.py broke all of them:

  no GUI action method is ever called to start an action. onRecord_, onPlay_
  and onAutoclick_ are never touched. The only way an action starts here is a
  key event going through the OS and coming back via the listener. The editor
  case does press the editor's own buttons, which is the point: an edit that
  is not made through the surface the user has proves nothing about that
  surface.

  no priming. Nothing sets engine context directly. Sentinel values are typed
  into the actual Cocoa field, the actual table selection and the actual
  checkbox, on the main thread, and the engine must pick them up by itself.

  values change twice in case i. Each action runs once, then every value is
  changed in the SAME live process and it runs again, so a one-time startup
  copy of the context cannot pass.

  main thread only for AppKit. Every field write, selection change, button
  press and state read happens in a timer callback on the main thread. Worker
  threads only inject key events, which is not an AppKit call.

Isolation: an injected temporary recordings directory and an injected
temporary config path. The real recordings directory and the real
~/.macro-recorder.json are never read, written or created.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASE = sys.argv[1] if len(sys.argv) > 1 else "i"
CONFIG_ARG = None
if "--config" in sys.argv:
    CONFIG_ARG = Path(sys.argv[sys.argv.index("--config") + 1])

TMP_ROOT = Path(tempfile.mkdtemp(prefix="macro_gate_a_"))
TMP_RECORDINGS = TMP_ROOT / "recordings"
TMP_RECORDINGS.mkdir(parents=True)
TMP_CONFIG = CONFIG_ARG if CONFIG_ARG is not None else (TMP_ROOT / "config.json")

import macro  # noqa: E402
macro.RECORDINGS_DIR = TMP_RECORDINGS          # injected BEFORE anything reads it

import bindings  # noqa: E402
import gui  # noqa: E402
import mainthread  # noqa: E402
import rawtap  # noqa: E402
from AppKit import (NSApplication,  # noqa: E402
                    NSApplicationActivationPolicyRegular)
from Foundation import NSIndexSet, NSTimer  # noqa: E402
from pynput import keyboard  # noqa: E402

FIXTURE_A = "gate_fixture_one"
FIXTURE_B = "gate_fixture_two"

MOD_KEYS = {"ctrl": keyboard.Key.ctrl, "alt": keyboard.Key.alt,
            "shift": keyboard.Key.shift, "cmd": keyboard.Key.cmd}

# F16 to F19 are deliberate: macOS binds nothing to them, so injecting one
# cannot trip a system shortcut, and they are not the F13 to F15 the playback
# fixtures use as payload.
CUSTOM_MAP = {
    "record": bindings.make_binding(["ctrl"], {"kind": "named", "name": "f16"}),
    "play": bindings.make_binding([], {"kind": "named", "name": "f17"}),
    "autoclick": bindings.make_binding([], {"kind": "named", "name": "f18"}),
}

results = []


def record(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("   %-56s %s%s" % (name, "PASS" if ok else "FAIL",
                             ("  " + detail) if detail else ""))
    sys.stdout.flush()


def plant(name, seconds=6.0):
    """A long keys-only fixture: F15 presses, harmless, cannot self-complete."""
    events, t = [], 0.0
    while t < seconds:
        events.append({"type": "key", "kind": "special", "name": "f15",
                       "pressed": True, "t": round(t, 6)})
        events.append({"type": "key", "kind": "special", "name": "f15",
                       "pressed": False, "t": round(t + 0.02, 6)})
        t += 0.4
    path = TMP_RECORDINGS / (name + ".json")
    path.write_text(json.dumps({"version": macro.FORMAT_VERSION, "name": name,
                                "created": "planted-by-gate-a",
                                "screen": macro.screen_size(),
                                "capture_moves": False, "events": events},
                               indent=2), encoding="utf-8")


def key_for_trigger(trigger):
    if trigger["kind"] == "named":
        return getattr(keyboard.Key, trigger["name"])
    if trigger["kind"] == "physical":
        return keyboard.KeyCode.from_vk(trigger["vk"])
    return None                                   # raw, posted a different way


def inject_async(binding):
    """
    Fire a whole binding through the OS: modifiers down, trigger, then up.
    Not an AppKit call, so a worker thread is the right place for it.
    """
    def go():
        kbd = keyboard.Controller()
        mods = [MOD_KEYS[m] for m in binding["modifiers"] if m in MOD_KEYS]
        for m in mods:
            kbd.press(m)
            time.sleep(0.02)
        trigger = binding["trigger"]
        if trigger["kind"] == "raw":
            rawtap.post_system_event(trigger["key_type"], True)
            time.sleep(0.05)
            rawtap.post_system_event(trigger["key_type"], False)
        else:
            key = key_for_trigger(trigger)
            kbd.press(key)
            time.sleep(0.05)
            kbd.release(key)
        for m in reversed(mods):
            time.sleep(0.02)
            kbd.release(m)
    threading.Thread(target=go, daemon=True).start()


def write_config(path, mapping):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": bindings.SCHEMA_VERSION,
                                "bindings": bindings.validate_map(mapping)},
                               indent=2) + "\n", encoding="utf-8")


def labels(mapping):
    return {a: bindings.format_binding(b) for a, b in mapping.items()}


def within_content_bounds(win, control):
    content = win.contentView()
    r = control.convertRect_toView_(control.bounds(), content)
    b = content.bounds()
    return (r.origin.x >= b.origin.x - 0.5
            and r.origin.y >= b.origin.y - 0.5
            and r.origin.x + r.size.width <= b.origin.x + b.size.width + 0.5
            and r.origin.y + r.size.height <= b.origin.y + b.size.height + 0.5)


class Gate:
    """Shared helpers. Every method here runs on the main thread."""

    def __init__(self, app, win, tool):
        self.app = app
        self.win = win
        self.tool = tool
        self.requests = []
        original = tool.handle_hotkey

        def spy(action):
            self.requests.append(action)
            return original(action)
        tool.handle_hotkey = spy

    def select(self, name):
        names = [n for n, _ in self.win.names]
        if name not in names:
            self.win.refresh_list()
            names = [n for n, _ in self.win.names]
        if name in names:
            self.win.table.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(names.index(name)), False)
            return True
        return False

    def sentinels(self, name, fixture, speed, forever, loops, lo, hi):
        self.win.name_field.setStringValue_(name)
        self.win.speed_field.setStringValue_(str(speed))
        self.win.forever.setState_(1 if forever else 0)
        self.win.sync_loop_field()
        if not forever:
            self.win.loop_field.setStringValue_(str(loops))
        self.win.min_field.setStringValue_(str(lo))
        self.win.max_field.setStringValue_(str(hi))
        return self.select(fixture)


# ---------------------------------------------------------------------------
# case i: absent config, built-in defaults, every value changed once
# ---------------------------------------------------------------------------

def steps_case_i(g):
    s = []
    bmap = g.tool.binding_map()

    def r1_setup():
        ok = g.sentinels("sentinel_one", FIXTURE_A, 1.5, True, 1, 55, 95)
        record("round1 sentinels typed into real Cocoa surfaces", ok)
    s.append(r1_setup)

    s.append(lambda: inject_async(bmap["record"]))

    def r1_record_check():
        record("round1 RECORD started via listener only",
               g.tool.state == "recording", "state=%s" % g.tool.state)
        record("round1 RECORD used the TYPED name",
               g.tool.name == "sentinel_one", "engine used %r" % g.tool.name)
    s.append(r1_record_check)

    s.append(lambda: inject_async(bmap["record"]))
    s.append(lambda: record("round1 RECORD stopped via listener only",
                            g.tool.state == "idle", "state=%s" % g.tool.state))

    s.append(lambda: record("round1 selected fixture A in the real table",
                            g.select(FIXTURE_A)))
    s.append(lambda: inject_async(bmap["play"]))

    def r1_play_check():
        record("round1 PLAY started via listener only",
               g.tool.state == "playing", "state=%s" % g.tool.state)
        record("round1 PLAY used the SELECTED fixture",
               g.tool.name == FIXTURE_A, "engine used %r" % g.tool.name)
        record("round1 PLAY used the ENTERED speed",
               abs(g.tool.speed - 1.5) < 1e-9, "engine used %r" % g.tool.speed)
        record("round1 PLAY used Loop forever from the checkbox",
               g.tool.loops == 0, "engine used %r" % g.tool.loops)
    s.append(r1_play_check)

    s.append(lambda: inject_async(bmap["play"]))
    s.append(lambda: record("round1 PLAY stopped via listener only",
                            g.tool.state == "idle", "state=%s" % g.tool.state))

    s.append(lambda: inject_async(bmap["autoclick"]))

    def r1_auto_check():
        record("round1 AUTOCLICK started via listener only",
               g.tool.state == "autoclicking", "state=%s" % g.tool.state)
        record("round1 AUTOCLICK used the ENTERED bounds",
               (g.tool.click_min_ms, g.tool.click_max_ms) == (55, 95),
               "engine used %r" % ((g.tool.click_min_ms, g.tool.click_max_ms),))
    s.append(r1_auto_check)

    s.append(lambda: inject_async(bmap["autoclick"]))
    s.append(lambda: record("round1 AUTOCLICK stopped via listener only",
                            g.tool.state == "idle", "state=%s" % g.tool.state))

    def r2_setup():
        ok = g.sentinels("sentinel_two", FIXTURE_B, 0.75, False, 3, 120, 180)
        record("round2 every value CHANGED in the same process", ok)
    s.append(r2_setup)

    s.append(lambda: inject_async(bmap["record"]))
    s.append(lambda: record("round2 RECORD used the CHANGED name",
                            g.tool.name == "sentinel_two",
                            "engine used %r" % g.tool.name))
    s.append(lambda: inject_async(bmap["record"]))
    s.append(lambda: None)

    s.append(lambda: inject_async(bmap["play"]))

    def r2_play_check():
        record("round2 PLAY used the CHANGED selection",
               g.tool.name == FIXTURE_B, "engine used %r" % g.tool.name)
        record("round2 PLAY used the CHANGED speed",
               abs(g.tool.speed - 0.75) < 1e-9, "engine used %r" % g.tool.speed)
        record("round2 PLAY used the CHANGED repeat count",
               g.tool.loops == 3, "engine used %r" % g.tool.loops)
    s.append(r2_play_check)
    s.append(lambda: inject_async(bmap["play"]))
    s.append(lambda: None)

    s.append(lambda: inject_async(bmap["autoclick"]))
    s.append(lambda: record("round2 AUTOCLICK used the CHANGED bounds",
                            (g.tool.click_min_ms,
                             g.tool.click_max_ms) == (120, 180),
                            "engine used %r" % ((g.tool.click_min_ms,
                                                 g.tool.click_max_ms),)))
    s.append(lambda: inject_async(bmap["autoclick"]))
    s.append(lambda: record("round2 AUTOCLICK stopped",
                            g.tool.state == "idle", "state=%s" % g.tool.state))
    return s


# ---------------------------------------------------------------------------
# case ii / relaunch: a persisted config drives everything
# ---------------------------------------------------------------------------

def steps_persisted(g, negatives=True):
    s = []
    bmap = g.tool.binding_map()
    expected = bindings.validate_map(json.loads(
        TMP_CONFIG.read_text(encoding="utf-8"))["bindings"])

    def loaded():
        record("the persisted config is the live map",
               labels(bmap) == labels(expected), "live=%s" % labels(bmap))
        record("loading a valid config raised no warning",
               g.tool.binding_warning is None,
               "warning=%r" % g.tool.binding_warning)
        ok = g.sentinels("persisted_one", FIXTURE_A, 1.25, True, 1, 60, 90)
        record("sentinels typed into real Cocoa surfaces", ok)
    s.append(loaded)

    for action, state, checks in (
            ("record", "recording", [("used the TYPED name",
                                      lambda: g.tool.name == "persisted_one")]),
            ("play", "playing", [("used the SELECTED fixture",
                                  lambda: g.tool.name == FIXTURE_A),
                                 ("used the ENTERED speed",
                                  lambda: abs(g.tool.speed - 1.25) < 1e-9)]),
            ("autoclick", "autoclicking",
             [("used the ENTERED bounds",
               lambda: (g.tool.click_min_ms, g.tool.click_max_ms) == (60, 90))]),
    ):
        s.append(lambda a=action: inject_async(bmap[a]))

        def started(a=action, st=state, cs=checks):
            record("CUSTOM %s (%s) started via listener only"
                   % (a, bindings.format_binding(bmap[a])),
                   g.tool.state == st, "state=%s" % g.tool.state)
            for text, fn in cs:
                record("CUSTOM %s %s" % (a, text), fn())
        s.append(started)
        s.append(lambda a=action: inject_async(bmap[a]))

        def stopped(a=action):
            record("CUSTOM %s stopped via listener only" % a,
                   g.tool.state == "idle", "state=%s" % g.tool.state)
        s.append(stopped)
        if action == "record":
            s.append(lambda: record("selected fixture A for playback",
                                    g.select(FIXTURE_A)))

    if not negatives:
        return s

    # THE NEGATIVE RULE: the defaults this config replaced must now match
    # nothing at all. A config that loads but leaves the old key live would
    # pass every positive test above and still be broken.
    defaults = bindings.default_map()
    for action in ("record", "play", "autoclick"):
        s.append(lambda a=action: (g.requests.clear(),
                                   inject_async(defaults[a])))

        def dead(a=action):
            record("replaced default %s requested NO action"
                   % bindings.format_binding(defaults[a]),
                   g.requests == [], "requests=%s" % g.requests)
            record("replaced default %s left the tool idle" % a,
                   g.tool.state == "idle", "state=%s" % g.tool.state)
        s.append(dead)

    # ...and the same rule after a LIVE edit, before any relaunch.
    live_edit = {"record": bindings.make_binding(
                     [], {"kind": "named", "name": "f19"}),
                 "play": bmap["play"], "autoclick": bmap["autoclick"]}

    def do_live_edit():
        g.tool.set_binding_map(live_edit)
        g.win.refresh_binding_labels()
        record("live edit swapped the map without a relaunch",
               g.tool.binding_label("record") == "F19",
               "label=%s" % g.tool.binding_label("record"))
    s.append(do_live_edit)

    s.append(lambda: (g.requests.clear(), inject_async(bmap["record"])))
    s.append(lambda: record("the LIVE-REPLACED binding %s requests nothing"
                            % bindings.format_binding(bmap["record"]),
                            g.requests == [], "requests=%s" % g.requests))
    s.append(lambda: inject_async(live_edit["record"]))
    s.append(lambda: record("the newly edited binding F19 starts RECORD",
                            g.tool.state == "recording",
                            "state=%s" % g.tool.state))
    s.append(lambda: inject_async(live_edit["record"]))
    s.append(lambda: record("the newly edited binding F19 stops RECORD",
                            g.tool.state == "idle", "state=%s" % g.tool.state))
    return s


# ---------------------------------------------------------------------------
# case iii: THE EDITOR GATE
# ---------------------------------------------------------------------------

EDITS = [
    ("record", bindings.make_binding([], {"kind": "named", "name": "f16"})),
    ("play", bindings.make_binding(["ctrl"], {"kind": "named", "name": "f17"})),
    ("autoclick", bindings.make_binding([], {"kind": "named", "name": "f18"})),
]


def file_map():
    if not TMP_CONFIG.exists():
        return None
    try:
        return bindings.validate_map(
            json.loads(TMP_CONFIG.read_text(encoding="utf-8"))["bindings"])
    except Exception:
        return None


def steps_case_iii(g):
    s = []
    state = {}

    def controls_check():
        bad = []
        for action in ("record", "play", "autoclick"):
            for kind, control in (("field", g.win.binding_fields[action]),
                                  ("Set", g.win.set_buttons[action]),
                                  ("Cancel", g.win.cancel_buttons[action])):
                if control.isHidden():
                    bad.append("%s %s hidden" % (action, kind))
                if not within_content_bounds(g.win.window, control):
                    bad.append("%s %s outside the window" % (action, kind))
        for name, control in (("Reset Defaults", g.win.btn_reset),
                              ("Test Input", g.win.btn_test)):
            if control.isHidden() or not control.isEnabled():
                bad.append("%s hidden or disabled" % name)
            if not within_content_bounds(g.win.window, control):
                bad.append("%s outside the window" % name)
        for action in ("record", "play", "autoclick"):
            if not g.win.set_buttons[action].isEnabled():
                bad.append("%s Set disabled" % action)
        record("every editor control is visible, enabled and inside the window",
               not bad, "problems=%s" % bad)
    s.append(controls_check)

    # ---- three successful edits, one per action ROW
    for action, binding in EDITS:
        def press_set(a=action):
            state["before"] = g.tool.binding_map()
            state["file_before"] = file_map()
            g.win.set_buttons[a].performClick_(None)
            record("Set pressed on the %s row, capture armed" % a,
                   g.tool.capture_active() and g.win.capturing == a,
                   "capturing=%r" % g.win.capturing)
            others = [x for x in ("record", "play", "autoclick") if x != a]
            record("other rows' Set buttons disabled during capture on %s" % a,
                   not any(g.win.set_buttons[o].isEnabled() for o in others))
        s.append(press_set)
        s.append(lambda b=binding: inject_async(b))

        def applied(a=action, b=binding):
            live = g.tool.binding_map()
            want = bindings.format_binding(b)
            record("%s row now bound to %s" % (a, want),
                   bindings.format_binding(live[a]) == want,
                   "live=%s" % bindings.format_binding(live[a]))
            unchanged = [x for x in ("record", "play", "autoclick") if x != a]
            same = all(bindings.format_binding(live[x])
                       == bindings.format_binding(state["before"][x])
                       for x in unchanged)
            record("ONLY the %s entry changed" % a, same,
                   "live=%s before=%s" % (labels(live), labels(state["before"])))
            record("the %s row LABEL shows the new binding" % a,
                   str(g.win.binding_fields[a].stringValue()) == want,
                   "label=%r" % str(g.win.binding_fields[a].stringValue()))
            on_disk = file_map()
            record("the new %s binding is on disk" % a,
                   on_disk is not None
                   and bindings.format_binding(on_disk[a]) == want,
                   "file=%s" % (labels(on_disk) if on_disk else None))
        s.append(applied)

    # ---- Cancel, pressed on a NAMED row
    def cancel_arm():
        state["before"] = g.tool.binding_map()
        g.win.set_buttons["play"].performClick_(None)
        record("Set pressed on the play row for the Cancel case",
               g.tool.capture_active())
    s.append(cancel_arm)

    def cancel_press():
        g.win.cancel_buttons["play"].performClick_(None)
        record("Cancel on the play row disarmed the capture",
               not g.tool.capture_active() and g.win.capturing is None)
        record("Cancel changed NO binding",
               labels(g.tool.binding_map()) == labels(state["before"]),
               "live=%s" % labels(g.tool.binding_map()))
        record("Cancel restored the play row label",
               str(g.win.binding_fields["play"].stringValue())
               == bindings.format_binding(state["before"]["play"]),
               "label=%r" % str(g.win.binding_fields["play"].stringValue()))
    s.append(cancel_press)

    # ---- duplicate rejection, pressed on a NAMED row
    def dup_arm():
        state["before"] = g.tool.binding_map()
        state["file_before"] = TMP_CONFIG.read_bytes()
        g.win.set_buttons["autoclick"].performClick_(None)
    s.append(dup_arm)
    s.append(lambda: inject_async(EDITS[0][1]))          # already record's key

    def dup_check():
        record("a duplicate binding was REJECTED",
               labels(g.tool.binding_map()) == labels(state["before"]),
               "live=%s" % labels(g.tool.binding_map()))
        record("the duplicate left the config file byte for byte unchanged",
               TMP_CONFIG.read_bytes() == state["file_before"])
        record("the duplicate was explained on screen",
               "cannot share" in str(g.win.status.stringValue()).lower()
               or "cannot use" in str(g.win.status.stringValue()).lower(),
               "status=%r" % str(g.win.status.stringValue()))
    s.append(dup_check)

    # ---- a FAILED SAVE, pressed on a NAMED row
    def fail_arm():
        state["before"] = g.tool.binding_map()
        state["file_before"] = TMP_CONFIG.read_bytes()
        state["real_save"] = bindings.save_map
        bindings.save_map = lambda *a, **k: (
            False, "Could not save hotkeys (injected failure). Your previous "
                   "settings are unchanged.")
        g.win.set_buttons["record"].performClick_(None)
    s.append(fail_arm)
    s.append(lambda: inject_async(
        bindings.make_binding([], {"kind": "named", "name": "f19"})))

    def fail_check():
        bindings.save_map = state["real_save"]
        record("a failed save left the LIVE map intact",
               labels(g.tool.binding_map()) == labels(state["before"]),
               "live=%s" % labels(g.tool.binding_map()))
        record("a failed save left the FILE byte for byte intact",
               TMP_CONFIG.read_bytes() == state["file_before"])
        record("a failed save was surfaced to the user",
               "could not save" in str(g.win.status.stringValue()).lower(),
               "status=%r" % str(g.win.status.stringValue()))
        record("a failed save restored the row label",
               str(g.win.binding_fields["record"].stringValue())
               == bindings.format_binding(state["before"]["record"]))
    s.append(fail_check)

    # ---- Reset Defaults
    def reset_press():
        g.win.btn_reset.performClick_(None)
        want = labels(bindings.default_map())
        record("Reset Defaults restored the built-in bindings",
               labels(g.tool.binding_map()) == want,
               "live=%s" % labels(g.tool.binding_map()))
        record("Reset Defaults wrote the defaults to disk",
               labels(file_map() or {}) == want,
               "file=%s" % labels(file_map() or {}))
        record("Reset Defaults updated every row label",
               all(str(g.win.binding_fields[a].stringValue()) == want[a]
                   for a in want))
    s.append(reset_press)

    # ---- re-apply the three edits so the file ends in the EDITED state, which
    # is what the relaunch process then has to honour
    for action, binding in EDITS:
        s.append(lambda a=action: g.win.set_buttons[a].performClick_(None))
        s.append(lambda b=binding: inject_async(b))

        def reapplied(a=action, b=binding):
            record("re-applied %s = %s through the editor"
                   % (a, bindings.format_binding(b)),
                   bindings.format_binding(g.tool.binding_map()[a])
                   == bindings.format_binding(b),
                   "live=%s" % bindings.format_binding(g.tool.binding_map()[a]))
        s.append(reapplied)

    # ---- and prove the edited bindings fire in THIS process before relaunch
    def before_fire():
        record("final saved config matches the three edits",
               labels(file_map() or {}) == labels(dict(EDITS)),
               "file=%s" % labels(file_map() or {}))
        record("sentinels typed for the post-edit firing check",
               g.sentinels("editor_one", FIXTURE_A, 1.0, True, 1, 70, 110))
    s.append(before_fire)

    for action, binding in EDITS:
        s.append(lambda b=binding: inject_async(b))

        def fired(a=action, b=binding):
            want = {"record": "recording", "play": "playing",
                    "autoclick": "autoclicking"}[a]
            record("edited %s (%s) fires through the listener"
                   % (a, bindings.format_binding(b)),
                   g.tool.state == want, "state=%s" % g.tool.state)
        s.append(fired)
        s.append(lambda b=binding: inject_async(b))
        s.append(lambda a=action: record("edited %s stops again" % a,
                                         g.tool.state == "idle",
                                         "state=%s" % g.tool.state))
        if action == "record":
            s.append(lambda: g.select(FIXTURE_A))
    return s


# ---------------------------------------------------------------------------

def summarise(title):
    print()
    print("=" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        if not ok:
            print("FAILED: %s %s" % (name, detail))
    print("%s: %d of %d assertions passed" % (title, passed, len(results)))
    print("VERDICT: %s" % ("PASS" if passed == len(results) and results
                           else "FAIL"))
    print("=" * 78)
    sys.stdout.flush()


def main():
    titles = {"i": "GATE A (i) absent config, built-in defaults",
              "ii": "GATE A (ii) persisted custom config",
              "iii": "GATE A (iii) THE EDITOR GATE",
              "relaunch": "GATE A relaunch, edited config in a fresh process"}
    title = titles.get(CASE)
    if title is None:
        print(__doc__)
        return 2
    print(title)
    print("  recordings dir : %s (injected)" % TMP_RECORDINGS)
    print("  config path    : %s (injected)" % TMP_CONFIG)
    plant(FIXTURE_A)
    plant(FIXTURE_B)

    if CASE == "ii" and CONFIG_ARG is None:
        write_config(TMP_CONFIG, CUSTOM_MAP)
    if CASE in ("ii", "relaunch") and not TMP_CONFIG.exists():
        print("no config at %s, nothing to prove" % TMP_CONFIG)
        return 2

    tool = macro.MacroTool("all", verbose=False, config_path=TMP_CONFIG)
    print("  bindings       : %s" % labels(tool.binding_map()))
    print("  config warning : %r" % tool.binding_warning)
    tool.start_listeners()
    print("  listeners      : kbd=%s mouse=%s"
          % (tool.kbd_listener.running, tool.mouse_listener.running))

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    tool.start_raw_tap()
    win = gui.MacroWindow.alloc().initWithTool_(tool)
    g = Gate(app, win, tool)

    if CASE == "i":
        steps = steps_case_i(g)
    elif CASE == "ii":
        steps = steps_persisted(g, negatives=True)
    elif CASE == "relaunch":
        steps = steps_persisted(g, negatives=False)
    else:
        steps = steps_case_iii(g)

    def finish():
        ok, line = driver.thread_verdict()
        if not ok:
            record("every gate step ran on the main thread", False, line)
        else:
            record("every gate step ran on the main thread", True)
        summarise(title)

    driver = mainthread.Steps(app, [("step", fn) for fn in steps],
                              on_finish=finish)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        gui.POLL_SECONDS, win, "tick:", None, True)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.9, driver, "tick:", None, True)
    app.run()

    tool.shutdown()
    if CONFIG_ARG is None:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
    else:
        shutil.rmtree(TMP_RECORDINGS, ignore_errors=True)
    return 0 if results and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
