"""Package entry point enabling ``python -m product_value_system <command>``."""

from product_value_system.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
