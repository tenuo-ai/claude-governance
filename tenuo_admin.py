#!/usr/bin/env python3
"""Backward-compatible shim — prefer ``tenuo-admin`` on PATH."""
from tenuo_claude_code.admin import main

if __name__ == "__main__":
    main()
