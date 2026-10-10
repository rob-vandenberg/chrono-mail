# --- Version ------------------------------------------------------------
__version__ = "limits.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial parser limits

# Caps for anything whose size or count comes from the message itself (AGENTS.md section 4.2).
MAXIMUM_MIME_DEPTH: int = 20
MAXIMUM_PARTS: int = 1000
MAXIMUM_HEADER_SECTION_BYTES: int = 1024 * 1024
MAXIMUM_PART_BYTES: int = 256 * 1024 * 1024
MAXIMUM_DEFECTS: int = 50
