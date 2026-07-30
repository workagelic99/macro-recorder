#!/usr/bin/env python3
"""
gui.py - the window for macro.py, built on Cocoa through pyobjc.

This is a front end only. Every action calls straight into MacroTool from
macro.py, the same object the command line uses. There is no second copy of
the record or replay logic in here.

Why Cocoa and not tkinter. tkinter was tried first and abandoned on evidence.
The only Python on macOS that ships Tk is Apple's /usr/bin/python3, and it
carries Tk 8.5.9 from 2010, which macOS itself marks deprecated. On macOS
26.4 that Tk lays every widget out correctly and reports all of them mapped
and viewable, then draws none of them: the window opens the right size with
the right title and stays empty. It is not a pynput interaction. Measured
both ways, a window with no pynput anywhere in the process and a window with
the listeners started first both mapped 5 of 5 children, identically.
pyobjc is already required by pynput on macOS, so Cocoa costs no new
dependency, needs no deprecated toolkit, and draws.

Threading: recording, playback and autoclicking already run on their own
threads inside MacroTool, so no button handler blocks. Nothing on a worker
thread touches a view. An NSTimer on the main thread polls tool.status()
instead, which is safe to read from anywhere.
"""

import time

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyRegular,
                    NSBackingStoreBuffered, NSBezelBorder, NSBox,
                    NSButton, NSClosableWindowMask, NSFont,
                    NSMiniaturizableWindowMask, NSScrollView,
                    NSSwitchButton, NSTableColumn, NSTableView,
                    NSTextField, NSTitledWindowMask, NSWindow)
from Foundation import NSMakeRect, NSObject, NSTimer

import bindings
import health
import macro

POLL_SECONDS = 0.15
W, H = 580, 700

# How long a Test Input prompt listens before it reports that nothing arrived.
TEST_INPUT_SECONDS = 8.0

ACTION_TITLES = {"record": "Record", "play": "Play", "autoclick": "Autoclick"}


def tool_label(tool, action):
    """The ONE label formatter, so a button can never disagree with matching."""
    return tool.binding_label(action)


def label(text, x, y, w, h=18, bold=False):
    f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    f.setStringValue_(text)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setFont_(NSFont.boldSystemFontOfSize_(12) if bold
               else NSFont.systemFontOfSize_(12))
    return f


def field(text, x, y, w, h=22):
    f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    f.setStringValue_(text)
    f.setFont_(NSFont.systemFontOfSize_(12))
    return f


def readout(text, x, y, w, h=22):
    """A bordered box that shows a value the user cannot type into."""
    f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    f.setStringValue_(text)
    f.setEditable_(False)
    f.setSelectable_(True)
    f.setBezeled_(True)
    f.setFont_(NSFont.systemFontOfSize_(12))
    return f


def button(title, x, y, w, target, action, h=28):
    b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    b.setTitle_(title)
    b.setBezelStyle_(1)          # rounded push button
    b.setTarget_(target)
    b.setAction_(objc.selector(action, signature=b"v@:@"))
    return b


class MacroWindow(NSObject):
    """Window controller, table data source and window delegate in one."""

    def initWithTool_(self, tool):
        self = objc.super(MacroWindow, self).init()
        if self is None:
            return None
        self.tool = tool
        self.names = []
        self.last_state = None
        # editor state, main thread only
        self.capturing = None          # which action is waiting for a key
        self.set_buttons = {}
        self.cancel_buttons = {}
        self.binding_fields = {}
        # Test Input prompt state, main thread only
        self.test_window = None
        self.test_seq = 0
        self.test_started = 0.0
        self.test_decided = False
        self.build()
        return self

    # ---------- construction

    def build(self):
        style = (NSTitledWindowMask | NSClosableWindowMask
                 | NSMiniaturizableWindowMask)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(240, 240, W, H), style, NSBackingStoreBuffered, False)
        self.window.setTitle_("Macro Recorder")
        self.window.setDelegate_(self)
        view = self.window.contentView()

        view.addSubview_(label("Recordings", 16, H - 34, 120, 18, bold=True))

        # recordings table
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(16, H - 244, W - 32, 205))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        self.table = NSTableView.alloc().initWithFrame_(scroll.bounds())
        col = NSTableColumn.alloc().initWithIdentifier_("name")
        col.setWidth_(W - 56)
        col.headerCell().setStringValue_("Name")
        self.table.addTableColumn_(col)
        self.table.setDataSource_(self)
        self.table.setDelegate_(self)
        scroll.setDocumentView_(self.table)
        view.addSubview_(scroll)

        # save-as name plus the three main buttons
        y = H - 285
        view.addSubview_(label("Save as", 16, y + 4, 60))
        self.name_field = field("my_sequence", 78, y, 150)
        view.addSubview_(self.name_field)
        self.btn_record = button("Record (%s)" % tool_label(self.tool, "record"),
                                 232, y - 4, 122, self, self.onRecord_)
        self.btn_stop = button("Stop", 360, y - 4, 74, self, self.onStop_)
        self.btn_play = button("Play (%s)" % tool_label(self.tool, "play"),
                               440, y - 4, 104, self, self.onPlay_)
        for b in (self.btn_record, self.btn_stop, self.btn_play):
            view.addSubview_(b)

        # playback options
        box = NSBox.alloc().initWithFrame_(NSMakeRect(16, H - 375, W - 32, 74))
        box.setTitle_("Playback")
        box.setTitleFont_(NSFont.systemFontOfSize_(11))
        view.addSubview_(box)
        inner = box.contentView()
        by = 14
        inner.addSubview_(label("Speed", 10, by + 4, 46))
        self.speed_field = field("1.0", 58, by, 56)
        inner.addSubview_(self.speed_field)
        inner.addSubview_(label("Repeat", 130, by + 4, 50))
        self.loop_field = field("1", 182, by, 56)
        inner.addSubview_(self.loop_field)
        self.forever = NSButton.alloc().initWithFrame_(
            NSMakeRect(256, by - 2, 150, 24))
        self.forever.setButtonType_(NSSwitchButton)
        self.forever.setTitle_("Loop forever")
        self.forever.setFont_(NSFont.systemFontOfSize_(12))
        self.forever.setTarget_(self)
        self.forever.setAction_(objc.selector(self.onForever_,
                                              signature=b"v@:@"))
        inner.addSubview_(self.forever)

        # autoclicker
        box2 = NSBox.alloc().initWithFrame_(NSMakeRect(16, H - 459, W - 32, 74))
        box2.setTitle_("Autoclicker")
        box2.setTitleFont_(NSFont.systemFontOfSize_(11))
        view.addSubview_(box2)
        inner2 = box2.contentView()
        inner2.addSubview_(label("Min ms", 10, by + 4, 50))
        self.min_field = field("80", 62, by, 56)
        inner2.addSubview_(self.min_field)
        inner2.addSubview_(label("Max ms", 134, by + 4, 52))
        self.max_field = field("140", 188, by, 56)
        inner2.addSubview_(self.max_field)
        self.btn_auto = button("Start (%s)" % tool_label(self.tool, "autoclick"),
                               256, by - 4, 130, self, self.onAutoclick_)
        inner2.addSubview_(self.btn_auto)

        # hotkeys editor. One row per action so a Set, a Cancel or a duplicate
        # rejection is always attributable to the row it was pressed on: a
        # single shared Set button would hide a misrouted edit.
        box3 = NSBox.alloc().initWithFrame_(NSMakeRect(16, H - 624, W - 32, 158))
        box3.setTitle_("Hotkeys")
        box3.setTitleFont_(NSFont.systemFontOfSize_(11))
        view.addSubview_(box3)
        inner3 = box3.contentView()
        for row, action in enumerate(("record", "play", "autoclick")):
            ry = 104 - row * 28
            inner3.addSubview_(label(ACTION_TITLES[action], 10, ry + 4, 74))
            f = readout(self.tool.binding_label(action), 88, ry, 168)
            self.binding_fields[action] = f
            inner3.addSubview_(f)
            setter = {"record": self.onSetRecord_, "play": self.onSetPlay_,
                      "autoclick": self.onSetAutoclick_}[action]
            canceller = {"record": self.onCancelRecord_,
                         "play": self.onCancelPlay_,
                         "autoclick": self.onCancelAutoclick_}[action]
            b_set = button("Set", 266, ry - 2, 64, self, setter, h=24)
            b_cancel = button("Cancel", 336, ry - 2, 82, self, canceller, h=24)
            b_cancel.setEnabled_(False)
            self.set_buttons[action] = b_set
            self.cancel_buttons[action] = b_cancel
            inner3.addSubview_(b_set)
            inner3.addSubview_(b_cancel)
        self.btn_reset = button("Reset Defaults", 10, 8, 140, self,
                                self.onResetDefaults_, h=24)
        inner3.addSubview_(self.btn_reset)
        self.btn_test = button("Test Input", 158, 8, 118, self,
                               self.onTestInput_, h=24)
        inner3.addSubview_(self.btn_test)
        inner3.addSubview_(label("Set, then press the key you want.",
                                 286, 12, 250))

        # status line
        self.status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(16, 14, W - 32, 24))
        self.status.setEditable_(False)
        self.status.setSelectable_(True)
        self.status.setBezeled_(True)
        self.status.setStringValue_(
            self.tool.binding_warning or "Idle")
        self.status.setFont_(NSFont.systemFontOfSize_(12))
        view.addSubview_(self.status)

        self.refresh_list()
        self.sync_loop_field()
        self.window.makeKeyAndOrderFront_(None)

    # ---------- table data source

    def numberOfRowsInTableView_(self, table):
        return len(self.names)

    def tableView_objectValueForTableColumn_row_(self, table, col, row):
        if 0 <= row < len(self.names):
            name, count = self.names[row]
            return "%s   (%s events)" % (name, count if count >= 0 else "?")
        return ""

    # ---------- helpers

    @objc.python_method
    def refresh_list(self):
        previous = self.selected_name()
        self.names = [(n, c) for n, c, _ in macro.list_recordings()]
        self.table.reloadData()
        for i, (n, _) in enumerate(self.names):
            if n == previous:
                self.table.selectRowIndexes_byExtendingSelection_(
                    __import__("Foundation").NSIndexSet.indexSetWithIndex_(i),
                    False)
                break

    @objc.python_method
    def selected_name(self):
        row = self.table.selectedRow()
        if 0 <= row < len(self.names):
            return self.names[row][0]
        return None

    @objc.python_method
    def flash(self, message):
        self.tool.last_message = message

    @objc.python_method
    def sync_loop_field(self):
        self.loop_field.setEnabled_(self.forever.state() == 0)

    # ---------- actions, none of these block

    def onRecord_(self, sender):
        name = self.name_field.stringValue().strip()
        if not macro.valid_name(name):
            self.flash("That name is not allowed. Letters, numbers, dot, "
                       "dash and underscore only.")
            return
        self.tool.start_record(name)

    def onStop_(self, sender):
        if self.tool.state == "recording":
            self.tool.stop_record()
            self.refresh_list()
        else:
            self.tool.request_stop()

    def onPlay_(self, sender):
        name = self.selected_name()
        if not name:
            self.flash("Pick a recording from the list first.")
            return
        try:
            speed = float(self.speed_field.stringValue())
        except ValueError:
            self.flash("Speed must be a number, for example 1.5")
            return
        if speed <= 0:
            self.flash("Speed must be greater than 0.")
            return
        if self.forever.state():
            loops = 0
        else:
            try:
                loops = int(self.loop_field.stringValue())
            except ValueError:
                self.flash("Repeat must be a whole number.")
                return
            if loops < 1:
                self.flash("Repeat must be 1 or more, or tick Loop forever.")
                return
        self.tool.start_play(name=name, speed=speed, loops=loops)

    def onAutoclick_(self, sender):
        if self.tool.state == "autoclicking":
            self.tool.request_stop()
            return
        try:
            lo = int(self.min_field.stringValue())
            hi = int(self.max_field.stringValue())
        except ValueError:
            self.flash("Autoclicker min and max must be whole numbers of ms.")
            return
        self.tool.start_autoclick(min_ms=lo, max_ms=hi)

    def onForever_(self, sender):
        self.sync_loop_field()

    # ---------- hotkeys editor, all on the main thread

    @objc.python_method
    def refresh_binding_labels(self):
        """One formatter feeds the rows and the buttons, so a label can never
        disagree with what actually matches."""
        for action, f in self.binding_fields.items():
            f.setStringValue_(self.tool.binding_label(action))
        self.btn_record.setTitle_("Record (%s)"
                                  % tool_label(self.tool, "record"))
        self.btn_play.setTitle_("Play (%s)" % tool_label(self.tool, "play"))

    @objc.python_method
    def begin_capture(self, action):
        if self.capturing is not None:
            self.cancel_capture(self.capturing)
        self.capturing = action
        self.tool.begin_capture()
        self.binding_fields[action].setStringValue_("press a key...")
        for name, b in self.set_buttons.items():
            b.setEnabled_(name == action)
            b.setTitle_("Waiting" if name == action else "Set")
        for name, b in self.cancel_buttons.items():
            b.setEnabled_(name == action)
        self.flash("Press the key you want for %s. Cancel to keep the current "
                   "one." % ACTION_TITLES[action])

    @objc.python_method
    def cancel_capture(self, action=None):
        action = action or self.capturing
        self.tool.cancel_capture()
        self.capturing = None
        for name, b in self.set_buttons.items():
            b.setEnabled_(True)
            b.setTitle_("Set")
        for b in self.cancel_buttons.values():
            b.setEnabled_(False)
        self.refresh_binding_labels()
        if action:
            self.flash("%s hotkey unchanged." % ACTION_TITLES[action])

    @objc.python_method
    def apply_binding(self, action, binding):
        """
        Validate, SAVE, then swap the live map, in that order.

        The order is the contract. If the save fails there must be no way for
        the running tool to be using a hotkey the file does not contain, so the
        live map only changes after the bytes are safely on disk.
        """
        candidate = self.tool.binding_map()
        candidate[action] = binding
        try:
            bindings.validate_map(candidate)
        except bindings.BindingError as exc:
            self.flash("Cannot use that: %s" % exc)
            return False
        ok, warning = bindings.save_map(candidate, self.tool.config_path)
        if not ok:
            self.flash(warning)
            return False
        self.tool.set_binding_map(candidate)
        self.refresh_binding_labels()
        self.flash("%s is now %s" % (ACTION_TITLES[action],
                                     bindings.format_binding(binding)))
        return True

    @objc.python_method
    def finish_capture(self, binding, error):
        """Called from the timer once the listener has a key. Main thread."""
        action = self.capturing
        self.capturing = None
        for b in self.set_buttons.values():
            b.setEnabled_(True)
            b.setTitle_("Set")
        for b in self.cancel_buttons.values():
            b.setEnabled_(False)
        if action is None:
            return
        if error or binding is None:
            self.flash("That key cannot be used: %s" % (error or "unknown key"))
            self.refresh_binding_labels()
            return
        if not self.apply_binding(action, binding):
            self.refresh_binding_labels()

    def onSetRecord_(self, sender):
        self.begin_capture("record")

    def onSetPlay_(self, sender):
        self.begin_capture("play")

    def onSetAutoclick_(self, sender):
        self.begin_capture("autoclick")

    def onCancelRecord_(self, sender):
        self.cancel_capture("record")

    def onCancelPlay_(self, sender):
        self.cancel_capture("play")

    def onCancelAutoclick_(self, sender):
        self.cancel_capture("autoclick")

    def onResetDefaults_(self, sender):
        if self.capturing is not None:
            self.cancel_capture(self.capturing)
        defaults = bindings.default_map()
        ok, warning = bindings.save_map(defaults, self.tool.config_path)
        if not ok:
            self.flash(warning)
            return
        self.tool.set_binding_map(defaults)
        self.refresh_binding_labels()
        self.flash("Hotkeys reset to the built-in defaults.")

    # ---------- Test Input

    def onTestInput_(self, sender):
        self.open_test_input()

    @objc.python_method
    def build_test_window(self):
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(320, 300, 560, 300),
            NSTitledWindowMask | NSClosableWindowMask,
            NSBackingStoreBuffered, False)
        win.setTitle_("Test Input")
        view = win.contentView()
        view.addSubview_(label("Press any key. This only watches, it does not "
                               "trigger anything.", 16, 262, 520, 18))
        self.test_outcome = readout("", 16, 216, 528, 38)
        self.test_outcome.setFont_(NSFont.boldSystemFontOfSize_(12))
        view.addSubview_(self.test_outcome)
        view.addSubview_(label("Permissions, checked live in this process",
                               16, 188, 400, 18, bold=True))
        self.test_health = NSTextField.alloc().initWithFrame_(
            NSMakeRect(16, 48, 528, 136))
        self.test_health.setEditable_(False)
        self.test_health.setSelectable_(True)
        self.test_health.setBezeled_(True)
        self.test_health.setFont_(NSFont.fontWithName_size_("Menlo", 10)
                                  or NSFont.systemFontOfSize_(10))
        view.addSubview_(self.test_health)
        view.addSubview_(button("Test again", 16, 12, 120, self,
                                self.onTestAgain_, h=26))
        self.test_window = win
        return win

    @objc.python_method
    def open_test_input(self):
        """
        Snapshot the callback counter and the clock, THEN clear the readout.

        The snapshot is the whole point. Without it a prompt could report a
        keypress from a minute ago as proof the listener is alive, which is
        exactly the stale evidence a health indicator is supposed to rule out.
        """
        win = self.test_window or self.build_test_window()
        self.test_seq = self.tool.input_seq()
        self.test_started = time.monotonic()
        self.test_decided = False
        self.test_outcome.setStringValue_("Waiting for a keypress...")
        self.test_health.setStringValue_(
            "\n".join(health.report_lines(self.tool)))
        win.makeKeyAndOrderFront_(None)

    def onTestAgain_(self, sender):
        self.open_test_input()

    @objc.python_method
    def update_test_input(self):
        """One of exactly three outcomes, decided once per prompt."""
        if self.test_window is None or self.test_decided:
            return
        if not self.test_window.isVisible():
            return
        elapsed = time.monotonic() - self.test_started
        event = self.tool.last_input()
        if (event and event["seq"] > self.test_seq
                and event["mono"] >= self.test_started
                and (event["mono"] - self.test_started) <= TEST_INPUT_SECONDS):
            self.test_decided = True
            if event["kind"] == "raw_unmapped":
                self.test_outcome.setStringValue_(
                    "Seen, but unusable: %s. This Mac sends that key as a "
                    "media key this app cannot read, so it cannot be a hotkey."
                    % event["detail"])
            else:
                self.test_outcome.setStringValue_(
                    "Key seen: %s. Hotkeys can use this key."
                    % event["detail"])
            return
        if elapsed > TEST_INPUT_SECONDS:
            self.test_decided = True
            self.test_outcome.setStringValue_(
                "Nothing seen in %d seconds. No key reached this app."
                % int(TEST_INPUT_SECONDS))

    # ---------- polling, the only thing that touches views

    def push_context(self):
        """
        Hand the engine everything a button handler would use.

        Runs on the MAIN thread only (called from the timer), because a
        listener thread may never read an AppKit view. Pushing on every tick
        rather than once at startup is deliberate: values the user edits
        mid-session must be the ones a hotkey acts on.
        """
        ctx = {"name": self.name_field.stringValue().strip() or None,
               "selected": self.selected_name()}
        try:
            ctx["speed"] = float(self.speed_field.stringValue())
        except ValueError:
            pass
        if self.forever.state():
            ctx["loops"] = 0
        else:
            try:
                ctx["loops"] = int(self.loop_field.stringValue())
            except ValueError:
                pass
        try:
            ctx["min_ms"] = int(self.min_field.stringValue())
            ctx["max_ms"] = int(self.max_field.stringValue())
        except ValueError:
            pass
        self.tool.set_context(**ctx)

    def tick_(self, timer):
        self.push_context()
        # A capture completes on a listener thread, which may never touch a
        # view. It parks the result and this main-thread tick applies it.
        if self.capturing is not None:
            taken = self.tool.take_capture()
            if taken is not None:
                self.finish_capture(taken[0], taken[1])
        self.update_test_input()
        state = self.tool.state
        self.status.setStringValue_(self.tool.status())
        busy = state != "idle"
        self.btn_record.setEnabled_(not busy)
        self.btn_play.setEnabled_(not busy)
        self.btn_stop.setEnabled_(busy)
        label = tool_label(self.tool, "autoclick")
        self.btn_auto.setTitle_(("Stop (%s)" if state == "autoclicking"
                                 else "Start (%s)") % label)
        self.btn_auto.setEnabled_(state in ("idle", "autoclicking"))
        if self.last_state == "recording" and state != "recording":
            self.refresh_list()
        self.last_state = state

    # ---------- window delegate

    def windowWillClose_(self, notification):
        self.tool.shutdown()
        NSApplication.sharedApplication().terminate_(None)


def run():
    # Listeners first, before the app run loop exists. This ordering was
    # required under Tk and is kept because it is also simply correct: the
    # hotkeys should be live the moment the window appears.
    tool = macro.MacroTool("all", verbose=False)
    tool.start_listeners()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    # The media key watcher goes on the MAIN run loop, which only exists here.
    # It is listen only, so a failure costs those keys and nothing else.
    tool.start_raw_tap()
    controller = MacroWindow.alloc().initWithTool_(tool)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        POLL_SECONDS, controller, "tick:", None, True)
    app.activateIgnoringOtherApps_(True)
    try:
        app.run()
    finally:
        tool.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
