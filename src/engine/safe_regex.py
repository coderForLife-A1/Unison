"""RE2-backed regular expression helpers for untrusted notification text."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import re2

MAX_REGEX_INPUT_BYTES = 2 * 1024


class SafePattern:
    """A compiled RE2 pattern with a bounded input surface.

    RE2 deliberately does not support backreferences or look-around, so
    matching remains linear in the input length. Every operation also rejects
    oversized input instead of allowing callers to bypass the boundary.
    """

    def __init__(
        self,
        pattern: str,
        flags: int = 0,
        *,
        max_input_bytes: int = MAX_REGEX_INPUT_BYTES,
    ) -> None:
        self._pattern = re2.compile(pattern, flags)
        if max_input_bytes <= 0:
            raise ValueError("max_input_bytes must be positive")
        self._max_input_bytes = max_input_bytes

    def _checked_text(self, text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("regex input must be a string")
        if len(text.encode("utf-8")) > self._max_input_bytes:
            raise ValueError(
                f"regex input exceeds the {self._max_input_bytes} byte execution limit"
            )
        return text

    def search(self, text: str) -> Any:
        return self._pattern.search(self._checked_text(text))

    def finditer(self, text: str) -> Iterator[Any]:
        return self._pattern.finditer(self._checked_text(text))

    def sub(self, replacement: str, text: str) -> str:
        return self._pattern.sub(replacement, self._checked_text(text))
