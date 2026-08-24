# The README hero. Recorded by `make demo-record`.
#
# One story, not a tour: the transfer is alive, a typo still finds the
# torrent, and a bulk action shows exactly what it would touch -- then
# is abandoned. The last frame is the preview, because that is the frame
# a reader remembers.
#
# `Set Framerate 12` is the single biggest lever on file size, and it was
# absent: VHS defaults to 50, which is why the first recorded demo shipped
# at 53 MB. A terminal is not an animation; twelve frames a second reads
# as smooth and costs a quarter of the bytes.
Output demo/output/qbit-ops-demo.gif

Set Shell "bash"
Set FontSize 16
# 1500 px, not 1200: the full wordmark needs a 140-column terminal
# (measured -- at 120 the header falls back to the compact mark).
Set Width 1500
Set Height 760
Set Padding 20
Set Framerate 12
Set TypingSpeed 70ms
Set BorderRadius 12

Hide
Type "PS1='$ ' QBIT_OPS_ENV_FILE=demo/qbit-ops.env poetry run qbit-ops tui"
Enter
# Hidden, so the viewer arrives on a curve already half drawn rather than
# on an empty axis. `demo-transfer` re-arms the transfer just before this
# runs, and the leecher is capped at 100 kB/s: twenty-five seconds buys
# roughly half of the graph's sixty-second window.
Sleep 25s
Show

# The transfer, second by second.
Sleep 5s

# Into the library.
Type "t"
Sleep 1500ms

# Narrow the set. `linux` is a real fixture category, so the list
# actually shrinks -- an empty filter applies nothing and shows nothing.
#
# Enter APPLIES and leaves the modal open, by design (see the class
# docstring of `FiltersScreen`). Escape is what closes it. Skipping the
# escape sent the next keystroke into the Category field.
Type "f"
Sleep 2s
Type "linux"
Sleep 1500ms
Enter
Sleep 1500ms
Escape
Sleep 1200ms

# Focus a row before asking for anything: the per-torrent actions need
# a focused hash, not just a selection.
Down
Sleep 800ms

# Take the whole visible set -- the gesture the command bar announces.
Ctrl+a
Sleep 1500ms

# Ask for a bulk pause, and read what it would do.
Type "a"
Sleep 1500ms
Enter
Sleep 4s

# Walk away without applying. Nothing moved.
Escape
Sleep 1500ms
