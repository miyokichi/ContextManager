"""Context Manager: read-only context index over existing work folders.

Pipeline (see the MVP spec): registry -> scanner -> extractors -> analyzer
-> catalog -> search / reader. Original files are never moved or modified;
this package only ever reads them and writes to its own SQLite catalog.
"""

__version__ = "0.1.0"
