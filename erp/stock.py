"""Reservation-aware stock documents, fulfillment and immutable reversal entries."""
from collections import defaultdict
from decimal import Decimal, localcontext
from uuid import uuid4
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .audit import with_actor
from .models import (Order, OrderLine, Product, ProductionJob, StockDocument,
    StockDocumentLine, StockMovement, StockReservation, Warehouse, stock_balance)
from .quantities import validate_count

ZERO=Decimal(0)


def require(actor,permission):
    if not actor or not actor.has_perm('erp.'+permission):raise PermissionDenied('Bu işlem için yetkiniz yok.')


def amount(value,unit,allow_zero=False):
    from .inventory import exact_stock_quantity
    try:value=Decimal(str(value))
    except Exception as exc:raise ValidationError('Geçerli miktar girin.') from exc
    if not value.is_finite() or value<0 or (not allow_zero and value==0):raise ValidationError('Pozitif miktar girin.')
    validate_count(value,unit)
    return ZERO if value==0 else exact_stock_quantity(value)


def money(value):
    with localcontext() as context:
        context.prec=60
        return Decimal(value).quantize(Decimal('0.00000001'))


def reserved_stock(product,warehouse=None,*,job_id=None,order_line_id=None):
    query=StockReservation.objects.filter(product=product,state='active')
    if warehouse is not None:query=query.filter(warehouse=warehouse)
    if job_id is not None:query=query.exclude(job_id=job_id)
    if order_line_id is not None:query=query.exclude(order_line_id=order_line_id)
    return query.aggregate(total=Sum('quantity'))['total'] or ZERO


def available_stock(product,warehouse=None,**owners):
    return stock_balance(product,warehouse)-reserved_stock(product,warehouse,**owners)


def valuation(product,warehouse=None):
    movements=StockMovement.objects.filter(product=product).order_by('pk')
    if warehouse is not None:movements=movements.filter(warehouse=warehouse)
    quantity,value,known=ZERO,ZERO,True
    with localcontext() as context:
        context.prec=60
        for move in movements:
            sign=1 if move.kind=='in' else -1
            quantity+=sign*move.quantity
            if move.total_cost is None or move.value_adjustment is None:known=False
            else:value+=sign*(move.total_cost+move.value_adjustment)
            if quantity==0:value,known=ZERO,True
        return {'quantity':quantity,'value':money(value) if known else None,
            'average':money(value/quantity) if known and quantity>0 else None}


def average_cost(product,warehouse=None):return valuation(product,warehouse)['average']


def movement_cost(product,warehouse,quantity):
    book=valuation(product,warehouse)
    if book['value'] is None or book['quantity']<=0:return None
    with localcontext() as context:
        context.prec=60
        return money(book['value']*quantity/book['quantity'])


def fulfilled_quantity(line):
    kind='delivery' if line.order.kind=='sale' else 'receipt'
    return StockDocumentLine.objects.filter(order_line=line,document__kind=kind,document__status='posted').exclude(
        document__reversal_document__status='posted').aggregate(total=Sum('quantity'))['total'] or ZERO


def lock_stock(warehouse_ids,product_ids):
    list(Warehouse.objects.select_for_update().filter(pk__in=sorted(set(warehouse_ids))).order_by('pk'))
    return {p.pk:p for p in Product.objects.select_for_update().filter(pk__in=sorted(set(product_ids))).order_by('pk')}


def lock_order_line(line_id):
    identity=OrderLine.objects.only('order_id').get(pk=line_id)
    order=Order.objects.select_for_update().get(pk=identity.order_id)
    line=OrderLine.objects.select_for_update().select_related('product').get(pk=line_id)
    if line.order_id!=order.pk:raise ValidationError('Kalemin siparişi değişti; sayfayı yenileyin.')
    line.order=order
    return line


def consume_reservations(product,warehouse,quantity,*,job_id=None,order_line_id=None):
    query=StockReservation.objects.select_for_update().filter(product=product,warehouse=warehouse,state='active')
    query=query.filter(job_id=job_id) if job_id else query.filter(order_line_id=order_line_id)
    for reservation in query.order_by('pk'):
        used=min(quantity,reservation.quantity)
        quantity-=used
        if used==reservation.quantity:reservation.state='consumed'
        else:reservation.quantity-=used
        reservation.save()
        if quantity<=0:break


def release_job_reservations(job,reason):
    for reservation in job.reservations.filter(state='active'):
        reservation.state='released';reservation.reason=reason;reservation.save()


@with_actor
@transaction.atomic
def reserve_order_line(line_id,warehouse_id,quantity,actor):
    require(actor,'add_stockreservation')
    line=lock_order_line(line_id)
    if line.order.kind!='sale' or line.order.status not in ('draft','approved') or line.status!='active':
        raise ValidationError('Etkin ve açık bir satış kalemi seçin.')
    if not line.product.active or not line.unit or line.unit!=line.product.unit:raise ValidationError('Kart etkin olmalı ve sipariş birimiyle uyuşmalıdır.')
    products=lock_stock([warehouse_id],[line.product_id])
    quantity=amount(quantity,line.unit)
    reserved=line.reservations.filter(state='active').aggregate(total=Sum('quantity'))['total'] or ZERO
    if reserved+quantity>line.remaining_quantity:raise ValidationError('Kalan sipariş miktarından fazla rezervasyon yapılamaz.')
    if available_stock(products[line.product_id],warehouse_id)<quantity:raise ValidationError('Rezervasyon için serbest stok yetersiz.')
    return StockReservation.objects.create(product=line.product,warehouse_id=warehouse_id,order_line=line,
        quantity=quantity,unit=line.unit,created_by=actor)


@with_actor
@transaction.atomic
def release_reservation(reservation_id,actor,reason):
    require(actor,'release_stockreservation')
    if not str(reason or '').strip():raise ValidationError('Serbest bırakma nedeni girin.')
    identity=StockReservation.objects.get(pk=reservation_id)
    if identity.job_id:raise ValidationError('Üretim rezervasyonu iş iptal edildiğinde veya parti işlendiğinde güncellenir.')
    lock_order_line(identity.order_line_id)
    lock_stock([identity.warehouse_id],[identity.product_id])
    reservation=StockReservation.objects.select_for_update().get(pk=reservation_id)
    if reservation.state!='active':return reservation
    reservation.state='released';reservation.reason=reason;reservation.save()
    return reservation


def material_plan(job):
    if job.state in ('cancelled','completed'):return []
    lines=list(job.stock_inputs.select_related('product')) if job.stock_inputs.exists() else list(
        job.consumption_recipe.inputs.filter(active=True).select_related('product')) if job.consumption_recipe_id else []
    quantities=defaultdict(Decimal);products={}
    with localcontext() as context:
        context.prec=60
        for line in lines:
            if line.product_id and line.quantity is not None:
                quantities[line.product_id]+=line.quantity*job.open_quantity;products[line.product_id]=line.product
    rows=[]
    for pk,needed in quantities.items():
        product=products[pk]
        stock=stock_balance(product,job.input_warehouse_id) if job.input_warehouse_id else ZERO
        reserved=reserved_stock(product,job.input_warehouse_id,job_id=job.pk) if job.input_warehouse_id else ZERO
        rows.append({'product':product,'needed':needed,'stock':stock,'other_reserved':reserved,'available':stock-reserved,'shortage':max(ZERO,needed-stock+reserved)})
    return rows


def refresh_job_reservations(job,actor,*,strict=False):
    rows=material_plan(job)
    lock_stock([job.input_warehouse_id],[row['product'].pk for row in rows])
    release_job_reservations(job,'Kalan üretim ihtiyacı yeniden ayrıldı.')
    for row in rows:
        quantity=amount(row['needed'],row['product'].unit,allow_zero=True)
        free=available_stock(row['product'],job.input_warehouse_id)
        if strict and quantity>free:raise ValidationError(f"{row['product']}: üretime başlamak için serbest stok yetersiz; gereken {quantity}, serbest {free}.")
        quantity=min(quantity,max(ZERO,free))
        if quantity:
            StockReservation.objects.create(product=row['product'],warehouse_id=job.input_warehouse_id,job=job,
                quantity=quantity,unit=row['product'].unit,created_by=actor)


def document_number(prefix='STK'):return f'{prefix}-{timezone.now():%Y%m%d%H%M%S}-{uuid4().hex[:8]}'


def validate_document(document,lines):
    if document.kind not in ('delivery','receipt','transfer','count'):raise ValidationError('Ters fiş yalnız özgün fişten oluşturulur.')
    if not lines:raise ValidationError('En az bir fiş kalemi girin.')
    if document.kind in ('delivery','transfer','count') and not document.source_warehouse_id:raise ValidationError('Çıkış / sayım deposu seçin.')
    if document.kind in ('receipt','transfer') and not document.target_warehouse_id:raise ValidationError('Giriş deposu seçin.')
    if document.kind=='transfer' and document.source_warehouse_id==document.target_warehouse_id:raise ValidationError('Transfer depoları farklı olmalıdır.')
    if document.kind=='count' and not document.reason.strip():raise ValidationError('Sayım farkı açıklaması girin.')
    if document.kind in ('delivery','receipt'):
        required='sale' if document.kind=='delivery' else 'purchase'
        if not document.order_id or document.order.kind!=required or document.order.status!='approved':raise ValidationError('Onaylı ve fiş türüne uygun sipariş seçin.')
    elif document.order_id:raise ValidationError('Transfer / sayım fişine sipariş bağlanmaz.')
    products=set();totals=defaultdict(Decimal)
    for line in lines:
        if not line.product.active or not line.product.unit:raise ValidationError('Stok kartı etkin ve birimi tanımlı olmalıdır.')
        if line.unit and line.unit!=line.product.unit:raise ValidationError('Taslak fişin kart birimi değişmiş; yeni fiş hazırlayın.')
        amount(line.quantity,line.product.unit,allow_zero=document.kind=='count')
        if document.kind=='count' and line.product_id in products:raise ValidationError('Sayımda aynı stok kartı bir kez girilebilir.')
        products.add(line.product_id)
        if document.kind in ('delivery','receipt'):
            if not line.order_line_id or line.order_line.order_id!=document.order_id or line.order_line.status!='active' or line.order_line.product_id!=line.product_id:raise ValidationError('Fiş kalemi seçilen siparişe ve ürüne ait olmalıdır.')
            if line.order_line.unit!=line.product.unit:raise ValidationError('Siparişin kayıtlı birimi stok kartıyla uyuşmuyor.')
            totals[line.order_line_id]+=line.quantity
        elif line.order_line_id:raise ValidationError('Bu fişte sipariş kalemi seçilemez.')
    for line_id,quantity in totals.items():
        if quantity>OrderLine.objects.get(pk=line_id).remaining_quantity:raise ValidationError('Kalan sipariş miktarı aşılamaz.')


@with_actor
@transaction.atomic
def post_document(document_id,actor):
    require(actor,'post_stockdocument')
    document=StockDocument.objects.select_for_update().select_related('order').get(pk=document_id)
    if document.status=='posted':return document
    if document.order_id:
        document.order=Order.objects.select_for_update().get(pk=document.order_id)
        list(document.order.lines.select_for_update().order_by('pk'))
    lines=list(document.lines.select_related('product','order_line__order'))
    warehouse_ids=[pk for pk in (document.source_warehouse_id,document.target_warehouse_id) if pk]
    products=lock_stock(warehouse_ids,[line.product_id for line in lines])
    for line in lines:line.product=products[line.product_id]
    validate_document(document,lines)
    if document.kind=='count' and any(line.baseline_quantity is None or line.baseline_quantity!=stock_balance(line.product,document.source_warehouse_id) for line in lines):
        raise ValidationError('Sayım taslağından sonra stok değişti veya başlangıç bakiyesi bilinmiyor. Yeni sayım fişi hazırlayın.')
    for line in lines:
        common=dict(product=line.product,quantity=line.quantity,document_line=line,note=f'{document.number} · {document.get_kind_display()}',created_by=actor)
        if document.kind=='delivery':
            StockMovement.objects.create(**common,warehouse_id=document.source_warehouse_id,kind='out')
            consume_reservations(line.product,document.source_warehouse_id,line.quantity,order_line_id=line.order_line_id)
        elif document.kind=='receipt':
            cost=line.unit_cost
            if cost is None and document.order.currency=='TRY':cost=line.order_line.unit_price
            if cost is None:raise ValidationError('Dövizli mal kabulde TL cinsinden birim maliyeti açıkça girin.')
            StockMovement.objects.create(**common,warehouse_id=document.target_warehouse_id,kind='in',unit_cost=cost)
        elif document.kind=='transfer':
            outgoing=StockMovement.objects.create(**common,warehouse_id=document.source_warehouse_id,kind='out')
            StockMovement.objects.create(**common,warehouse_id=document.target_warehouse_id,kind='in',unit_cost=outgoing.unit_cost,total_cost=outgoing.total_cost)
        elif document.kind=='count':
            difference=line.quantity-stock_balance(line.product,document.source_warehouse_id)
            if difference:
                common['quantity']=abs(difference)
                cost=line.unit_cost if difference>0 and line.unit_cost is not None else average_cost(line.product,document.source_warehouse_id)
                StockMovement.objects.create(**common,warehouse_id=document.source_warehouse_id,kind='in' if difference>0 else 'out',unit_cost=cost)
    document.status='posted';document.posted_by=actor;document.posted_at=timezone.now();document.save()
    if document.order_id and all(line.remaining_quantity==0 for line in document.order.lines.filter(status='active')):
        document.order.status='completed';document.order.save(update_fields=['status'])
    return document


@with_actor
@transaction.atomic
def reverse_document(document_id,actor,reason):
    require(actor,'reverse_stockdocument')
    if not str(reason or '').strip():raise ValidationError('Ters fiş nedeni girin.')
    original=StockDocument.objects.select_for_update().get(pk=document_id)
    if original.status!='posted' or original.kind in ('reversal','production_reversal'):raise ValidationError('Yalnız işlenmiş özgün fiş ters çevrilebilir.')
    existing=StockDocument.objects.filter(reverses=original,status='posted').first()
    if existing:return existing
    if original.order_id:
        order=Order.objects.select_for_update().get(pk=original.order_id)
        list(order.lines.select_for_update().order_by('pk'))
    movements=list(StockMovement.objects.filter(document_line__document=original).select_related('product','warehouse').order_by('pk'))
    reversal=StockDocument.objects.create(number=document_number('TERS'),kind='reversal',reverses=original,order_id=original.order_id,reason=reason,created_by=actor)
    reverse_movements(movements,reversal,actor)
    reversal.status='posted';reversal.posted_by=actor;reversal.posted_at=timezone.now();reversal.save()
    if original.order_id and order.status=='completed':order.status='approved';order.save(update_fields=['status'])
    return reversal


def reverse_movements(movements,reversal,actor):
    lock_stock([m.warehouse_id for m in movements],[m.product_id for m in movements])
    # Reverse former inputs first; all legs still commit or roll back together.
    for move in sorted(movements,key=lambda m:(m.kind=='in',m.pk)):
        if move.product.unit!=move.unit:raise ValidationError('Özgün hareketin birimi kartla uyuşmuyor; ters fiş durduruldu.')
        line=StockDocumentLine.objects.create(document=reversal,product=move.product,quantity=move.quantity,unit=move.unit,unit_cost=move.unit_cost)
        StockMovement.objects.create(product=move.product,warehouse=move.warehouse,quantity=move.quantity,
            kind='out' if move.kind=='in' else 'in',unit_cost=move.unit_cost,reverses=move,document_line=line,
            total_cost=move.total_cost,note=f'{reversal.number} · ters hareket',created_by=actor)
