"""Package entry point.

The Apify CLI runs an actor with `python -m src`, which requires this file.
The Dockerfile uses the same command so that local runs and cloud runs execute
through an identical path — a mismatch here is how you get an actor that works
on your machine and fails on the platform.

All logic lives in main.py; this only starts the event loop.
"""

import asyncio

from .main import main

asyncio.run(main())
