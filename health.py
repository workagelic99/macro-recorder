#!/usr/bin/env python3
"""
health.py - what this tool can actually do right now, and under whose name.

macOS does not grant input permissions to a Python file. It grants them to the
APP that is responsible for the process, which for a script run from a terminal
is the terminal app, and for a double clicked .command file is Terminal
specifically. That is why a tool can work perfectly for the person who built it
and do nothing at all for the same person launching it a different way: the
permission follows the launcher, not the script.

So this module answers two questions with live evidence rather than assumption:

  is each permission granted in THIS process, right now
  which app name the person has to look for in System Settings to grant it

Nothing here ever blocks or prompts. A missing permission is reported and the
tool carries on with whatever still works.
"""

import os
import subprocess

import Quartz

try:
    import HIServices
except Exception:                                   # pragma: no cover
    HIServices = None


SETTINGS_ROOT = "System Settings > Privacy & Security"


def _run(argv, timeout=4):
    """A short, stdin-closed subprocess. Returns "" on any trouble."""
    try:
        out = subprocess.run(argv, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=timeout, text=True)
        return out.stdout.strip()
    except Exception:
        return ""


def parent_chain(max_depth=12):
    """
    [(pid, command name, executable path), ...] from this process upward.

    Recorded from the run itself. A description of how the tool was probably
    launched is worth nothing next to the actual chain.
    """
    chain = []
    pid = os.getpid()
    for _ in range(max_depth):
        name = _run(["ps", "-o", "comm=", "-p", str(pid)])
        chain.append((pid, os.path.basename(name) if name else "unknown", name))
        parent = _run(["ps", "-o", "ppid=", "-p", str(pid)])
        if not parent or not parent.strip().isdigit():
            break
        parent = int(parent.strip())
        if parent <= 1 or parent == pid:
            break
        pid = parent
    return chain


def outer_bundle_name(path):
    """
    The OUTERMOST app bundle a path sits inside, or None.

    Outermost matters twice over. "Code Helper.app" is nested inside
    "Visual Studio Code.app" and it is the outer name that appears in System
    Settings, and Python itself ships inside a Python.app stub which is never
    what the person is looking for in that list.
    """
    if not path or ".app/" not in path:
        return None
    return os.path.basename(path.split(".app/")[0])


def responsible_app():
    """
    The app name macOS attributes this process's permissions to, which is the
    name that appears in the System Settings list.

    macOS credits the permission to the app RESPONSIBLE for the process, which
    is the launcher at the top of the chain, not the interpreter at the bottom.
    So this walks past our own process and takes the outermost bundled
    ANCESTOR: Terminal for a double clicked .command, Visual Studio Code for a
    shell inside the editor.

    Returns (app name, executable path), or (None, None) when nothing in the
    chain is an app, which is what a bare login shell over ssh looks like.
    """
    found = (None, None)
    for index, (_, _, path) in enumerate(parent_chain()):
        if index == 0:
            continue                    # ourselves, never the responsible app
        name = outer_bundle_name(path)
        if name:
            found = (name, path)        # keep going, outermost ancestor wins
    return found


def _accessibility():
    if HIServices is None:
        return None
    try:
        return bool(HIServices.AXIsProcessTrusted())
    except Exception:
        return None


def _preflight(name):
    fn = getattr(Quartz, name, None)
    if fn is None:
        return None
    try:
        return bool(fn())
    except Exception:
        return None


def permissions():
    """
    [(label, granted, settings pane, what it is for), ...]

    granted is True, False, or None when this build of macOS cannot be asked.
    """
    return [
        ("Accessibility", _accessibility(),
         SETTINGS_ROOT + " > Accessibility",
         "watching and replaying input"),
        ("Input Monitoring", _preflight("CGPreflightListenEventAccess"),
         SETTINGS_ROOT + " > Input Monitoring",
         "seeing your hotkeys"),
        ("Screen Recording", _preflight("CGPreflightScreenCaptureAccess"),
         SETTINGS_ROOT + " > Screen & System Audio Recording",
         "not needed to record or replay, needed later for screenshots"),
    ]


def report_lines(tool=None):
    """
    The health surface, as plain lines. The Test Input window renders these and
    so does the launcher, so there is one description of health, not two that
    can drift apart.
    """
    app, path = responsible_app()
    lines = []
    if app:
        lines.append("Permissions are granted to: %s" % app)
    else:
        lines.append("Permissions are granted to: could not identify a "
                     "launching app from the process chain")
    for label, granted, pane, purpose in permissions():
        if granted is True:
            mark = "ON"
        elif granted is False:
            mark = "OFF"
        else:
            mark = "unknown"
        lines.append("  %-18s %-7s  %s" % (label, mark, purpose))
        if granted is False and app:
            lines.append("      to fix: %s, then switch %s on" % (pane, app))
        elif granted is False:
            lines.append("      to fix: %s" % pane)
    if tool is not None:
        available, reason = tool.raw_tap_status()
        lines.append("  %-18s %-7s  %s"
                     % ("Media key watcher", "ON" if available else "OFF",
                        "seeing keys like F7 that arrive as media keys"))
        if not available and reason:
            lines.append("      %s" % reason)
    if path:
        lines.append("Launched from: %s" % path)
    return lines


if __name__ == "__main__":
    for line in report_lines():
        print(line)
    print()
    print("process chain, nearest first:")
    for pid, name, path in parent_chain():
        print("   %-7d %-22s %s" % (pid, name, path))
