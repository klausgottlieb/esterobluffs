"""Cayucos Library events fetcher (County of SLO Public Libraries).

The library calendar runs on the LibraryMarket platform. Cayucos is branch 76;
the filtered list page is:
  https://sanluisobispo.librarycalendar.com/events/list?branches%5B76%5D=76

To wire it up: open that page, click the calendar "Subscribe" / iCal export
control, copy the .ics subscription URL it produces, and paste it below.
Until LIBRARY_ICAL_URL is set, this source reports 'pending' and the annual
seed carries the calendar.
"""
from events_ical import fetch_ical

LIBRARY_ICAL_URL = ""  # <-- paste the Cayucos-branch (76) iCal subscription URL

LIST_URL = "https://sanluisobispo.librarycalendar.com/events/list?branches%5B76%5D=76"


def fetch():
    return fetch_ical(
        LIBRARY_ICAL_URL,
        source="library",
        source_type="government",
        organizer="Cayucos Library",
        area="cayucos",
        category="library",
        confidence=95,
        default_venue="Cayucos Library",
        default_url=LIST_URL,
    )
