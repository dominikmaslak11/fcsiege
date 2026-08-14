#!/usr/bin/env python3
"""Uruchamia aplikacje FCSiege."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fcsiege.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
