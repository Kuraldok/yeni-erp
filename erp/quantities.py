from decimal import Decimal
from django.core.exceptions import ValidationError


def is_count_unit(unit):
    return str(unit or '').strip().casefold() == 'adet'


def is_fractional_count(value, unit):
    return value is not None and is_count_unit(unit) and Decimal(value) != Decimal(value).to_integral_value()


def validate_count(value, unit, field='quantity'):
    if is_fractional_count(value, unit):
        raise ValidationError({field:'Adet birimindeki miktar tam sayı olmalıdır.'})


def quantity_fields(instance):
    name = instance._meta.model_name
    if name == 'product':
        return [('minimum_stock', instance.minimum_stock, instance.unit)]
    if name == 'orderline':
        return [('quantity',instance.quantity,instance.unit or instance.product.unit)] if instance.product_id else []
    if name == 'stockmovement':
        return [('quantity',instance.quantity,instance.unit or instance.product.unit)] if instance.product_id else []
    if name == 'productionjob':
        return [('quantity',instance.quantity,instance.unit),('actual_quantity',instance.actual_quantity,instance.unit)]
    if name == 'productionposting':
        return [('quantity',instance.quantity,instance.unit),('scrap_quantity',instance.scrap_quantity,instance.unit)]
    if name in ('materialrequirement','consumptionrecipeline','productioninputsnapshot','stockdocumentline','stockreservation'):
        return [('quantity',instance.quantity,instance.unit)]
    return []


def enforce_counts(instance, before=None, source_bulk=False):
    if source_bulk and instance._meta.model_name == 'materialrequirement':
        return  # Original Excel values are retained and flagged, never rounded.
    for field, value, unit in quantity_fields(instance):
        if not is_fractional_count(value, unit):
            continue
        unchanged = before and before.get(field) is not None and Decimal(str(before[field])) == Decimal(value)
        if unchanged and before.get('unit',unit) == unit and before.get('product_id',getattr(instance,'product_id',None)) == getattr(instance,'product_id',None):
            continue
        validate_count(value, unit, field)


def review_quantities(instance):
    from django.utils import timezone
    from .models import QuantityReview
    for field, value, unit in quantity_fields(instance):
        identity = {'model_label':instance._meta.label_lower,'object_id':str(instance.pk),'field_name':field}
        if is_fractional_count(value, unit):
            QuantityReview.objects.update_or_create(**identity,defaults={'quantity':value,'resolved_at':None})
        else:
            for review in QuantityReview.objects.filter(**identity,resolved_at__isnull=True):
                review.resolved_at = timezone.now()
                review.save(update_fields=['resolved_at'])
