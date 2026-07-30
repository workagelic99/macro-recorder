#!/bin/bash
# Macro Recorder, double-clickable launcher.
#
# Double click this file in Finder and the window opens. That is the whole
# point of it: running a Python script from a terminal is not something to ask
# of someone every time they want their own tool.
#
# READ THIS ONCE, it explains the confusing part of macOS permissions.
#
# macOS does not grant Accessibility or Input Monitoring to a script. It grants
# them to the APP RESPONSIBLE for the process. A .command file double clicked in
# Finder is run by Terminal, so the name to look for in System Settings is
# Terminal, not Python and not Macro Recorder. The same tool launched from
# inside a code editor asks for that editor's name instead. This is why a tool
# can work perfectly in one place and do nothing in another.
#
# The launcher prints exactly which name to look for, checked live, before it
# opens the window. It never blocks on a missing permission: it reports it and
# opens anyway, because a half-permitted tool is still worth having.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/.venv/bin/python"

cd "$HERE" || exit 1

echo "Macro Recorder"
echo "=============="
echo

if [ ! -x "$PY" ]; then
  echo "The private Python for this tool is missing:"
  echo "  $PY"
  echo
  echo "Set it up once with these two lines, then double click this file again:"
  echo "  python3 -m venv \"$HERE/.venv\""
  echo "  \"$HERE/.venv/bin/pip\" install pynput pyobjc-framework-Cocoa pyobjc-framework-Quartz"
  echo
  echo "Press return to close this window."
  read -r _
  exit 1
fi

# Live permission report, from this exact launch context. Same code the
# Test Input window uses, so the two can never tell different stories.
"$PY" "$HERE/health.py" < /dev/null
echo

echo "Opening the window. Leave this Terminal window open while you use it."
echo "Closing the Macro Recorder window quits the tool."
echo

"$PY" "$HERE/macro.py" gui < /dev/null
STATUS=$?

echo
if [ $STATUS -ne 0 ]; then
  echo "The window exited with status $STATUS."
  echo "If the hotkeys did nothing, open the window again and press Test Input:"
  echo "it says whether your keypress reached this tool at all."
else
  echo "Closed."
fi
echo "Press return to close this window."
read -r _
