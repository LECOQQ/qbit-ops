# 🚨 Errors and exit codes

Fatal command errors are written to **stderr**. Successful or operational reports are written to **stdout**, including JSON/JSONL/CSV output.

| Code | Meaning |
|---:|---|
| 0 | ✅ success or intentional cancellation |
| 1 | ⚠️ warning or handled command failure |
| 2 | ⛔ critical/no-match result, depending on the command |
| 3 | 📡 remote data unavailable for health-style commands |
| 4 | 🚫 invalid qbit-ops usage or configuration |
| 70 | 💥 unexpected internal qbit-ops error |

Exact meanings vary for health-style commands such as `status`, `doctor`, `trackers status`, and `explain`. Use the command's `--help` and structured output when scripting.

## 📤 Structured output

On a successful machine-readable invocation:

- ✅ stdout contains only valid serialized output;
- ✅ stderr stays empty;
- ✅ no Rich or ANSI decoration is emitted.

On a fatal error:

- ⛔ stdout stays empty;
- ⛔ stderr contains a concise, redacted message;
- ⛔ the process exits non-zero.

`doctor` is intentionally different: configuration and connection failures are part of its diagnostic report, so they are returned as structured checks on stdout.

Tracker credentials and announce URLs are redacted from ordinary errors and reports.
