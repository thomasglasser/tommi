#!/usr/bin/env python3
import sys
import os

# Ensure tommi root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.cli import main

if __name__ == "__main__":
    sys.exit(main())
