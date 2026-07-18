"""CLI entry point for the default-disabled async-effect worker foundation."""

from app.async_effects.worker import main


if __name__ == "__main__":
    raise SystemExit(main())
