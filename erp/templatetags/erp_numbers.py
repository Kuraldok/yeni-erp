from django import template
from django.template.defaultfilters import floatformat

register = template.Library()


@register.filter
def requirement_amount(value, unit):
    """Format displayed requirements without changing their stored precision."""
    if value is None or value == '':
        return '—'
    places = '0' if str(unit).strip().casefold() == 'adet' else '3'
    return floatformat(value, places)
