from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .audit import with_actor
from .models import ProductionJob, ProductionPosting, StockDocument
from .stock import (document_number, lock_order_line, refresh_job_reservations,
    release_job_reservations, require, reverse_movements)


@with_actor
@transaction.atomic
def cancel_job(job_id,actor,reason):
    require(actor,'cancel_productionjob')
    if not str(reason or '').strip():raise ValidationError('İptal nedeni girin.')
    identity=ProductionJob.objects.only('order_line_id').get(pk=job_id)
    if identity.order_line_id:lock_order_line(identity.order_line_id)
    job=ProductionJob.objects.select_for_update().get(pk=job_id)
    if job.state=='cancelled':return job
    if job.state=='completed':raise ValidationError('Tamamlanmış üretim önce ilgili üretim fişinden ters çevrilmelidir.')
    release_job_reservations(job,reason)
    job.state='cancelled';job.cancelled_by=actor;job.cancelled_at=timezone.now();job.cancellation_reason=reason
    job._audit_action='cancel';job._audit_reason=reason
    job.save(update_fields=['state','cancelled_by','cancelled_at','cancellation_reason'])
    return job


@with_actor
@transaction.atomic
def reverse_production(posting_id,actor,reason):
    require(actor,'reverse_stockdocument')
    if not str(reason or '').strip():raise ValidationError('Üretim ters fişi nedeni girin.')
    identity=ProductionPosting.objects.select_related('job').get(pk=posting_id)
    if identity.job.order_line_id:
        line=lock_order_line(identity.job.order_line_id)
        if line.order.status not in ('draft','approved') or line.status!='active':raise ValidationError('Önce bağlı siparişin teslimatını ters fişle düzeltip siparişi açın.')
    job=ProductionJob.objects.select_for_update().get(pk=identity.job_id)
    posting=ProductionPosting.objects.select_for_update().get(pk=posting_id)
    existing=StockDocument.objects.filter(production_posting=posting,status='posted').first()
    if existing:return existing
    reversal=StockDocument.objects.create(number=document_number('URT-TERS'),kind='production_reversal',production_posting=posting,reason=reason,created_by=actor)
    movements=list(posting.movements.select_related('product','warehouse').order_by('pk'))
    reverse_movements(movements,reversal,actor)
    reversal.status='posted';reversal.posted_by=actor;reversal.posted_at=timezone.now();reversal.save()
    job.actual_quantity=job.stock_postings.exclude(reversal__status='posted').aggregate(total=Sum('quantity'))['total'] or Decimal(0)
    job.posting_version+=1
    if job.state=='completed':
        job.state='running'
        final=job.steps.order_by('-sequence').first()
        final.completed_at=None;final.completed_by=None;final.save(update_fields=['completed_at','completed_by'])
    job.save(update_fields=['actual_quantity','posting_version','state'])
    if job.state=='running':refresh_job_reservations(job,actor)
    return reversal
