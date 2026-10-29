"""Read-only schedule projection and consecutive published active days."""
from collections import Counter
from datetime import timedelta

from django import forms
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time

from .public_service import PUBLIC_TIMEZONE


def calendar_date(value):
    try:
        return parse_date(str(value or ''))
    except ValueError:
        return None


class ScheduleNavigationForm(forms.Form):
    intramurals_start_date = forms.DateField(required=False, label='Official intramurals start date',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    non_competition_dates = forms.CharField(required=False, label='Non-competition dates',
        max_length=5000, widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
            'placeholder': '2026-11-01\n2026-11-02'}))

    def clean_non_competition_dates(self):
        raw = self.cleaned_data['non_competition_dates'].replace(',', '\n').split()
        dates = [calendar_date(value) for value in raw]
        if any(value is None for value in dates):
            raise forms.ValidationError('Enter valid dates in YYYY-MM-DD format, separated by commas or new lines.')
        if len(set(dates)) > 366:
            raise forms.ValidationError('Use no more than 366 non-competition dates.')
        start = self.cleaned_data.get('intramurals_start_date')
        if dates and not start:
            raise forms.ValidationError('Set the official start date before excluding dates.')
        if start and any(value <= start for value in dates):
            raise forms.ValidationError('Excluded dates must be after the official competition start date.')
        return sorted({value.isoformat() for value in dates})


def active_schedule_days(rows):
    """Number unique, populated public schedule dates before any viewer filters."""
    counts = Counter(row['date'] for row in rows if row['date'])
    return {date: {'number': number, 'count': counts[date]}
            for number, date in enumerate(sorted(counts), 1)}


def expand_schedule_rows(rows, events):
    """Project dated criteria sessions or inclusive event spans; never duplicate fixtures."""
    today = timezone.localdate(timezone=PUBLIC_TIMEZONE)
    expanded = []
    for row in rows:
        event = events.get(row['public_event_id']) if not row['fixture_id'] else None
        if not event:
            expanded.append(row)
            continue
        sessions = []
        if row['event_kind'] == 'criteria':
            for stage in event.rounds_config or []:
                if not isinstance(stage, dict) or stage.get('enabled') is False:
                    continue
                schedule = stage.get('schedule')
                if not isinstance(schedule, dict):
                    continue
                date = calendar_date(schedule.get('date'))
                if not date:
                    continue
                try:
                    start_time = parse_time(str(schedule.get('start_time') or ''))
                    end_time = parse_time(str(schedule.get('end_time') or ''))
                except ValueError:
                    start_time, end_time = None, None
                sessions.append({**row, 'date': date, 'time': start_time,
                    'end_time': end_time, 'round': str(stage.get('name') or '')[:80],
                    'public_venue': str(schedule.get('venue') or event.venue)[:200]})
        if not sessions:
            date = event.event_date
            end = max(date, event.end_date or date)
            # An event span is a date range, not a set of invented bracket matches.
            while date <= end:
                sessions.append({**row, 'date': date})
                if date == end:
                    break
                date += timedelta(days=1)
        for session in sessions:
            session['public_status'] = ('completed' if event.status == 'completed' else
                'upcoming' if event.status == 'upcoming' or session['date'] > today else 'ongoing')
        expanded.extend(sessions)
    return expanded
