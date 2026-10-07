"""Gettext translation with an English fallback and packaged locale catalogues."""

import gettext
from pathlib import Path

_translation = gettext.translation(
    "orbit", localedir=Path(__file__).parent / "locales", fallback=True
)
tr = _translation.gettext
ntr = _translation.ngettext
