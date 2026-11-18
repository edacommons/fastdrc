"""The design rules of the open GF180MCU process as a FastDRC deck.

The deck is this folder: ``process.json``, ``rules/<section>/<rule>.json`` and
``layouts/``. FastDRC finds it by the name ``gf180mcu``::

    fastdrc check chip.gds gf180mcu --profile gf180mcuC
"""

import os

PATH = os.path.dirname(os.path.abspath(__file__))
