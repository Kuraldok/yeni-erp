from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from .audit import with_actor
from .models import Order, OrderLine


@with_actor
@transaction.atomic
def cancel_line(line_id, actor, reason):
    if not actor.has_perm('erp.cancel_orderline'):
        raise PermissionDenied('Sipariş kalemi iptal yetkiniz yok.')
    reason = str(reason or '').strip()
    if not reason or len(reason) > 2000:
        raise ValidationError('İptal nedeni girin (en fazla 2000 karakter).')
    identity = OrderLine.objects.only('order_id').get(pk=line_id)
    order = Order.objects.select_for_update().get(pk=identity.order_id)
    line = OrderLine.objects.select_for_update().get(pk=line_id)
    if line.order_id != order.pk:
        raise ValidationError('Kalemin siparişi değişmiş. Sayfayı yenileyin.')
    if line.status == 'cancelled':
        return line, False
    if order.status != 'draft':
        raise ValidationError('İptal için önce siparişi taslağa alıp kaydedin.')
    if line.fulfilled_quantity or line.production_jobs.filter(state='running').exists():
        raise ValidationError('Önce teslimat / mal kabul fişlerini ters çevirin ve çalışan üretimi iptal edin.')
    for reservation in line.reservations.filter(state='active'):
        reservation.state='released';reservation.reason=reason;reservation.save()
    line.status = 'cancelled'
    line.cancelled_by = actor
    line.cancelled_at = timezone.now()
    line.cancellation_reason = reason
    line._audit_action = 'cancel'
    line._audit_reason = reason
    line.save(update_fields=['status','cancelled_by','cancelled_at','cancellation_reason'])
    return line, True
