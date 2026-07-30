#!/usr/bin/env python3
"""
bindings.py - one canonical model for what a hotkey IS.

Everything that needs to talk about a hotkey goes through here: capture in the
GUI, serialization to the config file, live matching in the listener, the
labels shown in the window, CLI help, and the README examples. One model, one
label formatter, so an edited binding can never display as one thing and match
as another.

Trigger identity rules, deliberately chosen:

  printable keys are PHYSICAL. The identity is the virtual key code, and the
  character is a label only. Holding Shift or Option changes the character the
  keyboard emits but not which physical key was struck, so a binding on the
  A key keeps working when the emitted character becomes "A" or a dead-key
  accent.

  named keys (f6, escape, tab...) are identified by their pynput name.

  raw system keys, the ones the top row can emit as NSSystemDefined events,
  live in their OWN namespace keyed by decoded semantic key type. Matching a
  raw key on the opaque data1 value would be wrong because press, release and
  repeat share bits in that field, so the decoded type is the identity and the
  state is handled separately.

Fn / Globe is never a chord modifier. pynput has no Fn in its key enum at all,
so it cannot be observed reliably, and a binding that silently depended on it
would be unmatchable.
"""

import json
import os
import tempfile
from pathlib import Path

from pynput import keyboard

SCHEMA_VERSION = 1

# Your saved hotkeys. MACRO_CONFIG_PATH overrides the location, which is how
# every test runs against a throwaway file: no proof in this project is ever
# allowed to read, write or create your real one.
CONFIG_PATH = Path(os.environ.get("MACRO_CONFIG_PATH")
                   or os.path.expanduser("~/.macro-recorder.json"))
ACTIONS = ("record", "play", "autoclick")

# Left and right variants collapse to one token. An extra held modifier means
# no match, so these have to normalize identically or bindings become
# position-dependent by accident.
MODIFIER_ALIASES = {
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
}
MODIFIER_ORDER = ("ctrl", "alt", "shift", "cmd")
MODIFIER_GLYPH = {"ctrl": "Control", "alt": "Option", "shift": "Shift",
                  "cmd": "Command"}

# Decoded NSSystemDefined key types, IOKit NX_KEYTYPE_* numbering. Canonical
# names live here so a saved raw binding reloads as itself and never as
# "unknown". UNVERIFIED beyond this hardware: which of these a given keyboard
# actually emits varies by model, per Apple's own documentation.
RAW_KEY_NAMES = {
    0: "sound_up", 1: "sound_down", 2: "brightness_up", 3: "brightness_down",
    4: "caps_lock", 5: "help", 6: "power", 7: "mute", 10: "num_lock",
    11: "contrast_up", 12: "contrast_down", 13: "launch_panel", 14: "eject",
    15: "vidmirror", 16: "play", 17: "next", 18: "previous", 19: "fast",
    20: "rewind", 21: "illumination_up", 22: "illumination_down",
    23: "illumination_toggle",
}
RAW_KEY_TYPES = {v: k for k, v in RAW_KEY_NAMES.items()}


class BindingError(ValueError):
    """A config or edit that cannot be honored, with a human message."""


# --------------------------------------------------------------------------
# triggers
# --------------------------------------------------------------------------

def trigger_from_key(key):
    """A pynput key from a listener callback to a canonical trigger dict."""
    if isinstance(key, keyboard.Key):
        return {"kind": "named", "name": key.name}
    vk = getattr(key, "vk", None)
    if vk is None:
        raise BindingError("that key reports no identity macOS can match on")
    label = getattr(key, "char", None)
    out = {"kind": "physical", "vk": int(vk)}
    if label and label.isprintable() and len(label) == 1:
        out["label"] = label
    return out


def trigger_from_raw(key_type):
    """A decoded NSSystemDefined key type to a canonical trigger dict."""
    key_type = int(key_type)
    return {"kind": "raw", "name": RAW_KEY_NAMES.get(key_type,
                                                     "raw_%d" % key_type),
            "key_type": key_type}


def normalize_trigger(trigger):
    if not isinstance(trigger, dict):
        raise BindingError("a trigger must be an object")
    kind = trigger.get("kind")
    if kind == "named":
        name = trigger.get("name")
        if not isinstance(name, str) or not hasattr(keyboard.Key, name):
            raise BindingError("unknown key name %r" % (name,))
        return {"kind": "named", "name": name}
    if kind == "physical":
        try:
            vk = int(trigger["vk"])
        except (KeyError, TypeError, ValueError):
            raise BindingError("a physical trigger needs a numeric vk")
        out = {"kind": "physical", "vk": vk}
        label = trigger.get("label")
        if isinstance(label, str) and len(label) == 1:
            out["label"] = label
        return out
    if kind == "raw":
        try:
            key_type = int(trigger["key_type"])
        except (KeyError, TypeError, ValueError):
            raise BindingError("a raw trigger needs a numeric key_type")
        return {"kind": "raw", "key_type": key_type,
                "name": RAW_KEY_NAMES.get(key_type, "raw_%d" % key_type)}
    raise BindingError("unknown trigger kind %r" % (kind,))


def trigger_matches_key(trigger, key):
    """Does this pynput key satisfy this trigger? Identity only, no modifiers."""
    if trigger["kind"] == "named":
        return isinstance(key, keyboard.Key) and key.name == trigger["name"]
    if trigger["kind"] == "physical":
        if isinstance(key, keyboard.Key):
            # a named key can still carry a vk, compare on it so a binding
            # captured as physical still matches
            return getattr(key.value, "vk", None) == trigger["vk"]
        return getattr(key, "vk", None) == trigger["vk"]
    return False


def trigger_matches_raw(trigger, key_type):
    return trigger["kind"] == "raw" and trigger["key_type"] == int(key_type)


def trigger_label(trigger):
    kind = trigger["kind"]
    if kind == "named":
        return trigger["name"].upper()
    if kind == "physical":
        label = trigger.get("label")
        return label.upper() if label else "key %d" % trigger["vk"]
    name = trigger.get("name", "raw")
    return name.replace("_", " ") + " (media key)"


# --------------------------------------------------------------------------
# modifiers and bindings
# --------------------------------------------------------------------------

def normalize_modifier(name):
    if isinstance(name, keyboard.Key):
        name = name.name
    token = MODIFIER_ALIASES.get(str(name))
    if token is None:
        raise BindingError("%r cannot be used as a modifier" % (name,))
    return token


def normalize_modifiers(names):
    return frozenset(normalize_modifier(n) for n in (names or ()))


def make_binding(modifiers, trigger):
    return {"modifiers": sorted(normalize_modifiers(modifiers),
                                key=MODIFIER_ORDER.index),
            "trigger": normalize_trigger(trigger)}


def normalize_binding(binding):
    if not isinstance(binding, dict):
        raise BindingError("a binding must be an object")
    return make_binding(binding.get("modifiers"), binding.get("trigger"))


def binding_key(binding):
    """Hashable identity, used to detect duplicates across actions."""
    t = binding["trigger"]
    if t["kind"] == "named":
        tid = ("named", t["name"])
    elif t["kind"] == "physical":
        tid = ("physical", t["vk"])
    else:
        tid = ("raw", t["key_type"])
    return (frozenset(binding["modifiers"]), tid)


def format_binding(binding):
    """THE label formatter. Engine, GUI, CLI help and README all use this."""
    parts = [MODIFIER_GLYPH[m] for m in
             sorted(normalize_modifiers(binding["modifiers"]),
                    key=MODIFIER_ORDER.index)]
    parts.append(trigger_label(binding["trigger"]))
    return " + ".join(parts)


# --------------------------------------------------------------------------
# defaults
# --------------------------------------------------------------------------

def default_map():
    """
    Built-in defaults.

    These are PROVISIONAL until the physical measurement says whether the keys
    are viable on the actual keyboard. They are kept as the starting point
    because they drive the working CLI surface and its documented commands.
    """
    return {
        "record": make_binding([], {"kind": "named", "name": "f6"}),
        "play": make_binding([], {"kind": "named", "name": "f7"}),
        "autoclick": make_binding([], {"kind": "named", "name": "f8"}),
    }


def validate_map(mapping):
    """Normalize a whole action map and reject duplicates across actions."""
    if not isinstance(mapping, dict):
        raise BindingError("bindings must be an object")
    out = {}
    for action in ACTIONS:
        if action not in mapping:
            raise BindingError("no binding for %r" % action)
        out[action] = normalize_binding(mapping[action])
    unknown = [a for a in mapping if a not in ACTIONS]
    if unknown:
        raise BindingError("unknown action(s): %s" % ", ".join(sorted(unknown)))
    seen = {}
    for action, binding in out.items():
        k = binding_key(binding)
        if k in seen:
            raise BindingError(
                "%s and %s cannot share the same binding (%s)"
                % (seen[k], action, format_binding(binding)))
        seen[k] = action
    return out


# --------------------------------------------------------------------------
# config file
# --------------------------------------------------------------------------

def load_map(path=None):
    """
    Returns (mapping, warning). Never raises for a bad file, never crashes a
    cold start: an unusable config falls back to the built-in defaults and the
    caller is handed a warning string to show.
    """
    path = Path(path) if path is not None else CONFIG_PATH
    if not path.exists():
        return default_map(), None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return default_map(), ("Could not read %s (%s). Using the built-in "
                               "default hotkeys." % (path, exc.strerror or exc))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return default_map(), ("%s is not valid JSON (%s). Using the built-in "
                               "default hotkeys." % (path, exc))
    if not isinstance(data, dict):
        return default_map(), ("%s does not contain a settings object. Using "
                               "the built-in default hotkeys." % path)
    version = data.get("version")
    if version != SCHEMA_VERSION:
        return default_map(), ("%s was written by a different version of this "
                               "tool (found %r, expected %d). Using the "
                               "built-in default hotkeys."
                               % (path, version, SCHEMA_VERSION))
    try:
        return validate_map(data.get("bindings")), None
    except BindingError as exc:
        return default_map(), ("%s has a hotkey problem: %s. Using the "
                               "built-in default hotkeys." % (path, exc))


def save_map(mapping, path=None, _replace=os.replace):
    """
    Write atomically: a sibling temp file in the SAME directory, then one
    replace. Same directory matters, a cross-device rename is not atomic.

    Returns (ok, warning). On any failure the previous file is left exactly as
    it was, byte for byte, and the temp file is cleaned up.
    """
    path = Path(path) if path is not None else CONFIG_PATH
    mapping = validate_map(mapping)
    payload = json.dumps({"version": SCHEMA_VERSION,
                          "bindings": mapping}, indent=2) + "\n"
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        handed_over = False
        try:
            fh = os.fdopen(fd, "w", encoding="utf-8")
            handed_over = True      # fh owns fd now, closing it closes fd
            with fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            if not handed_over:
                os.close(fd)
            raise
        _replace(tmp_name, str(path))
        tmp_name = None
        return True, None
    except Exception as exc:
        return False, ("Could not save hotkeys to %s (%s). Your previous "
                       "settings are unchanged." % (path, exc))
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
