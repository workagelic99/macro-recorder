#!/usr/bin/env python3
"""
rawtap.py - see the top-row keys that pynput cannot see.

Measured on this Mac on 2026-07-30, with the keyboard's own top row and no fn
held: F7 arrives as an NSSystemDefined event carrying decoded key type 20
(REWIND) and pynput delivers NOTHING for it, because the installed backend
maps only seven media key types. A hotkey bound to a key the callback layer
never receives is dead no matter how correct the rest of the tool is, so this
module adds a second, passive layer that watches those events directly.

Two rules this file exists to obey.

  LISTEN ONLY. The tap is created with kCGEventTapOptionListenOnly, so it has
  no power to swallow or alter anything. An intercept tap that ever forgot to
  return an event would eat that key system wide, for every app. Every code
  path here still hands the original event back, which costs nothing and means
  a future change to an active tap cannot silently break the machine.

  DECODE, NEVER MATCH THE RAW VALUE. One integer, data1, carries the key type,
  the press or release state, and the auto repeat bit in different bits of the
  same field. Matching on data1 itself would make a press and a release look
  like two different keys. decode_system pulls the three apart and callers
  match on the decoded key type alone.

MAIN THREAD ONLY. NSEvent.eventWithCGEvent_ is an AppKit call, so the tap is
added to the main run loop and its callback runs there. Nothing in this file
may be started from a worker thread.
"""

import Quartz
from AppKit import NSEvent
from pynput import keyboard

# CGEvent type 14 is NSSystemDefined. There is no Quartz constant for it.
NSSYSTEMDEFINED = 14

# Measured subtype for the media and brightness keys on this hardware, and the
# same subtype the installed pynput backend filters on. Anything else with
# type 14 is a different kind of system event and is ignored.
MEDIA_KEYS_SUBTYPE = 8


def pynput_mapped_key_types():
    """
    Which NSSystemDefined key types the INSTALLED pynput can deliver.

    Read off the installed backend rather than hardcoded, so upgrading pynput
    cannot leave a stale list here claiming a key is unmappable when it is not.
    Measured today this returns 0, 1, 7, 14, 16, 17 and 18, which is why the
    REWIND that F7 emits reaches nothing.
    """
    out = {}
    for key in keyboard.Key:
        value = key.value
        if getattr(value, "_is_media", False):
            try:
                out[int(value.vk)] = key.name
            except (TypeError, ValueError):
                continue
    return out


PYNPUT_MAPPED = pynput_mapped_key_types()


def decode_system(data1):
    """
    Split an NSSystemDefined data1 field into its three separate facts.

    Returns (key_type, state, repeat) where state is "down", "up", or a hex
    string for a state this decoder has not seen before. Never raises.
    """
    value = int(data1)
    key_type = (value & 0xFFFF0000) >> 16
    key_flags = value & 0x0000FFFF
    key_state = (key_flags & 0xFF00) >> 8
    repeat = bool(key_flags & 0x1)
    state = {0x0A: "down", 0x0B: "up"}.get(key_state, "0x%02X" % key_state)
    return key_type, state, repeat


def is_mapped_by_pynput(key_type):
    return int(key_type) in PYNPUT_MAPPED


def make_system_event(key_type, state_down, repeat=False):
    """
    Build a real NSSystemDefined media key event, for tests and for the
    Test Input proof. Returns an NSEvent, or None if AppKit refuses.

    data1 is assembled the same way the system assembles it, so an event built
    here is indistinguishable from a physical one at the tap.
    """
    key_state = 0x0A if state_down else 0x0B
    data1 = (int(key_type) << 16) | (key_state << 8) | (1 if repeat else 0)
    try:
        return NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            NSSYSTEMDEFINED, (0, 0), 0xA00, 0, 0, None,
            MEDIA_KEYS_SUBTYPE, data1, -1)
    except Exception:
        return None


def post_system_event(key_type, state_down, repeat=False):
    """Post one synthetic media key event. Returns True if it went out."""
    event = make_system_event(key_type, state_down, repeat)
    if event is None:
        return False
    cg = event.CGEvent()
    if cg is None:
        return False
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)
    return True


class RawTap:
    """
    A passive watcher for NSSystemDefined media key events.

    start() must be called on the MAIN thread and needs a run loop that will
    actually be run: the GUI's NSApplication provides one. If the tap cannot be
    created the object stays unavailable with a plain reason string instead of
    raising, because a missing permission must degrade the tool, never crash a
    cold start.
    """

    def __init__(self, on_event):
        self.on_event = on_event
        self.available = False
        self.reason = "the raw key watcher has not been started"
        self._tap = None
        self._source = None

    def start(self):
        try:
            mask = Quartz.CGEventMaskBit(NSSYSTEMDEFINED)
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                mask,
                self._callback,
                None)
        except Exception as exc:
            self.reason = ("the raw key watcher could not start (%s), so media "
                           "and brightness keys cannot be used as hotkeys"
                           % exc)
            return False
        if not tap:
            self.reason = ("the raw key watcher could not start, so media and "
                           "brightness keys cannot be used as hotkeys. This "
                           "usually means Accessibility is not granted to the "
                           "app you launched from.")
            return False
        self._tap = tap
        self._source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), self._source,
                                  Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self.available = True
        self.reason = None
        return True

    def _callback(self, proxy, etype, event, refcon):
        try:
            if etype == NSSYSTEMDEFINED:
                ns = NSEvent.eventWithCGEvent_(event)
                if ns is not None and ns.subtype() == MEDIA_KEYS_SUBTYPE:
                    key_type, state, repeat = decode_system(ns.data1())
                    self.on_event(key_type, state, repeat)
            elif etype in (Quartz.kCGEventTapDisabledByTimeout,
                           Quartz.kCGEventTapDisabledByUserInput):
                # macOS disables a slow tap. Re-arm rather than going deaf.
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
        except Exception:
            pass
        # listen only, so this return is belt and braces. It stays here so a
        # later change to an active tap cannot swallow a key by omission.
        return event

    def stop(self):
        if self._tap is not None:
            try:
                Quartz.CGEventTapEnable(self._tap, False)
            except Exception:
                pass
        if self._source is not None:
            try:
                Quartz.CFRunLoopRemoveSource(Quartz.CFRunLoopGetCurrent(),
                                             self._source,
                                             Quartz.kCFRunLoopCommonModes)
            except Exception:
                pass
        self._tap = None
        self._source = None
        self.available = False
        self.reason = "the raw key watcher was stopped"
