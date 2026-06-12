#!/usr/bin/env python3
"""Backward-compatible shim — prefer ``tenuo-claude`` on PATH."""
from tenuo_claude_code.cli import main

if __name__ == "__main__":
    main()
