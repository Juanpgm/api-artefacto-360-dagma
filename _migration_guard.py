"""Safety guard for destructive Firestore migration scripts.

Migration scripts in this directory write to the PRODUCTION Firestore database
when run with ``--apply``. To prevent accidental (fat-finger) writes, every
``--apply`` run must pass an explicit confirmation before any write happens:

- Interactive terminal: the operator must type the exact collection name.
- Non-interactive (CI, piped, cron): refused unless the environment variable
  ``MIGRATION_CONFIRM`` equals the collection name.

Call ``confirm_apply(collection)`` right after parsing ``--apply`` and before
invoking the write path.
"""
import os
import sys

CONFIRM_ENV = "MIGRATION_CONFIRM"


def confirm_apply(collection: str) -> None:
    """Abort the process unless the operator explicitly confirms the write.

    Raises ``SystemExit`` (exit code 2) when confirmation is missing or wrong,
    so an unconfirmed ``--apply`` never reaches Firestore.
    """
    print(
        f"\n  WARNING: about to WRITE to PRODUCTION Firestore collection "
        f"'{collection}'.",
        file=sys.stderr,
    )

    stdin = sys.stdin
    is_tty = bool(stdin) and hasattr(stdin, "isatty") and stdin.isatty()

    if not is_tty:
        if os.getenv(CONFIRM_ENV, "") == collection:
            print(f"  Confirmed via {CONFIRM_ENV}={collection}.", file=sys.stderr)
            return
        print(
            f"  Refusing: non-interactive run. Set {CONFIRM_ENV}={collection} "
            f"to apply.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    answer = input(
        f"  Type the collection name '{collection}' to proceed (anything else aborts): "
    ).strip()
    if answer != collection:
        print("  Aborted: confirmation did not match.", file=sys.stderr)
        raise SystemExit(2)
    print("  Confirmed.", file=sys.stderr)
