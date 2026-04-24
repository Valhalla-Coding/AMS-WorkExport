#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys

try:
    import requests
except ImportError:
    print("Paketet 'requests' saknas. Installera med:")
    print("  pip install requests")
    sys.exit(1)

from menu import main

if __name__ == "__main__":
    main()
