"""Interface-agnostic primitives reused by multiple features/adapters.

Not a generic dumping ground: a module belongs here only when it is
free of Typer/Rich/Textual, is not itself a complete user-facing use
case, and is genuinely reusable rather than owned by one feature. See
docs/ARCHITECTURE.md for the strict `features/` vs `shared/` split.
"""
