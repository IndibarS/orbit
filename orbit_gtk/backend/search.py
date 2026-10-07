"""Bounded, deterministic package search and safe matching-text markup."""

import re
from difflib import SequenceMatcher
from fnmatch import translate
from html import escape


def _distance(left: str, right: str, maximum: int) -> int:
    """Banded edit distance, including adjacent typing transpositions."""
    if abs(len(left) - len(right)) > maximum:
        return maximum + 1
    previous = list(range(len(right) + 1))
    older = previous
    for i, char in enumerate(left, 1):
        current = [maximum + 1] * (len(right) + 1)
        current[0] = i
        for j in range(max(1, i - maximum), min(len(right), i + maximum) + 1):
            current[j] = min(
                previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char != right[j - 1])
            )
            if i > 1 and j > 1 and char == right[j - 2] and left[i - 2] == right[j - 1]:
                current[j] = min(current[j], older[j - 2] + 1)
        if min(current) > maximum:
            return maximum + 1
        older, previous = previous, current
    return previous[-1]


class _NamePattern:
    """Shell-style name patterns with bounded highlighting (no user regex)."""

    def __init__(self, pattern):
        self.compiled = re.compile(translate(pattern))
        self.tokens = []
        i = 0
        while i < len(pattern):
            char = pattern[i]
            end = i + 1
            if char == "[":
                close = end + (pattern[end : end + 1] == "!")
                close += pattern[close : close + 1] == "]"
                close = pattern.find("]", close)
                if close >= 0:
                    end = close + 1
            token = pattern[i:end]
            if token != "*" or not self.tokens or self.tokens[-1] != "*":
                self.tokens.append(token)
            i = end
        self.checks = [
            None if token == "*" else re.compile(translate(token)) for token in self.tokens
        ]

    def positions(self, name):
        if not self.compiled.fullmatch(name):
            return None
        # A suffix table chooses a valid alignment without recursive/backtracking
        # highlighting. Stars consume gaps; '?' is unknown, so isn't emphasized.
        count = len(name)
        rows = [bytearray(count + 1) for _ in range(len(self.tokens) + 1)]
        rows[-1][-1] = 1
        for i in range(len(self.tokens) - 1, -1, -1):
            token, check = self.tokens[i], self.checks[i]
            if token == "*":
                rows[i][count] = rows[i + 1][count]
            for j in range(count - 1, -1, -1):
                rows[i][j] = (
                    (rows[i + 1][j] or rows[i][j + 1])
                    if token == "*"
                    else (bool(check.fullmatch(name[j])) and rows[i + 1][j + 1])
                )
        positions = set()
        j = 0
        for i, token in enumerate(self.tokens):
            if token == "*":
                while not rows[i + 1][j]:
                    j += 1
            else:
                if token != "?":
                    positions.add(j)
                j += 1
        return positions


class SmartSearch:
    """Literal phrases and words first; conservative spelling matches next.

    Short terms stay literal. Fuzzy matching only examines names and their
    components, never long descriptions. Longer words allow up to three edits;
    compact, ordered abbreviations rank below spelling matches.
    Wildcards are recognized automatically and match complete package names.
    """

    def __init__(self, query: str):
        self.query = query.strip().casefold()
        if len(self.query) > 256:
            raise ValueError("Search text must be at most 256 characters")
        self.terms = tuple(dict.fromkeys(self.query.split()))
        self.pattern = _NamePattern(self.query) if any(c in self.query for c in "*?[") else None

    def matches_text(self, text: str) -> bool:
        if self.pattern is not None:
            return False
        folded = text.casefold()
        return bool(self.terms) and all(term in folded for term in self.terms)

    def name_match(self, name: str):
        """Return (rank, matching character positions), or None."""
        folded = name.casefold()
        if not self.terms:
            return None
        if self.pattern is not None:
            positions = self.pattern.positions(folded)
            return (2, positions) if positions is not None else None
        positions = set()
        literal = True
        quality = 0.0
        candidates = [(0, folded)] + [(m.start(), m.group()) for m in re.finditer(r"[\w]+", folded)]
        for term in self.terms:
            start = folded.find(term)
            if start >= 0:
                while start >= 0:
                    positions.update(range(start, start + len(term)))
                    start = folded.find(term, start + len(term))
                continue
            literal = False
            if not 4 <= len(term) <= 64 or not re.fullmatch(r"[\w.+-]+", term):
                return None
            maximum = min(3, max(1, len(term) // 3))
            best = None
            for offset, candidate in candidates:
                matched = None
                if abs(len(candidate) - len(term)) <= maximum:
                    distance = _distance(term, candidate, maximum)
                    if distance <= maximum:
                        score = distance / (len(term) + 1)
                        matched = set()
                        for block in SequenceMatcher(
                            None, term, candidate, autojunk=False
                        ).get_matching_blocks():
                            matched.update(range(offset + block.b, offset + block.b + block.size))
                # Abbreviations must start at a name/component boundary and
                # retain at least a third of the letters. This avoids matching
                # arbitrary scattered letters in very long library names.
                if (
                    matched is None
                    and candidate.startswith(term[0])
                    and (len(term) < len(candidate) <= len(term) * 3)
                ):
                    cursor = 0
                    abbreviation = set()
                    for char in term:
                        found = candidate.find(char, cursor)
                        if found < 0:
                            break
                        abbreviation.add(offset + found)
                        cursor = found + 1
                    else:
                        score = 1 + (len(candidate) - len(term)) / len(candidate)
                        matched = abbreviation
                if matched is not None and candidate != folded:
                    score += 0.15  # Prefer a whole package name over an embedded component.
                if matched is not None and (best is None or score < best[0]):
                    best = score, matched
            if best is None:
                return None
            quality = max(quality, best[0])
            positions.update(best[1])
        if literal:
            rank = 0 if folded == self.query else 1 if folded.startswith(self.query) else 2
        else:
            rank = 3 + quality
        return rank, positions

    def markup(self, name: str) -> str:
        match = self.name_match(name)
        positions = match[1] if match else set()
        result = []
        offset = 0
        active = False
        for char in name:
            width = len(char.casefold())
            highlighted = any(index in positions for index in range(offset, offset + width))
            if highlighted != active:
                result.append(
                    "<span background='#f6d32d' foreground='#241f00'>" if highlighted else "</span>"
                )
                active = highlighted
            result.append(escape(char))
            offset += width
        if active:
            result.append("</span>")
        return "".join(result)
