from django import template
from django.template.defaultfilters import floatformat
from erp.quantities import is_count_unit, is_fractional_count

register = template.Library()


@register.filter
def count_amount(value, unit):
    return requirement_amount(value, unit) if is_count_unit(unit) else value


@register.filter
def requirement_amount(value, unit):
    """Format displayed requirements without changing their stored precision."""
    if value is None or value == '':
        return '—'
    if is_fractional_count(value,unit):
        return f'{floatformat(value,"-12")} (inceleme)'
    places = '0' if str(unit).strip().casefold() == 'adet' else '3'
    return floatformat(value, places)


@register.filter
def requirement_list_amount(value, unit):
    """The requirements list uses two decimals; other existing screens keep theirs."""
    if value is None or value == '':
        return '—'
    if is_fractional_count(value,unit):
        return f'{floatformat(value,"-12")} (inceleme)'
    places = '0' if str(unit).strip().casefold() == 'adet' else '2'
    return floatformat(value, places)
