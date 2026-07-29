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

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyRegular,
                    NSBackingStoreBuffered, NSBezelBorder, NSBox,
                    NSButton, NSClosableWindowMask, NSFont,
                    NSMiniaturizableWindowMask, NSScrollView,
                    NSSwitchButton, NSTableColumn, NSTableView,
                    NSTextField, NSTitledWindowMask, NSWindow)
from Foundation import NSMakeRect, NSObject, NSTimer

import macro

POLL_SECONDS = 0.15
W, H = 580, 500


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
        self.btn_record = button("Record (F6)", 240, y - 4, 110, self,
                                 self.onRecord_)
        self.btn_stop = button("Stop", 356, y - 4, 80, self, self.onStop_)
        self.btn_play = button("Play (F7)", 442, y - 4, 100, self, self.onPlay_)
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
        self.btn_auto = button("Start (F8)", 262, by - 4, 110, self,
                               self.onAutoclick_)
        inner2.addSubview_(self.btn_auto)

        # status line
        self.status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(16, 14, W - 32, 24))
        self.status.setEditable_(False)
        self.status.setSelectable_(True)
        self.status.setBezeled_(True)
        self.status.setStringValue_("Idle")
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

    # ---------- polling, the only thing that touches views

    def tick_(self, timer):
        state = self.tool.state
        self.status.setStringValue_(self.tool.status())
        busy = state != "idle"
        self.btn_record.setEnabled_(not busy)
        self.btn_play.setEnabled_(not busy)
        self.btn_stop.setEnabled_(busy)
        self.btn_auto.setTitle_("Stop (F8)" if state == "autoclicking"
                                else "Start (F8)")
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
