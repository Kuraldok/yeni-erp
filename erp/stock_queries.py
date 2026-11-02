"""Batched ledger reads shared by stock cards, alerts and reports."""
from decimal import Decimal, localcontext

from django.db.models import Case, DecimalField, F, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import StockMovement, StockReservation


def stock_products(products):
    number = DecimalField(max_digits=38, decimal_places=12)
    balances = StockMovement.objects.filter(product_id=OuterRef('pk')).order_by().values('product_id').annotate(
        total=Sum(Case(When(kind='in', then=F('quantity')), default=-F('quantity'), output_field=number)),
    ).values('total')
    return products.annotate(stock_quantity=Coalesce(Subquery(balances, output_field=number), Value(Decimal(0)), output_field=number))


def reservation_totals(product_ids=None, warehouse_ids=None):
    query = StockReservation.objects.filter(state='active')
    if product_ids is not None:
        query = query.filter(product_id__in=product_ids)
    if warehouse_ids is not None:
        query = query.filter(warehouse_id__in=warehouse_ids)
    return {(row['product_id'], row['warehouse_id']): row['total'] for row in
            query.order_by().values('product_id', 'warehouse_id').annotate(total=Sum('quantity'))}


def ledger_books(movements, start=None):
    books = {}
    with localcontext() as context:
        context.prec = 60
        for move in movements:
            key = (move.product_id, move.warehouse_id)
            book = books.setdefault(key, {name: Decimal(0) for name in ('opening', 'in', 'out', 'quantity', 'value', 'adjustment')})
            book.setdefault('known', True)
            book.setdefault('adjustment_known', True)
            sign = 1 if move.kind == 'in' else -1
            in_period = not start or timezone.localdate(move.created_at) >= start
            if in_period:
                book[move.kind] += move.quantity
            else:
                book['opening'] += sign * move.quantity
            book['quantity'] += sign * move.quantity
            if move.total_cost is None or move.value_adjustment is None:
                book['known'] = False
            else:
                book['value'] += sign * (move.total_cost + move.value_adjustment)
            if in_period:
                if move.value_adjustment is None:
                    book['adjustment_known'] = False
                else:
                    book['adjustment'] += move.value_adjustment
            if book['quantity'] == 0:
                book['value'], book['known'] = Decimal(0), True
    return books


def stock_card_balances(user, product):
    if not all(user.has_perm('erp.' + permission) for permission in ('view_stockmovement', 'view_warehouse')):
        return []
    from .models import Warehouse
    from .stock import money
    warehouses = list(Warehouse.objects.all())
    movements = StockMovement.objects.filter(product=product).order_by('pk').only(
        'product_id', 'warehouse_id', 'kind', 'quantity', 'total_cost', 'value_adjustment', 'created_at',
    )
    books = ledger_books(movements.iterator(chunk_size=2000))
    can_reserve = user.has_perm('erp.view_stockreservation')
    reserved = reservation_totals([product.pk]) if can_reserve else {}
    rows = []
    for warehouse in warehouses:
        book = books.get((product.pk, warehouse.pk))
        quantity = book['quantity'] if book else Decimal(0)
        value = money(book['value']) if book and book['known'] else Decimal(0) if not book else None
        with localcontext() as context:
            context.prec = 60
            average = money(value / quantity) if value is not None and quantity > 0 else None
            held = reserved.get((product.pk, warehouse.pk), Decimal(0)) if can_reserve else None
            available = quantity - held if held is not None else None
        rows.append({'warehouse': warehouse, 'quantity': quantity, 'reserved': held,
            'available': available,
            'valuation': {'quantity': quantity, 'value': value, 'average': average}})
    return rows
