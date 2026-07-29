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
import random
import re
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from pynput import keyboard, mouse

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

# Milliseconds to wait after moving the cursor before pressing the button.
# 0 is correct for accurate timing and works for normal apps. Some games only
# register a click if the cursor has settled first; if clicks get dropped in
# game, try 8 or 15.
MOVE_SETTLE_MS = 0

# How often the playback loop wakes up to check whether you pressed stop.
# Lower means the stop hotkey reacts faster, at slightly more idle CPU.
STOP_POLL_SECONDS = 0.02

# ===========================================================================
# End of config
# ===========================================================================

TOOL_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = TOOL_DIR / "recordings"
FORMAT_VERSION = 1
SPIN_SECONDS = 0.0015  # final busy-wait window, keeps timing tight
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

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
                 click_min_ms=80, click_max_ms=140, verbose=True):
        self.mode = mode
        self.name = name
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
        self.last_message = ""
        self.worker = None
        self.stop_flag = threading.Event()

        # Events we synthesised ourselves, so the listener can ignore them and
        # a replayed keystroke can never toggle this tool.
        self._synthetic = Counter()
        self._synth_lock = threading.Lock()

        self.kbd_listener = None
        self.mouse_listener = None

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

    def on_key_press(self, key):
        # Our own injected key. Never a hotkey, never recorded.
        if self.consume_synthetic(key_token(key)):
            return
        if key in HOTKEYS:
            self.handle_hotkey(key)
            return                       # hotkeys never land in a recording
        if self.state == "recording":
            self.append({"type": "key", "pressed": True, **key_to_json(key)})

    def on_key_release(self, key):
        if self.consume_synthetic(key_token(key)):
            return
        if key in HOTKEYS:
            return
        if self.state == "recording":
            self.append({"type": "key", "pressed": False, **key_to_json(key)})

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

    def handle_hotkey(self, key):
        live = self.mode == "all"
        if key == HOTKEY_RECORD and (live or self.mode == "record"):
            self.toggle_record()
        elif key == HOTKEY_PLAY and (live or self.mode == "play"):
            self.toggle_play()
        elif key == HOTKEY_AUTOCLICK and (live or self.mode == "autoclick"):
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
        if name:
            self.name = name
        if not valid_name(self.name):
            self.say("Cannot record: that name is not allowed. Use letters, "
                     "numbers, dot, dash or underscore.")
            return False
        self.events = []
        self.record_start = time.perf_counter()
        self.state = "recording"
        self.say("Recording into '%s'. Press %s again to stop."
                 % (self.name, key_label(HOTKEY_RECORD)))
        return True

    def stop_record(self):
        if self.state != "recording":
            return False
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
        if name:
            self.name = name
        if speed is not None:
            self.speed = speed
        if loops is not None:
            self.loops = loops
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
                    key_label(HOTKEY_PLAY)))
        return True

    def playback_loop(self, events):
        base = events[0]["t"]
        done = 0
        try:
            while not self.stop_flag.is_set():
                self.loop_current = done + 1
                start = time.perf_counter()
                for ev in events:
                    target = start + (ev["t"] - base) / self.speed
                    if not self.sleep_until(target):
                        return
                    self.inject(ev)
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

    def inject(self, ev):
        kind = ev["type"]
        if kind == "move":
            self.mouse_ctl.position = (ev["x"], ev["y"])
        elif kind == "click":
            self.mouse_ctl.position = (ev["x"], ev["y"])
            if MOVE_SETTLE_MS:
                time.sleep(MOVE_SETTLE_MS / 1000.0)
            button = getattr(mouse.Button, ev["button"])
            if ev["pressed"]:
                self.mouse_ctl.press(button)
            else:
                self.mouse_ctl.release(button)
        elif kind == "scroll":
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
                    key_label(HOTKEY_AUTOCLICK)))
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

    def run_cli(self):
        """Blocking loop for the command line modes."""
        print(banner(self))
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
    if tool.mode == "record":
        lines.append("  %s  start / stop recording into '%s'"
                     % (key_label(HOTKEY_RECORD), tool.name))
    elif tool.mode == "play":
        lines.append("  %s  start / stop playing '%s'"
                     % (key_label(HOTKEY_PLAY), tool.name))
    else:
        lines.append("  %s  start / stop autoclicker"
                     % key_label(HOTKEY_AUTOCLICK))
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record and replay mouse and keyboard on this Mac.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="arm recording, %s toggles it"
                           % key_label(HOTKEY_RECORD))
    p_rec.add_argument("name", type=safe_name_arg, help="name to save under")

    p_play = sub.add_parser("play", help="arm playback, %s toggles it"
                            % key_label(HOTKEY_PLAY))
    p_play.add_argument("name", type=safe_name_arg, help="recording to play")
    p_play.add_argument("--speed", type=float, default=1.0,
                        help="speed multiplier, 2 is twice as fast")
    p_play.add_argument("--loop", type=int, default=1,
                        help="how many times to repeat, 0 means forever")

    p_auto = sub.add_parser("autoclick", help="arm autoclicker, %s toggles it"
                            % key_label(HOTKEY_AUTOCLICK))
    p_auto.add_argument("--min", dest="min_ms", type=int, required=True,
                        help="shortest delay between clicks, milliseconds")
    p_auto.add_argument("--max", dest="max_ms", type=int, required=True,
                        help="longest delay between clicks, milliseconds")

    sub.add_parser("list", help="show saved recordings")
    sub.add_parser("gui", help="open the window")

    args = parser.parse_args(argv)

    if args.command == "list":
        cmd_list()
        return 0

    if args.command == "gui":
        import gui
        return gui.run()

    if args.command == "record":
        tool = MacroTool("record", name=args.name)
    elif args.command == "play":
        if args.speed <= 0:
            parser.error("--speed must be greater than 0")
        if args.loop < 0:
            parser.error("--loop must be 0 or more")
        tool = MacroTool("play", name=args.name,
                         speed=args.speed, loops=args.loop)
    else:
        if args.min_ms < 0 or args.max_ms < 0:
            parser.error("--min and --max must be 0 or more")
        if args.min_ms > args.max_ms:
            parser.error("--min must not be greater than --max")
        tool = MacroTool("autoclick",
                         click_min_ms=args.min_ms, click_max_ms=args.max_ms)

    tool.run_cli()
    return 0


if __name__ == "__main__":
    sys.exit(main())
