"""Protect cards used by operations and enforce raw-material procurement."""
from django.core.exceptions import ValidationError
from django.db.models import Q


def has_open_work(product_id):
    from .models import ProductionJob
    return ProductionJob.objects.filter(state__in=['draft', 'running']).filter(
        Q(product_id=product_id) | Q(stock_inputs__product_id=product_id)
        | Q(consumption_recipe__inputs__product_id=product_id, consumption_recipe__inputs__active=True)
    ).exists()


def protect_product_change(product, previous):
    if previous is None:
        return
    changed = {field for field in ('unit', 'kind', 'active', 'production_area', 'supply_method')
               if getattr(previous, field) != getattr(product, field)}
    if not changed:
        return
    if has_open_work(product.pk):
        raise ValidationError({field: 'Açık üretim işinde kullanılan kartın birimi, türü, etkinliği veya üretim/temin tanımı değiştirilemez. Önce bağlı işleri kapatın.' for field in changed})
    if changed & {'unit', 'kind'}:
        from .models import OrderLine, ProductionInputSnapshot, ProductionJob, StockMovement
        from .quantities import is_fractional_count
        if 'unit' in changed and any(is_fractional_count(value, product.unit) for value in OrderLine.objects.filter(product_id=product.pk).values_list('quantity', flat=True)):
            raise ValidationError({'unit':'Kesirli miktarlı sipariş geçmişi bulunan kart Adet birimine çevrilemez; ayrı stok kartı açın.'})
        history = (StockMovement.objects.filter(product_id=product.pk).exists()
            or OrderLine.objects.filter(product_id=product.pk).exists()
            or ProductionJob.objects.filter(product_id=product.pk).exclude(state='cancelled').exists()
            or ProductionInputSnapshot.objects.filter(product_id=product.pk).exists())
        if history:
            raise ValidationError({field: 'İşlem geçmişi bulunan kartın birimi veya türü değiştirilemez; yeni stok kartı açın.' for field in changed & {'unit', 'kind'}})


def normalize_raw_product(product, previous):
    if product.kind != 'raw':
        return set()
    if previous and previous.kind == 'raw' and has_open_work(product.pk):
        return set()
    changed = set()
    for field, value in (('supply_method', 'purchase'), ('production_area', '')):
        if getattr(product, field) != value:
            setattr(product, field, value)
            changed.add(field)
    return changed
