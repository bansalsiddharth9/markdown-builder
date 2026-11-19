"""specval: independently validate specconv output against its sources.

Must never import specconv (design: independence rules).
"""

from importlib.metadata import version as _version

__version__ = _version("specval")
