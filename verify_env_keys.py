# verify_env_keys.py
import os
import sys
from textwrap import shorten

try:
    # Lazy import so the script still runs even if dotenv isn't installed yet.
    from dotenv import load_dotenv, find_dotenv  # type: ignore
except Exception:
    load_dotenv = None
    find_dotenv = None

def mask(s: str) -> str:
    if not s:
        return ""
    # show first 2 and last 4 chars, mask the middle
    if len(s) <= 6:
        return "*" * len(s)
    return f"{s[:2]}***{s[-4:]}"

def main() -> int:
    # Load .env if available
    dotenv_path = None
    if find_dotenv and load_dotenv:
        dotenv_path = find_dotenv(usecwd=True) or None
        if dotenv_path:
            load_dotenv(dotenv_path)

    keys = ["CH_API_KEY", "CHARITY_API_KEY", "OPENAI_API_KEY"]
    ok = True

    print("== Environment key visibility check ==")
    if dotenv_path:
        print(f"Loaded .env from: {dotenv_path}")
    else:
        print("No .env file auto-detected (that's fine if you export vars in your shell).")

    for k in keys:
        v = os.getenv(k)
        if v:
            print(f"✔ {k}: present  (preview: {mask(v)})")
        else:
            print(f"✖ {k}: MISSING")
            ok = False

    # Bonus: show a hint about the interpreter/venv
    in_venv = hasattr(sys, "base_prefix") and sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    print(f"\nPython: {sys.executable}")
    print(f"In venv: {in_venv}")

    if not ok:
        print("\nFix tips:")
        print("  • Ensure a .env exists in the project root OR export the vars in your shell.")
        print("  • Key names must match exactly: CH_API_KEY, CHARITY_API_KEY, OPENAI_API_KEY")
        print("  • After editing .env, restart the app (or this script) so changes are picked up.")

    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())