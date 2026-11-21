"""Pseudonymous ids for usage statistics.

Statistics tables never hold a raw glasses ``device_id`` or ``public_user_id``; they hold
``HMAC-SHA256(USAGE_ID_KEY, "<kind>:<id>")``. ``/me/stats`` recomputes the owner's pseudonym
from the authenticated identity to filter. Admin statistics responses (``/admin/stats/*``) never
include a pseudonym; the directory endpoints (``/admin/accounts``, ``/admin/devices``) list the
real account/glasses ids from the main tables, as the dashboard contract requires.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Literal


def pseudonym(key: bytes, kind: Literal["owner", "device"], raw: str) -> str:
    """Return the 64-hex HMAC pseudonym of ``raw`` for ``kind``."""
    return hmac.new(key, f"{kind}:{raw}".encode(), hashlib.sha256).hexdigest()
