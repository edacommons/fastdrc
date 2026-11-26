"""FastDRC — design rules drawn by example, compiled to a working DRC deck.

You draw a rule the way a design manual does (shapes, an arrow where a distance
matters, what must hold); the compiler derives the layer operations and a backend
writes the deck. See :mod:`fastdrc.process` (a deck: build, check, self-test),
:mod:`fastdrc.backend` (backends are plugins) and :mod:`fastdrc.editor`.
"""

from __future__ import annotations

__version__ = "0.0.3"
