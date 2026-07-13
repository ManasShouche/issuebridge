"""In-memory guard against duplicate event handling.

Covers two cases:
- Slack redelivers an event when the handler takes too long to ack (retries).
- An @mention in a thread fires both `app_mention` and `message` events for
  the same message; whichever handler claims it first wins.
"""
from collections import OrderedDict

_MAX_ENTRIES = 500
_seen: OrderedDict[str, None] = OrderedDict()


def already_handled(channel_id: str, ts: str) -> bool:
    """Return True if this (channel, ts) message was already claimed.

    First caller claims the message and gets False; every later caller
    (retry or twin event) gets True.
    """
    key = f"{channel_id}:{ts}"
    if key in _seen:
        return True
    _seen[key] = None
    if len(_seen) > _MAX_ENTRIES:
        _seen.popitem(last=False)
    return False
