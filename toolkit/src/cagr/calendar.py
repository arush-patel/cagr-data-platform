"""Session arithmetic on a venue's trading calendar.

A `Calendar` holds a venue's session dates and answers questions in sessions, not weekdays:
twenty sessions after a date, sessions between two dates, the last session on or before a
date. It does no I/O; `cagr.db.load_calendar` builds one from `ref.calendar_session`.
"""

import bisect
from collections.abc import Iterable
from datetime import date


class Calendar:
    def __init__(self, sessions: Iterable[date], venue: str = "XNSE") -> None:
        self.venue = venue
        self.sessions: list[date] = sorted(set(sessions))
        if not self.sessions:
            raise ValueError("a calendar needs at least one session")
        self._index = {d: i for i, d in enumerate(self.sessions)}

    def __len__(self) -> int:
        return len(self.sessions)

    def __repr__(self) -> str:
        return f"Calendar({self.venue}, {self.sessions[0]} to {self.sessions[-1]}, {len(self)})"

    @property
    def first(self) -> date:
        return self.sessions[0]

    @property
    def last(self) -> date:
        return self.sessions[-1]

    def is_session(self, d: date) -> bool:
        return d in self._index

    def index(self, d: date) -> int:
        """Position of session d (0 for the first session). d must be a session."""
        try:
            return self._index[d]
        except KeyError:
            raise ValueError(f"{d} is not a {self.venue} session") from None

    def on_or_before(self, d: date) -> date:
        """The last session on or before d."""
        i = bisect.bisect_right(self.sessions, d)
        if i == 0:
            raise ValueError(f"{d} is before the calendar's first session, {self.first}")
        return self.sessions[i - 1]

    def on_or_after(self, d: date) -> date:
        """The first session on or after d."""
        i = bisect.bisect_left(self.sessions, d)
        if i == len(self.sessions):
            raise ValueError(f"{d} is after the calendar's last session, {self.last}")
        return self.sessions[i]

    def shift(self, d: date, n: int) -> date:
        """The session n sessions after session d (before, for negative n)."""
        i = self.index(d) + n
        if not 0 <= i < len(self.sessions):
            raise ValueError(f"{n} sessions from {d} is outside the calendar")
        return self.sessions[i]

    def between(self, start: date, end: date) -> list[date]:
        """Sessions from start to end, both included."""
        lo = bisect.bisect_left(self.sessions, start)
        hi = bisect.bisect_right(self.sessions, end)
        return self.sessions[lo:hi]

    def count(self, start: date, end: date) -> int:
        """Sessions after start up to and including end: 1 from one session to the next."""
        return bisect.bisect_right(self.sessions, end) - bisect.bisect_right(self.sessions, start)
