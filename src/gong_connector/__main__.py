"""Entry point: python -m gong_connector"""

import os
import sys


def _check_env() -> None:
    missing = []
    if not os.environ.get("GONG_API_KEY"):
        missing.append("GONG_API_KEY")
    if not os.environ.get("GONG_API_SECRET"):
        missing.append("GONG_API_SECRET")
    if missing:
        print(
            f"Error: Missing required environment variables: {', '.join(missing)}\n"
            f"Set them before running:\n"
            f"  export GONG_API_KEY=your-key\n"
            f"  export GONG_API_SECRET=your-secret",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    _check_env()
    from .server import main
    main()
