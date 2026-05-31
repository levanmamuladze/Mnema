"""Top-level package marker.

The `__version__` constant is the single source of truth for the
deployed app version. Bump it on every release; it's exposed via
`/health` so clients (and you) can tell what they're running without
poking at git or container labels.
"""

__version__ = "1.0.0"
