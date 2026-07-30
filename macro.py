#!/usr/bin/env python3
"""
macro.py - record and replay mouse and keyboard on macOS.

Records clicks, scrolls and keys with timestamps, replays them at the exact
screen coordinates they were recorded at. The command line and the GUI are the
same engine: MacroTool below. gui.py drives this class, it does not
reimplement any of it.

See README.md for setup and the two macOS permissions you must grant.
"""

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from pynput import keyboard, mouse

import bindings
import rawtap

# ===========================================================================
# CONFIG - change things here
# ===========================================================================

# Hotkeys. NOTE: many games bind the F-keys to their own actions. If your game
# already uses F6/F7/F8, change them here to something the game ignores, for
# example keyboard.Key.f13, or keyboard.Key.pause, or keyboard.Key.scroll_lock.
# Any name from the pynput Key list works.
HOTKEY_RECORD = keyboard.Key.f6        # start / stop recording
HOTKEY_PLAY = keyboard.Key.f7          # start / stop playback
HOTKEY_AUTOCLICK = keyboard.Key.f8     # start / stop autoclicker

# Record the full mouse movement path, not just clicks.
# False (default): the cursor teleports straight to each click position.
# True: every mouse move is recorded too, so drags and hand-drawn paths work.
#       Recordings get much bigger and replay is more sensitive to timing.
CAPTURE_MOUSE_MOVES = False

# Milliseconds the cursor is given to settle at its new position before the
# button is pressed. This does NOT cost you timing accuracy: playback moves
# the cursor this far AHEAD of schedule and still presses exactly on time.
#
# Do not set this to 0. macOS stamps a press with the cursor position it has
# already committed, so pressing immediately after setting the position makes
# every click land on the PREVIOUS target, silently shifting the whole
# sequence by one step. Measured on macOS 26.4.1: 0 ms and 5 ms both produced
# shifted clicks, 10 ms was correct. Raise it if clicks still land wrong.
MOVE_SETTLE_MS = 10

# How often the playback loop wakes up to check whether you pressed stop.
# Lower means the stop hotkey reacts faster, at slightly more idle CPU.
STOP_POLL_SECONDS = 0.02

# ===========================================================================
# End of config
# ===========================================================================

TOOL_DIR = Path(__file__).resolve().parent

# Your recordings live next to the tool. MACRO_RECORDINGS_DIR overrides that,
# which is how the proof harnesses run against a throwaway directory instead of
# writing into yours: a test that can reach your real recordings is one bad
# path away from overwriting one.
RECORDINGS_DIR = Path(os.environ.get("MACRO_RECORDINGS_DIR")
                      or (TOOL_DIR / "recordings"))
FORMAT_VERSION = 1
SPIN_SECONDS = 0.0015  # final busy-wait window, keeps timing tight
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Kept only so the module-level names above still mean something. The LIVE
# bindings are owned by each MacroTool instance and come from bindings.py, so
# an edited binding changes matching and labels together. Nothing matches
# against this set any more.
HOTKEYS = {HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_AUTOCLICK}


# --- key serialisation ------------------------------------------------------

def key_to_json(key):
    """pynput key object to a plain dict we can store."""
    if isinstance(key, keyboard.Key):
        return {"kind": "special", "name": key.name}
    if getattr(key, "char", None) is not None:
        return {"kind": "char", "char": key.char}
    return {"kind": "vk", "vk": key.vk}


def key_from_json(d):
    """Stored dict back to a pynput key object."""
    kind = d.get("kind")
    if kind == "special":
        return getattr(keyboard.Key, d["name"])
    if kind == "char":
        return keyboard.KeyCode.from_char(d["char"])
    if kind == "vk":
        return keyboard.KeyCode.from_vk(d["vk"])
    raise ValueError("unknown key kind in recording: %r" % (kind,))


def key_token(key):
    """Stable short string for a key, used to match our own injected events."""
    if isinstance(key, keyboard.Key):
        return "s:" + key.name
    if getattr(key, "char", None) is not None:
        return "c:" + key.char
    return "v:" + str(key.vk)


def key_description(key):
    """Plain words for a key, for the Test Input readout."""
    if isinstance(key, keyboard.Key):
        return key.name.replace("_", " ")
    char = getattr(key, "char", None)
    if char and char.isprintable():
        return "the %s key" % char
    return "key code %s" % getattr(key, "vk", "unknown")


def token_from_json(d):
    kind = d.get("kind")
    if kind == "special":
        return "s:" + d["name"]
    if kind == "char":
        return "c:" + d["char"]
    return "v:" + str(d["vk"])


def warm_up_pyobjc():
    """
    Resolve one pyobjc symbol on the main thread before any listener starts.

    pynput's macOS listener calls HIServices.AXIsProcessTrusted() from inside
    each listener thread. pyobjc resolves that lazily via get_constant, which
    does funcmap.pop(name) with no lock. Start the keyboard and mouse
    listeners at the same moment and both threads race for the same symbol:
    the first pops it, the second dies with KeyError: 'AXIsProcessTrusted',
    and that listener is silently gone, taking the hotkeys with it. Touching
    it once from here makes the later lookups hit a resolved attribute.
    """
    try:
        import HIServices
        HIServices.AXIsProcessTrusted()
    except Exception:
        pass


def screen_size():
    """Best effort screen size, used only to warn about resolution changes."""
    try:
        from AppKit import NSScreen
        frame = NSScreen.mainScreen().frame()
        return [int(frame.size.width), int(frame.size.height)]
    except Exception:
        return None


def list_recordings():
    """[(name, event_count, created), ...] sorted by name."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for path in sorted(RECORDINGS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append((path.stem, len(data.get("events", [])),
                        data.get("created", "?")))
        except Exception as exc:
            out.append((path.stem, -1, "unreadable: %s" % exc))
    return out


def load_recording(name):
    path = RECORDINGS_DIR / (name + ".json")
    if not path.exists():
        raise FileNotFoundError("no recording named '%s' in %s"
                                % (name, RECORDINGS_DIR))
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ValueError("recording file is malformed")
    for ev in data["events"]:
        if not isinstance(ev, dict) or "type" not in ev or "t" not in ev:
            raise ValueError("recording contains a malformed event")
    return data


def valid_name(name):
    return bool(NAME_RE.match(name or ""))


# --- the engine -------------------------------------------------------------

class MacroTool:
    """
    Everything the tool does. The CLI and the GUI both drive this class.

    mode decides which hotkeys are live:
      "record" / "play" / "autoclick"  one armed mode, used by the CLI
      "all"                            every hotkey live, used by the GUI
    """

    def __init__(self, mode, name=None, speed=1.0, loops=1,
                 click_min_ms=80, click_max_ms=140, verbose=True,
                 binding_map=None, config_path=None):
        self.mode = mode
        self.name = name

        # LIVE bindings, owned by this instance. Swapped atomically on edit.
        self.config_path = Path(config_path) if config_path is not None \
            else bindings.CONFIG_PATH
        if binding_map is not None:
            self._bindings = bindings.validate_map(binding_map)
            self.binding_warning = None
        else:
            self._bindings, self.binding_warning = bindings.load_map(config_path)
        self._binding_lock = threading.Lock()

        # Context the GUI PUSHES in from the main thread. A global hotkey has
        # to act on the same values a button click would use, and a listener
        # thread may never read an AppKit view, so the window pushes instead of
        # the engine pulling. Pushed on every GUI timer tick, so a value the
        # user changes mid-session is always current: a one-time startup copy
        # would pass a naive test and fail in real use.
        self._context = {
            "name": name, "selected": name, "speed": speed, "loops": loops,
            "min_ms": click_min_ms, "max_ms": click_max_ms,
        }
        self._context_lock = threading.Lock()

        # normalized modifiers currently held down, for exact chord matching
        self._held_modifiers = set()
        self._mod_lock = threading.Lock()

        # One activation per completed press cycle, keyed by the trigger that
        # started it. Keyed rather than a single slot because two bindings can
        # share a trigger with different modifiers, and because the release of
        # a key whose press was NOT a hotkey must still be recorded.
        self._active_triggers = {}

        # Chord resolver bookkeeping while recording. A modifier that might
        # still turn into a hotkey is held back here with its ORIGINAL
        # timestamp rather than written straight into the recording, and is
        # either dropped (the chord completed, so it belongs to the tool, not
        # to the user's macro) or inserted back in time order the moment the
        # chord becomes impossible.
        self._pending_mods = []
        self._suppress_mod_release = set()

        # Test Input evidence. Listener threads only ever bump these numbers,
        # the main thread renders them, so no view is ever touched off main.
        self._input_seq = 0
        self._last_input = None
        self._input_lock = threading.Lock()

        # Hotkey capture for the editor. While a capture is armed, no hotkey
        # fires and nothing is recorded: the next key becomes the new binding.
        self._capture_active = False
        self._captured = None
        self._capture_lock = threading.Lock()

        self.raw_tap = None
        self.speed = speed
        self.loops = loops
        self.click_min_ms = click_min_ms
        self.click_max_ms = click_max_ms
        self.verbose = verbose

        self.mouse_ctl = mouse.Controller()
        self.kbd_ctl = keyboard.Controller()

        self.state = "idle"          # idle | recording | playing | autoclicking
        self.events = []
        self.record_start = 0.0
        self.loop_current = 0
        self.loop_total = 0
        self.click_count = 0
        # A config warning has to SURVIVE. Setting it on the status field at
        # build time was not enough: the window's own timer overwrote it on the
        # next tick, 150 ms later, so a person with a broken config saw a
        # perfectly calm "Idle" and hotkeys they never chose. Parking it in
        # last_message means status() keeps showing it until something else
        # genuinely happens.
        self.last_message = self.binding_warning or ""
        self.worker = None
        self.stop_flag = threading.Event()

        # Events we synthesised ourselves, so the listener can ignore them and
        # a replayed keystroke can never toggle this tool.
        self._synthetic = Counter()
        self._synth_lock = threading.Lock()

        self.kbd_listener = None
        self.mouse_listener = None

    # -- bindings and context --

    def binding_map(self):
        with self._binding_lock:
            return {a: dict(b) for a, b in self._bindings.items()}

    def set_binding_map(self, mapping):
        """Atomic swap. Raises BindingError without touching the live map."""
        validated = bindings.validate_map(mapping)
        with self._binding_lock:
            self._bindings = validated
        return True

    def binding_label(self, action):
        with self._binding_lock:
            return bindings.format_binding(self._bindings[action])

    def set_context(self, **values):
        """Called from the GUI main thread only. Cheap, idempotent."""
        with self._context_lock:
            for k, v in values.items():
                if v is not None:
                    self._context[k] = v

    def context(self, field):
        with self._context_lock:
            return self._context.get(field)

    def held_modifiers(self):
        with self._mod_lock:
            return frozenset(self._held_modifiers)

    def match_action(self, key):
        """
        Which action does this key press activate right now, if any?

        EXACT normalized modifier-set equality. An extra held modifier means no
        match, so plain F6 and Control+F6 are genuinely distinct bindings.
        """
        held = self.held_modifiers()
        with self._binding_lock:
            items = list(self._bindings.items())
        for action, binding in items:
            if frozenset(binding["modifiers"]) != held:
                continue
            if bindings.trigger_matches_key(binding["trigger"], key):
                return action
        return None

    def match_raw_action(self, key_type):
        """Same exact-equality rule for a decoded media or brightness key."""
        held = self.held_modifiers()
        with self._binding_lock:
            items = list(self._bindings.items())
        for action, binding in items:
            if frozenset(binding["modifiers"]) != held:
                continue
            if bindings.trigger_matches_raw(binding["trigger"], key_type):
                return action
        return None

    def has_raw_binding(self):
        with self._binding_lock:
            return any(b["trigger"]["kind"] == "raw"
                       for b in self._bindings.values())

    # -- Test Input evidence, written by listeners, read by the main thread --

    def note_input(self, kind, detail):
        """
        Record that the listener layer saw something. kind is one of
        "ordinary", "raw_mapped" or "raw_unmapped". The sequence number only
        ever increases, which is what lets a Test Input prompt ignore every
        event that arrived before it opened.
        """
        with self._input_lock:
            self._input_seq += 1
            self._last_input = {"seq": self._input_seq, "kind": kind,
                                "detail": detail, "mono": time.monotonic()}

    def input_seq(self):
        with self._input_lock:
            return self._input_seq

    def last_input(self):
        with self._input_lock:
            return dict(self._last_input) if self._last_input else None

    # -- hotkey capture for the editor --

    def begin_capture(self):
        with self._capture_lock:
            self._capture_active = True
            self._captured = None

    def cancel_capture(self):
        with self._capture_lock:
            self._capture_active = False
            self._captured = None

    def capture_active(self):
        with self._capture_lock:
            return self._capture_active

    def take_capture(self):
        """
        Main thread polls this. Returns (binding, error) once and then None,
        so a capture can never be applied twice.
        """
        with self._capture_lock:
            out = self._captured
            if out is not None:
                self._captured = None
                self._capture_active = False
            return out

    def _offer_capture(self, binding, error):
        with self._capture_lock:
            if self._capture_active and self._captured is None:
                self._captured = (binding, error)
                return True
        return False

    # -- chord resolver bookkeeping, recording side --

    def _chord_still_possible(self):
        """
        Could the modifiers held right now still become one of the bindings?

        If yes, a modifier press is held back instead of being written into the
        recording, because a recognized hotkey must leave no trace in the
        user's macro. If no, there is nothing to wait for.
        """
        held = self.held_modifiers()
        if not held:
            return False
        with self._binding_lock:
            sets = [frozenset(b["modifiers"]) for b in self._bindings.values()]
        return any(mods and held <= mods for mods in sets)

    def _flush_pending(self):
        """
        The chord became impossible. Put every held-back modifier back into the
        recording AT ITS ORIGINAL TIMESTAMP, in time order, which means before
        any ordinary event that happened while we were waiting. Playback
        replays file order, so inserting at the end would replay the modifier
        after the key it was meant to modify.
        """
        if not self._pending_mods:
            return
        for _, event in self._pending_mods:
            index = len(self.events)
            while index > 0 and self.events[index - 1]["t"] > event["t"]:
                index -= 1
            self.events.insert(index, event)
        self._pending_mods = []

    def _discard_pending(self):
        """The chord completed, so its modifiers belong to the tool, not the
        recording."""
        self._pending_mods = []

    def _stamp(self, event):
        event["t"] = round(time.perf_counter() - self.record_start, 6)
        return event

    # -- status, safe to read from another thread --

    def status(self):
        if self.state == "recording":
            return "Recording '%s': %d events" % (self.name, len(self.events))
        if self.state == "playing":
            if self.loop_total == 0:
                return ("Playing '%s': loop %d, repeating forever"
                        % (self.name, self.loop_current))
            return ("Playing '%s': loop %d of %d"
                    % (self.name, self.loop_current, self.loop_total))
        if self.state == "autoclicking":
            return ("Autoclicking: %d clicks, %d to %d ms apart"
                    % (self.click_count, self.click_min_ms, self.click_max_ms))
        if self.last_message:
            return "Idle. " + self.last_message
        return "Idle"

    def say(self, message):
        self.last_message = message
        if self.verbose:
            print("\n" + message)

    # -- self-injected event bookkeeping --

    def note_synthetic(self, token):
        with self._synth_lock:
            self._synthetic[token] += 1

    def consume_synthetic(self, token):
        with self._synth_lock:
            if self._synthetic[token] > 0:
                self._synthetic[token] -= 1
                return True
        return False

    def clear_synthetic(self):
        with self._synth_lock:
            self._synthetic.clear()

    # -- listener callbacks --

    @staticmethod
    def _modifier_token(key):
        if isinstance(key, keyboard.Key):
            return bindings.MODIFIER_ALIASES.get(key.name)
        return None

    def on_key_press(self, key):
        # Our own injected key. Never a hotkey, never recorded.
        if self.consume_synthetic(key_token(key)):
            return

        token = self._modifier_token(key)
        self.note_input("ordinary", key_description(key))

        if self.capture_active():
            # The editor is waiting for a key. Nothing fires and nothing is
            # recorded until the capture completes or is cancelled.
            if token is not None:
                with self._mod_lock:
                    self._held_modifiers.add(token)
                return
            try:
                trigger = bindings.trigger_from_key(key)
            except bindings.BindingError as exc:
                self._offer_capture(None, str(exc))
                return
            self._offer_capture(
                bindings.make_binding(self.held_modifiers(), trigger), None)
            return

        if token is not None:
            with self._mod_lock:
                self._held_modifiers.add(token)
            if self.state == "recording":
                event = self._stamp({"type": "key", "pressed": True,
                                     **key_to_json(key)})
                if self._chord_still_possible():
                    # might yet become a hotkey, so hold it back
                    self._pending_mods.append((token, event))
                else:
                    self._flush_pending()
                    self.events.append(event)
            return

        action = self.match_action(key)
        key_id = key_token(key)
        if action is not None:
            if key_id in self._active_triggers:
                return                    # auto repeat, one activation per cycle
            self._active_triggers[key_id] = action
            if self.state == "recording":
                self._discard_pending()
            self._suppress_mod_release |= set(self.held_modifiers())
            self.handle_hotkey(action)
            return                        # a recognized hotkey is never stored

        # Not a hotkey, so any chord that was building is now impossible.
        if self.state == "recording":
            self._flush_pending()
            self.append({"type": "key", "pressed": True, **key_to_json(key)})

    def on_key_release(self, key):
        if self.consume_synthetic(key_token(key)):
            return

        token = self._modifier_token(key)

        if self.capture_active():
            if token is not None:
                with self._mod_lock:
                    self._held_modifiers.discard(token)
            return

        if token is not None:
            with self._mod_lock:
                self._held_modifiers.discard(token)
            if token in self._suppress_mod_release:
                # its press was swallowed as part of a recognized hotkey, so
                # the release goes with it
                self._suppress_mod_release.discard(token)
                return
            if self.state == "recording":
                # a modifier let go without completing a chord: it was an
                # ordinary keystroke all along
                self._flush_pending()
                self.append({"type": "key", "pressed": False,
                             **key_to_json(key)})
            return

        key_id = key_token(key)
        if key_id in self._active_triggers:
            del self._active_triggers[key_id]
            return         # release of a trigger whose press we swallowed
        if self.state == "recording":
            self.append({"type": "key", "pressed": False, **key_to_json(key)})

    def on_raw_key(self, key_type, state, repeat):
        """
        A decoded NSSystemDefined media or brightness key.

        Called on the MAIN thread by rawtap. These events are never written
        into a recording: macro.py cannot replay them, so storing one would
        produce a recording that silently does less than it claims.
        """
        mapped = rawtap.is_mapped_by_pynput(key_type)
        name = bindings.RAW_KEY_NAMES.get(key_type, "raw_%d" % key_type)
        label = name.replace("_", " ") + " (media key)"
        if state == "down" and not repeat and not mapped:
            # pynput cannot deliver this one at all, which is the whole reason
            # this layer exists. Say so, rather than reporting silence.
            self.note_input("raw_unmapped", label)
        elif state == "down" and not repeat:
            self.note_input("raw_mapped", label)

        if self.capture_active():
            if state == "down" and not repeat:
                self._offer_capture(
                    bindings.make_binding(self.held_modifiers(),
                                          bindings.trigger_from_raw(key_type)),
                    None)
            return

        key_id = "raw:%d" % int(key_type)
        if state == "down":
            action = self.match_raw_action(key_type)
            if action is None:
                return
            if key_id in self._active_triggers:
                return
            self._active_triggers[key_id] = action
            if self.state == "recording":
                self._discard_pending()
            self._suppress_mod_release |= set(self.held_modifiers())
            self.handle_hotkey(action)
        elif state == "up":
            self._active_triggers.pop(key_id, None)

    def on_click(self, x, y, button, pressed):
        if self.state == "recording":
            self.append({"type": "click", "x": int(x), "y": int(y),
                         "button": button.name, "pressed": bool(pressed)})

    def on_scroll(self, x, y, dx, dy):
        if self.state == "recording":
            self.append({"type": "scroll", "x": int(x), "y": int(y),
                         "dx": int(dx), "dy": int(dy)})

    def on_move(self, x, y):
        if CAPTURE_MOUSE_MOVES and self.state == "recording":
            self.append({"type": "move", "x": int(x), "y": int(y)})

    def append(self, event):
        event["t"] = round(time.perf_counter() - self.record_start, 6)
        self.events.append(event)

    # -- hotkeys --

    def handle_hotkey(self, action):
        live = self.mode == "all"
        if action == "record" and (live or self.mode == "record"):
            self.toggle_record()
        elif action == "play" and (live or self.mode == "play"):
            self.toggle_play()
        elif action == "autoclick" and (live or self.mode == "autoclick"):
            self.toggle_autoclick()

    # -- record --

    def toggle_record(self):
        if self.state == "recording":
            self.stop_record()
        elif self.state == "idle":
            self.start_record()

    def start_record(self, name=None):
        if self.state != "idle":
            return False
        # A global hotkey arrives with no arguments, so fall back to the
        # context the GUI pushed. Before this existed, a hotkey reached here
        # with self.name still None and silently refused.
        if name is None:
            name = self.context("name")
        if name:
            self.name = name
        if not valid_name(self.name):
            self.say("Cannot record: that name is not allowed. Use letters, "
                     "numbers, dot, dash or underscore.")
            return False
        self.events = []
        self._pending_mods = []
        self.record_start = time.perf_counter()
        self.state = "recording"
        self.say("Recording into '%s'. Press %s again to stop."
                 % (self.name, self.binding_label("record")))
        return True

    def stop_record(self):
        if self.state != "recording":
            return False
        # Anything still held back never became a hotkey, so it was the user's
        # keystroke and belongs in the file.
        self._flush_pending()
        self.state = "idle"
        path = self.save_recording()
        self.say("Stopped. %d events saved to %s" % (len(self.events), path.name))
        return True

    def save_recording(self):
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        path = RECORDINGS_DIR / (self.name + ".json")
        payload = {
            "version": FORMAT_VERSION,
            "name": self.name,
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "screen": screen_size(),
            "capture_moves": CAPTURE_MOUSE_MOVES,
            "events": self.events,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    # -- playback --

    def toggle_play(self):
        if self.state == "playing":
            self.request_stop()
        elif self.state == "idle":
            self.start_play()

    def start_play(self, name=None, speed=None, loops=None):
        if self.state != "idle":
            return False
        # Same context fallback as recording. A hotkey used to arrive with
        # name None and die on None + ".json" inside load_recording.
        if name is None:
            name = self.context("selected") or self.context("name")
        if speed is None:
            speed = self.context("speed")
        if loops is None:
            loops = self.context("loops")
        if name:
            self.name = name
        if speed is not None:
            self.speed = speed
        if loops is not None:
            self.loops = loops
        if not self.name:
            self.say("Nothing to play. Pick a recording in the window first, "
                     "or pass a name on the command line.")
            return False
        try:
            data = load_recording(self.name)
        except Exception as exc:
            self.say("Cannot play: %s" % exc)
            return False
        events = data["events"]
        if not events:
            self.say("Recording '%s' is empty, nothing to play." % self.name)
            return False
        warning = resolution_warning(data)
        if warning:
            self.say(warning)
        self.state = "playing"
        self.loop_current = 0
        self.loop_total = self.loops
        self.stop_flag.clear()
        self.worker = threading.Thread(
            target=self.playback_loop, args=(events,), daemon=True)
        self.worker.start()
        self.say("Playing '%s': %d events, speed %sx, %s. Press %s to stop."
                 % (self.name, len(events), self.speed,
                    "repeating forever" if self.loops == 0
                    else "%d time(s)" % self.loops,
                    self.binding_label("play")))
        return True

    def playback_loop(self, events):
        base = events[0]["t"]
        done = 0
        lead = MOVE_SETTLE_MS / 1000.0
        # Warm the injection path before anything is timed. The FIRST cursor
        # move in a process resolves pyobjc symbols lazily and can take longer
        # than the settle window, which made the first click of a run land on
        # whatever the cursor was on before playback started. Every later click
        # was fine, which is what gave it away.
        try:
            self.mouse_ctl.position = self.mouse_ctl.position
            first = next((e for e in events if "x" in e and "y" in e), None)
            if first is not None and lead:
                # Park on the first target and give it a generous settle. The
                # warm-up above is not enough on its own: the opening click of
                # a run still landed on the previous cursor position often
                # enough to matter, because one lead is a thin margin for the
                # very first move. Later clicks are already parked by the loop.
                self.mouse_ctl.position = (first["x"], first["y"])
                time.sleep(max(lead, 0.05))
        except Exception:
            pass
        try:
            while not self.stop_flag.is_set():
                self.loop_current = done + 1
                # start in the future by one lead, so even the first event
                # gets its settle window without arriving late.
                start = time.perf_counter() + lead
                for ev in events:
                    target = start + (ev["t"] - base) / self.speed
                    if lead and ev["type"] in ("click", "scroll"):
                        # park the cursor early, then press exactly on time
                        if not self.sleep_until(target - lead):
                            return
                        self.mouse_ctl.position = (ev["x"], ev["y"])
                        parked = True
                    else:
                        parked = False
                    if not self.sleep_until(target):
                        return
                    self.inject(ev, parked=parked)
                done += 1
                if self.loops != 0 and done >= self.loops:
                    break
        finally:
            self.clear_synthetic()
            self.state = "idle"
            self.loop_current = 0
            if self.stop_flag.is_set():
                self.say("Stopped after %d loop(s)." % done)
            else:
                self.say("Finished %d loop(s)." % done)

    def sleep_until(self, target):
        """Wait until target. Returns False if stop was requested."""
        while True:
            if self.stop_flag.is_set():
                return False
            remaining = target - time.perf_counter()
            if remaining <= 0:
                return True
            if remaining > SPIN_SECONDS:
                time.sleep(min(remaining - SPIN_SECONDS, STOP_POLL_SECONDS))
            # else: fall through and spin, re-checking stop each pass

    def inject(self, ev, parked=False):
        """
        parked=True means playback_loop already moved the cursor there and
        waited. Do NOT set the position again: re-assigning it immediately
        before the press is exactly what makes macOS stamp the event with the
        old location, which shifts the click back onto the previous target.
        """
        kind = ev["type"]
        if kind == "move":
            self.mouse_ctl.position = (ev["x"], ev["y"])
        elif kind == "click":
            if not parked:
                self.mouse_ctl.position = (ev["x"], ev["y"])
            button = getattr(mouse.Button, ev["button"])
            if ev["pressed"]:
                self.mouse_ctl.press(button)
            else:
                self.mouse_ctl.release(button)
        elif kind == "scroll":
            if not parked:
                self.mouse_ctl.position = (ev["x"], ev["y"])
            self.mouse_ctl.scroll(ev["dx"], ev["dy"])
        elif kind == "key":
            self.note_synthetic(token_from_json(ev))
            key = key_from_json(ev)
            if ev["pressed"]:
                self.kbd_ctl.press(key)
            else:
                self.kbd_ctl.release(key)

    # -- autoclicker --

    def toggle_autoclick(self):
        if self.state == "autoclicking":
            self.request_stop()
        elif self.state == "idle":
            self.start_autoclick()

    def start_autoclick(self, min_ms=None, max_ms=None):
        if self.state != "idle":
            return False
        if min_ms is None:
            min_ms = self.context("min_ms")
        if max_ms is None:
            max_ms = self.context("max_ms")
        if min_ms is not None:
            self.click_min_ms = min_ms
        if max_ms is not None:
            self.click_max_ms = max_ms
        if self.click_min_ms < 0 or self.click_max_ms < self.click_min_ms:
            self.say("Autoclicker needs the min to be 0 or more, and not "
                     "bigger than the max.")
            return False
        self.state = "autoclicking"
        self.click_count = 0
        self.stop_flag.clear()
        self.worker = threading.Thread(target=self.autoclick_loop, daemon=True)
        self.worker.start()
        self.say("Autoclicking at the cursor, %d to %d ms apart. Press %s to stop."
                 % (self.click_min_ms, self.click_max_ms,
                    self.binding_label("autoclick")))
        return True

    def autoclick_loop(self):
        try:
            while not self.stop_flag.is_set():
                self.mouse_ctl.click(mouse.Button.left)
                self.click_count += 1
                delay = random.uniform(self.click_min_ms,
                                       self.click_max_ms) / 1000.0
                if not self.sleep_until(time.perf_counter() + delay):
                    return
        finally:
            self.state = "idle"
            self.say("Stopped after %d clicks." % self.click_count)

    # -- shared --

    def request_stop(self):
        self.stop_flag.set()

    def start_listeners(self):
        """
        Start the input listeners.

        ORDERING IS LOAD BEARING ON macOS. pynput installs a CGEventTap and
        spins a CFRunLoop. If a Tk main loop is created BEFORE this runs, the
        process dies with SIGABRT and no traceback at all. gui.py therefore
        calls this before it creates its Tk root. Measured on macOS 26.4.1
        with Tk 8.5: Tk first aborted 3 of 3 runs, listeners first survived
        every run. Do not reorder these two things.
        """
        warm_up_pyobjc()
        self.kbd_listener = keyboard.Listener(
            on_press=self.on_key_press, on_release=self.on_key_release)
        self.mouse_listener = mouse.Listener(
            on_click=self.on_click, on_scroll=self.on_scroll,
            on_move=self.on_move if CAPTURE_MOUSE_MOVES else None)
        self.kbd_listener.start()
        self.mouse_listener.start()
        for listener in (self.kbd_listener, self.mouse_listener):
            listener.wait()
        dead = [n for n, l in (("keyboard", self.kbd_listener),
                               ("mouse", self.mouse_listener))
                if not l.running]
        if dead:
            raise RuntimeError(
                "the %s listener died on startup, so the hotkeys would not "
                "work. This usually means Input Monitoring is not granted, "
                "or Terminal was not quit and reopened after granting it."
                % " and ".join(dead))

    def start_raw_tap(self):
        """
        Start watching the media and brightness keys pynput cannot see.

        MAIN THREAD ONLY, and only in a process whose main run loop will
        actually run: the tap's callback decodes through AppKit. The GUI
        qualifies. The command line does not, which is why a raw binding there
        gets an honest warning instead of silently doing nothing.
        """
        if self.raw_tap is not None:
            return self.raw_tap.available
        self.raw_tap = rawtap.RawTap(self.on_raw_key)
        return self.raw_tap.start()

    def raw_tap_status(self):
        """(available, reason). reason is None when it is available."""
        if self.raw_tap is None:
            return False, ("the raw key watcher is not running in this "
                           "process, so media and brightness keys cannot "
                           "trigger anything here")
        return self.raw_tap.available, self.raw_tap.reason

    def stop_listeners(self):
        for listener in (self.kbd_listener, self.mouse_listener):
            if listener is not None:
                listener.stop()

    def shutdown(self):
        self.request_stop()
        if self.state == "recording":
            path = self.save_recording()
            self.say("Saved in-progress recording to %s" % path.name)
        self.stop_listeners()
        if self.raw_tap is not None:
            self.raw_tap.stop()

    def run_cli(self):
        """Blocking loop for the command line modes."""
        # flush: the banner IS the command line interface. Piped into a log or
        # read by a supervisor it must appear immediately, not sit in a block
        # buffer until the process happens to exit.
        print(banner(self), flush=True)
        self.start_listeners()
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\nQuitting.")
        finally:
            self.shutdown()


# --- helpers ----------------------------------------------------------------

def key_label(key):
    return key.name.upper() if isinstance(key, keyboard.Key) else str(key)


def banner(tool):
    lines = ["", "macro.py ready. Leave this window open."]
    if tool.binding_warning:
        lines.append("  NOTE: %s" % tool.binding_warning)
    if tool.has_raw_binding():
        lines.append("  NOTE: one of your hotkeys is a media or brightness "
                     "key. Those only work in the window (macro.py gui), not "
                     "on the command line.")
    if tool.mode == "record":
        lines.append("  %s  start / stop recording into '%s'"
                     % (tool.binding_label("record"), tool.name))
    elif tool.mode == "play":
        lines.append("  %s  start / stop playing '%s'"
                     % (tool.binding_label("play"), tool.name))
    else:
        lines.append("  %s  start / stop autoclicker"
                     % tool.binding_label("autoclick"))
    lines.append("  ctrl-c in this window quits.")
    lines.append("")
    return "\n".join(lines)


def resolution_warning(data):
    was = data.get("screen")
    now = screen_size()
    if was and now and was != now:
        return ("WARNING: recorded at %dx%d but the screen is now %dx%d. "
                "Absolute coordinates may land in the wrong place."
                % (was[0], was[1], now[0], now[1]))
    return None


def safe_name_arg(name):
    if not valid_name(name):
        raise argparse.ArgumentTypeError(
            "name may only contain letters, numbers, dot, dash, underscore")
    return name


def cmd_list():
    rows = list_recordings()
    if not rows:
        print("No recordings yet in %s" % RECORDINGS_DIR)
        return
    print("%-24s %8s  %s" % ("NAME", "EVENTS", "CREATED"))
    for name, count, created in rows:
        print("%-24s %8s  %s" % (name, count if count >= 0 else "?", created))


def cmd_hotkeys():
    """
    Read-only. Prints exactly which hotkeys a fresh process would use, and any
    warning about the config file, then exits.

    Deliberately read only: the window is the only place bindings are changed,
    so there is one editing surface to reason about rather than two.
    """
    mapping, warning = bindings.load_map()
    print("Hotkeys this Mac would use right now:")
    for action in bindings.ACTIONS:
        print("  %-10s %s" % (action, bindings.format_binding(mapping[action])))
    print("Config file: %s" % bindings.CONFIG_PATH)
    if warning:
        print("WARNING: %s" % warning)
    else:
        print("WARNING: none")
    return 0


def main(argv=None):
    # Help text is built before a tool exists, so it describes the DEFAULTS.
    # The running tool always reports its own live bindings.
    defaults = bindings.default_map()

    def default_label(action):
        return bindings.format_binding(defaults[action])

    parser = argparse.ArgumentParser(
        description="Record and replay mouse and keyboard on this Mac.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="arm recording, %s toggles it"
                           % default_label("record"))
    p_rec.add_argument("name", type=safe_name_arg, help="name to save under")

    p_play = sub.add_parser("play", help="arm playback, %s toggles it"
                            % default_label("play"))
    p_play.add_argument("name", type=safe_name_arg, help="recording to play")
    p_play.add_argument("--speed", type=float, default=1.0,
                        help="speed multiplier, 2 is twice as fast")
    p_play.add_argument("--loop", type=int, default=1,
                        help="how many times to repeat, 0 means forever")

    p_auto = sub.add_parser("autoclick", help="arm autoclicker, %s toggles it"
                            % default_label("autoclick"))
    p_auto.add_argument("--min", dest="min_ms", type=int, required=True,
                        help="shortest delay between clicks, milliseconds")
    p_auto.add_argument("--max", dest="max_ms", type=int, required=True,
                        help="longest delay between clicks, milliseconds")

    sub.add_parser("list", help="show saved recordings")
    sub.add_parser("hotkeys", help="show the hotkeys this Mac would use now")
    sub.add_parser("gui", help="open the window")

    args = parser.parse_args(argv)

    if args.command == "list":
        cmd_list()
        return 0

    if args.command == "hotkeys":
        return cmd_hotkeys()

    if args.command == "gui":
        import gui
        return gui.run()

    if args.command == "record":
        tool = MacroTool("record", name=args.name)
        tool.set_context(name=args.name, selected=args.name)
    elif args.command == "play":
        if args.speed <= 0:
            parser.error("--speed must be greater than 0")
        if args.loop < 0:
            parser.error("--loop must be 0 or more")
        tool = MacroTool("play", name=args.name,
                         speed=args.speed, loops=args.loop)
        tool.set_context(name=args.name, selected=args.name,
                         speed=args.speed, loops=args.loop)
    else:
        if args.min_ms < 0 or args.max_ms < 0:
            parser.error("--min and --max must be 0 or more")
        if args.min_ms > args.max_ms:
            parser.error("--min must not be greater than --max")
        tool = MacroTool("autoclick",
                         click_min_ms=args.min_ms, click_max_ms=args.max_ms)
        tool.set_context(min_ms=args.min_ms, max_ms=args.max_ms)

    tool.run_cli()
    return 0


if __name__ == "__main__":
    sys.exit(main())
