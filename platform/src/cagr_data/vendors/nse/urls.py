"""Fill a raw.feed url_template for one date.

Placeholders: {YYYY}, {MM}, {DD} (zero-padded) and {MON} (upper-case English month, JAN...DEC),
the forms NSE's archive paths use.
"""

from datetime import date

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def build_url(template: str, day: date) -> str:
    return template.format(
        YYYY=f"{day.year:04d}",
        MM=f"{day.month:02d}",
        DD=f"{day.day:02d}",
        MON=_MONTHS[day.month - 1],
    )
