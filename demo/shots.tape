# Six stills for the README grid. Recorded by `make demo-record`.
#
# Separate from `tui.tape` because the two have opposite constraints: a
# still is read, so it can be wide and slow; the hero GIF is watched, so
# every frame is paid for. Sharing one tape made every screenshot a
# by-product of a recording nobody watched at that resolution.
#
# The GIF this writes is a throwaway -- VHS always renders its `Output`,
# and `demo-record` deletes it. The `Screenshot` frames are the product.
Output demo/output/_shots-throwaway.gif

Set Shell "bash"
Set FontSize 16
# 1700 px: the full wordmark needs 140 terminal columns, measured.
Set Width 1700
Set Height 900
Set Padding 20
Set Framerate 6
Set TypingSpeed 80ms

Hide
Type "PS1='$ ' QBIT_OPS_ENV_FILE=demo/qbit-ops.env poetry run qbit-ops tui"
Enter
# Hidden, because the rate graph plots a 60s window and starts empty:
# twenty-five seconds of the capped transfer buys roughly half of it.
# `demo-record` re-arms the transfer just before this tape runs.
Sleep 25s
Show

# 1 -- Overview, on a graph that has had time to draw.
Sleep 3s
Screenshot demo/output/overview.png

# 2 -- Torrents workspace, with the command bar announcing every gesture.
Type "t"
Sleep 2s
Screenshot demo/output/torrents.png

# 3 -- Search that ignores word order: the fixtures are named
# `Documentary S01E0x`, and the words are typed the other way round.
# The TUI bans the fuzzy tier on purpose (`TuiState.set_search`), so a
# typo finds nothing here -- that example belongs to the CLI. Three
# matches rather than one, so the table is not mostly empty.
Type "/"
Sleep 400ms
Type "s01 documentary"
Sleep 2500ms
Screenshot demo/output/search.png

# Clear the term before moving on. `escape` closes the search box but
# keeps what was typed, and `ctrl+r` resets filters, sort and selection
# without touching the search -- so everything after this ran against a
# single matched torrent until the term was actually deleted.
Backspace 20
Sleep 800ms
Escape
Sleep 800ms

# 4 -- Filters: 27 fields behind four panes, applied only on Apply.
# `linux` is a real fixture category, so the pane shows a filter that
# does something. Enter applies and keeps the modal open; escape closes.
Type "f"
Sleep 2s
Type "linux"
Sleep 1500ms
Screenshot demo/output/filters.png
Enter
Sleep 1200ms
Escape
Sleep 1s

# 5 -- Details for one torrent, trackers included. Focus a row first:
# the per-torrent actions need a focused hash.
Down
Sleep 800ms
Enter
Sleep 2500ms
Screenshot demo/output/details.png
Escape
Sleep 800ms

# 6 -- The frozen preview: exactly what an Apply would change, before it
# changes anything. This is the one that carries the product's thesis.
Ctrl+a
Sleep 1200ms
Type "a"
Sleep 1500ms
Enter
Sleep 3s
Screenshot demo/output/preview.png
Sleep 2s
