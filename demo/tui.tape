# Record with: make demo-record (requires `make demo-up` to be running
# and VHS installed separately: https://github.com/charmbracelet/vhs)
Output demo/output/qbit-ops-demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1400
Set Height 800
Set Padding 20
Set TypingSpeed 60ms
Set PlaybackSpeed 1.0

Hide
Type "PS1='$ ' QBIT_OPS_ENV_FILE=demo/qbit-ops.env poetry run qbit-ops tui"
Enter
Sleep 3s
Show

# Branded Overview
Sleep 2000ms

# Torrents workspace
Type "t"
Sleep 800ms

# Inline search
Type "/"
Sleep 300ms
Type "Documentary"
Sleep 800ms

# Focus a result, open Details briefly
Enter
Sleep 400ms
Enter
Sleep 1200ms
Escape
Sleep 300ms

# Select two visible torrents
Space
Sleep 200ms
Down
Sleep 200ms
Space
Sleep 500ms

# Actions -> Pause -> frozen Preview
Type "a"
Sleep 600ms
Enter
Sleep 2500ms
