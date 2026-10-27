"""The Cagr toolkit: the library every component imports (spec 0004).

Layers, lowest first; a lower layer never imports a higher one:

- compute: `ind`, `metrics`, `calendar`, `splits` (pure functions, no I/O)
- definitions: `fs` (features and labels)
- access: `db` (settings, read-only connections)
"""

__version__ = "0.1.0"
