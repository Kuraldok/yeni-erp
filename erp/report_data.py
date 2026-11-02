"""Batched report data with dated, signed production corrections."""
from collections import defaultdict
from decimal import Decimal, localcontext

from django.db.models import Count, Prefetch, Q, Sum
from django.utils import timezone

from .group_filters import filter_by_group
from .models import (OrderLine, Product, ProductionInputSnapshot, ProductionJob, ProductionPosting,
    StockDocument, StockDocumentLine, StockMovement, Warehouse)
from .quantities import is_count_unit, is_fractional_count
from .stock import money
from .stock_queries import ledger_books, reservation_totals


def quantity_cell(value, unit):
    if value is None:
        return None
    if is_fractional_count(value, unit):
        return f'{value.normalize()} (inceleme)'
    return int(value) if is_count_unit(unit) else value.normalize()


def period_filter(query, field, start, end):
    if start:
        query = query.filter(**{field + '__date__gte': start})
    if end:
        query = query.filter(**{field + '__date__lte': end})
    return query


def stock_movements(user, category, end, warehouse):
    products = filter_by_group(Product.objects.all(), category, user)
    movements = StockMovement.objects.filter(product__in=products)
    if warehouse:
        movements = movements.filter(warehouse=warehouse)
    if end:
        movements = movements.filter(created_at__date__lte=end)
    return movements


def stock_pairs(user, category, end, warehouse):
    return stock_movements(user, category, end, warehouse).order_by(
        'product__code', 'warehouse__name', 'product_id', 'warehouse_id',
    ).values('product_id', 'warehouse_id').distinct()


def stock_rows(user, category, start, end, warehouse, pairs=None):
    movements = stock_movements(user, category, end, warehouse)
    if pairs is not None:
        selected = Q(pk__in=[])
        for pair in pairs:
            selected |= Q(product_id=pair['product_id'], warehouse_id=pair['warehouse_id'])
        movements = movements.filter(selected)
    movements = movements.order_by('pk').only('product_id', 'warehouse_id', 'kind', 'quantity', 'total_cost', 'value_adjustment', 'created_at')
    books = ledger_books(movements.iterator(chunk_size=2000), start)
    products = Product.objects.in_bulk({key[0] for key in books})
    warehouses = Warehouse.objects.in_bulk({key[1] for key in books})
    historical = bool(end and end < timezone.localdate())
    reserved = {} if historical or not books else reservation_totals(products, warehouses)
    rows = []
    with localcontext() as context:
        context.prec = 60
        for key in sorted(books, key=lambda key: (products[key[0]].code, warehouses[key[1]].name)):
            book, product, location = books[key], products[key[0]], warehouses[key[1]]
            held = None if historical else reserved.get(key, Decimal(0))
            quantities = [book['opening'], book['in'], book['out'], book['quantity'], held,
                None if held is None else book['quantity'] - held]
            rows.append([product.code, product.name, location.name, product.get_unit_display(),
                *[quantity_cell(value, product.unit) for value in quantities],
                money(book['value']) if book['known'] else None,
                book['adjustment'] if book['adjustment_known'] else None])
    return ['Kod','Ürün','Depo','Birim','Açılış','Dönem giriş','Dönem çıkış','Kapanış','Ayrılan (güncel)','Serbest (güncel)','Stok değeri (TL)','Ters fiş değer farkı (dönem TL)'], rows


def correction_totals(job_ids, start, end):
    query = StockMovement.objects.filter(document_line__document__production_posting__job_id__in=job_ids,
        document_line__document__status='posted')
    query = period_filter(query, 'created_at', start, end)
    return {row['document_line__document__production_posting__job_id']:
        (None if row['unknown'] else row['total'] or Decimal(0), True) for row in query.order_by().values(
            'document_line__document__production_posting__job_id').annotate(
                total=Sum('value_adjustment'), unknown=Count('pk', filter=Q(value_adjustment__isnull=True)))}


def report_posts():
    return ProductionPosting.objects.select_related('reversal').prefetch_related(Prefetch('movements',
        queryset=StockMovement.objects.filter(kind='out').only('posting_id', 'product_id', 'quantity'), to_attr='_report_inputs'))


def in_period(value, start, end):
    day = timezone.localdate(value)
    return (not start or day >= start) and (not end or day <= end)


def production_totals(job, start=None, end=None):
    posts = getattr(job, '_report_posts', None)
    if posts is None:
        posts = list(report_posts().filter(job=job))
    weights = defaultdict(int)
    activity = False
    for post in posts:
        if in_period(post.created_at, start, end):
            weights[post.pk] += 1
            activity = True
        reversal = getattr(post, 'reversal', None)
        if reversal and reversal.status == 'posted' and in_period(reversal.posted_at or reversal.created_at, start, end):
            weights[post.pk] -= 1
            activity = True
    selected = [(post, weights[post.pk]) for post in posts if weights[post.pk]]
    with localcontext() as context:
        context.prec = 60
        good = sum((sign * post.quantity for post, sign in selected), Decimal(0))
        scrap = sum((sign * post.scrap_quantity for post, sign in selected), Decimal(0))
        labor = sum((sign * post.labor_cost for post, sign in selected), Decimal(0))
        overhead = sum((sign * post.overhead_cost for post, sign in selected), Decimal(0))
        material = sum((sign * post.material_cost for post, sign in selected), Decimal(0)) if activity and all(post.material_cost is not None for post, _ in selected) else None
        total = money(material + labor + overhead) if material is not None else None
        inputs = defaultdict(Decimal)
        for post, sign in selected:
            for move in post._report_inputs:
                inputs[move.product_id] += sign * move.quantity
        rates = getattr(job, '_report_rates', None)
        if rates is None:
            rates = list(job.stock_inputs.select_related('product'))
        amounts, labels = defaultdict(Decimal), {}
        for line in rates:
            amounts[line.product_id] += line.quantity
            labels[line.product_id] = (line.product.code, line.unit)
        variances = [f'{labels[pk][0]}: reçete {rate*(good+scrap)}, gerçek {inputs[pk]}, fark {inputs[pk]-rate*(good+scrap)} {labels[pk][1]}' for pk, rate in amounts.items()]
    correction = getattr(job, '_report_correction', None)
    if correction is None:
        correction = correction_totals([job.pk], start, end).get(job.pk, (Decimal(0), False))
    return {'good':good, 'scrap':scrap, 'labor':labor, 'overhead':overhead, 'material':material,
        'total':total, 'variance':' ; '.join(variances), 'adjustment':correction[0], 'has_correction':correction[1], 'activity':activity}


def production_jobs(user, category, start, end):
    jobs = filter_by_group(ProductionJob.objects.select_related('product', 'order_line__order'), category, user)
    if start or end:
        posted = period_filter(ProductionPosting.objects.all(), 'created_at', start, end).values('job_id')
        reversed_jobs = period_filter(StockDocument.objects.filter(kind='production_reversal', status='posted'),
            'posted_at', start, end).values('production_posting__job_id')
        created = period_filter(ProductionJob.objects.all(), 'created_at', start, end).values('pk')
        jobs = jobs.filter(Q(pk__in=posted) | Q(pk__in=reversed_jobs) | Q(pk__in=created))
    return jobs.order_by('-pk')


def production_rows(user, category, start, end, jobs=None):
    jobs = production_jobs(user, category, start, end) if jobs is None else jobs
    jobs = list(jobs.prefetch_related(
        Prefetch('stock_postings', queryset=report_posts(), to_attr='_report_posts'),
        Prefetch('stock_inputs', queryset=ProductionInputSnapshot.objects.select_related('product'), to_attr='_report_rates')))
    corrections = correction_totals([job.pk for job in jobs], start, end) if jobs else {}
    rows = []
    for job in jobs:
        job._report_correction = corrections.get(job.pk, (Decimal(0), False))
        totals = production_totals(job, start, end)
        rows.append([job.number, job.product.code, job.order_line.order.number if job.order_line_id else '',
            job.get_state_display(), job.unit, *[quantity_cell(value, job.unit) for value in
                (job.quantity, totals['good'], totals['scrap'], job.open_quantity)], totals['material'], totals['labor'],
            totals['overhead'], totals['total'], totals['adjustment'], totals['variance']])
    return ['İş','Ürün','Sipariş','Durum','Birim','Plan (toplam)','Sağlam (dönem)','Fire (dönem)','Kalan (toplam)','Malzeme TL','İşçilik TL','Diğer TL','Toplam TL','Ters fiş değer farkı TL','Reçete / gerçek tüketim'], rows


def order_lines(user, category, start, end):
    lines = filter_by_group(OrderLine.objects.select_related('order', 'product'), category, user)
    if start:
        lines = lines.filter(order__date__gte=start)
    if end:
        lines = lines.filter(order__date__lte=end)
    return lines.order_by('-order__date', 'order_id', 'pk')


def order_rows(user, category, start, end, lines=None):
    lines = list(order_lines(user, category, start, end) if lines is None else lines)
    fulfilled = StockDocumentLine.objects.filter(order_line_id__in=[line.pk for line in lines], document__status='posted').filter(
        Q(document__kind='delivery', order_line__order__kind='sale') | Q(document__kind='receipt', order_line__order__kind='purchase')
    ).exclude(document__reversal_document__status='posted').order_by().values('order_line_id').annotate(total=Sum('quantity'))
    amounts = {row['order_line_id']: row['total'] for row in fulfilled}
    rows = []
    for line in lines:
        accepted = amounts.get(line.pk, Decimal(0))
        remaining = Decimal(0) if line.status == 'cancelled' or line.order.status in ('completed', 'cancelled') else max(Decimal(0), line.quantity - accepted)
        rows.append([line.order.number, line.order.get_kind_display(), line.order.get_status_display(), line.product_code,
            line.unit, *[quantity_cell(value, line.unit) for value in (line.quantity, accepted, remaining)], line.get_status_display()])
    return ['Sipariş','Tür','Durum','Ürün','Birim','Sipariş miktarı','Teslim / kabul','Kalan','Kalem durumu'], rows
