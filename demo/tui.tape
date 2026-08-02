# Record with: make demo-record (requires `make demo-up` to be running
# and VHS installed separately: https://github.com/charmbracelet/vhs)
Output demo/output/qbit-ops-demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1400
Set Height 800
Set Padding 20
Set TypingSpeed 120ms
Set PlaybackSpeed 1.0

Hide
Type "PS1='$ ' QBIT_OPS_ENV_FILE=demo/qbit-ops.env poetry run qbit-ops tui"
Enter
Sleep 3s
Show

# Branded Overview
Sleep 2000ms
Screenshot demo/output/overview.png

# Torrents workspace
Type "t"
Sleep 2000ms
Screenshot demo/output/torrents.png

# Inline search
Type "/"
Sleep 500ms
Type "Documentary"
Sleep 2000ms

# Focus a result, open Details briefly
Enter
Sleep 800ms
Enter
Sleep 3000ms
Escape
Sleep 600ms
Screenshot demo/output/torrent-details.png

# Select two visible torrents
Space
Sleep 600ms
Down
Sleep 600ms
Space
Sleep 1000ms

# Actions -> Pause -> frozen Preview
Type "a"
Sleep 2000ms
Enter
Sleep 3000ms
Screenshot demo/output/action-preview.png
