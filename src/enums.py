"""Shared enum helpers.

The project targets Python 3.10, where :class:`enum.StrEnum` is not available.
:class:`StringEnum` provides the same behaviour (``str(member) == value``)
while staying compatible with 3.10.
"""

from __future__ import annotations

from enum import Enum


class StringEnum(str, Enum):
    """A string enum whose ``str()`` is the plain value on every Python version."""

    def __str__(self) -> str:
        return str(self.value)
