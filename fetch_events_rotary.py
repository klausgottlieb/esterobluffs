"""Rotary Club of Cayucos events fetcher (ClubRunner, club 4223).

The club calendar is at:
  https://portal.clubrunner.ca/4223/Events/Calendar

To wire it up: open that calendar, use its subscribe / iCal export link, copy
the .ics subscription URL, and paste it below. Until ROTARY_ICAL_URL is set,
this source reports 'pending' and the annual seed carries the calendar.
"""
from events_ical import fetch_ical

ROTARY_ICAL_URL = ""  # <-- paste the Rotary (club 4223) iCal subscription URL

CAL_URL = "https://portal.clubrunner.ca/4223/Events/Calendar"


def fetch():
    return fetch_ical(
        ROTARY_ICAL_URL,
        source="rotary",
        source_type="club",
        organizer="Rotary Club of Cayucos",
        area="cayucos",
        category="community",
        confidence=85,
        default_venue="Cayucos",
        default_url=CAL_URL,
    )
