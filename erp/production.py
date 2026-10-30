from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from .models import ProductionJob, ProductionJobStep, ProductionRoute
from .routing import is_codeless_grill_material, requirement_issues
from .audit import with_actor
from .quantities import is_fractional_count


def material_plan(recipe, quantity):
    """Workbook's flat BOM, without assuming how raw material is split among parts."""
    rows=[]
    problems=list(recipe.issues)
    lines = list(recipe.lines.select_related('component').all())
    routes = {}
    for route in ProductionRoute.objects.filter(product_id__in=[line.component_id for line in lines if line.component_id],active=True).select_related('flow').order_by('product_id','-version'):
        routes.setdefault(route.product_id,route)
    for line in lines:
        issues=requirement_issues(line)
        if is_fractional_count(line.quantity,line.unit) and not any('tam sayı' in issue for issue in issues):
            issues.append('Adet birimindeki miktar tam sayı olmalıdır.')
        if not line.component_id and not is_codeless_grill_material(line.component_kind, line.sheet, line.component_code):
            issues.append('Stok kartı eşleştirmesi yok.')
        rows.append({'line':line,'required':line.quantity*quantity if line.quantity is not None else None,'issues':issues,'route':routes.get(line.component_id)})
        problems.extend(f'{line.sheet}:{line.row_number}: {issue}' for issue in issues)
    if not rows:problems.append('Reçetenin bileşen satırı yok.')
    return rows,problems

@with_actor
@transaction.atomic
def advance_job(job_id, actor, expected, actual_quantity=None, **batch):
    if not actor or not actor.has_perm('erp.advance_productionjob'):
        raise PermissionDenied('Üretim ilerletme yetkiniz yok.')
    from .stock import lock_order_line
    identity=ProductionJob.objects.only('order_line_id').get(pk=job_id)
    if identity.order_line_id:lock_order_line(identity.order_line_id)
    job=ProductionJob.objects.select_for_update(of=('self',)).select_related('flow','product','recipe','route').get(pk=job_id)
    if job.order_line_id!=identity.order_line_id:raise ValidationError('Sipariş bağlantısı değişti; sayfayı yenileyin.')
    if job.state=='cancelled':raise ValidationError('İptal edilmiş iş ilerletilemez.')
    if job.state=='completed': raise ValidationError('İş emri zaten tamamlandı.')
    pending=job.steps.filter(completed_at__isnull=True).order_by('sequence').first()
    token='start' if job.state=='draft' else str(pending.pk) if pending else ''
    if expected!=token: raise ValidationError('İş emrinin aşaması değişti. Güncel durumu kontrol edin; işlem tekrarlanmadı.')
    if job.state=='draft':
        job.full_clean()
        if not job.route_id and job.flow.area!='shared':
            routes = list(ProductionRoute.objects.filter(product=job.product,active=True,supply_method='manufacture',flow=job.flow))
            if len(routes)>1:
                raise ValidationError('Birden fazla etkin ürün rotası var. İş emrinde kullanılacak rotayı seçin.')
            if routes:
                job.route=routes[0]
                job.full_clean()
            elif job.product.supply_method=='manufacture':
                raise ValidationError('Bu yarı mamul için seçilen akışa ait etkin üretim rotası yok.')
        if job.recipe_id and job.stock_mode == 'tracking':
            _,problems=material_plan(job.recipe,job.quantity)
            if problems:
                raise ValidationError('Reçetede eksik veya çelişkili bilgi var. Kaynak Excel düzeltilip tekrar aktarılmadan başlatılamaz.')
        previous=list(job.preceding_jobs.select_for_update().all())
        if any(p.pk==job.pk or p.state!='completed' for p in previous):
            raise ValidationError('Bağlı ahşap/döküm işleri tamamlanmadan bu iş başlatılamaz.')
        if previous and job.flow.area!='shared':
            raise ValidationError('Önceki işler yalnızca ortak montaj/paketleme akışına bağlanabilir.')
        if job.stock_mode == 'inventory':
            from .inventory import prepare_production_inputs
            prepare_production_inputs(job)
            from .stock import refresh_job_reservations
            refresh_job_reservations(job,actor,strict=True)
        if job.route_id:
            definitions=list(job.route.steps.all())
        else:
            definitions=list(job.flow.steps.all())
            definitions=[s for s in definitions if not s.optional_assembly or job.assembly_required is True]
        if not definitions:raise ValidationError('Akışın uygulanacak adımı yok.')
        ProductionJobStep.objects.bulk_create([ProductionJobStep(job=job,sequence=s.sequence,name=s.name) for s in definitions])
        job.state='running'
        job.save(update_fields=['state','route'])
        return 'İş emri başlatıldı. İlk aşama bekliyor.'
    step=job.steps.filter(completed_at__isnull=True).order_by('sequence').first()
    if not step:raise ValidationError('Tamamlanacak aşama bulunamadı.')
    if job.stock_mode == 'inventory' and not job.steps.filter(completed_at__isnull=True).exclude(pk=step.pk).exists():
        if actual_quantity is None:
            raise ValidationError('Son aşamada gerçekleşen üretim miktarını girin.')
        from .inventory import post_production
        posting=post_production(job,actor,actual_quantity,**batch)
        job.refresh_from_db()
        if not posting._closed:
            return f'Parti {posting.sequence} kaydedildi. Kalan üretim: {job.remaining_quantity} {job.unit}.'
    step.completed_at=timezone.now()
    step.completed_by=actor
    step.save(update_fields=['completed_at','completed_by'])
    if not job.steps.filter(completed_at__isnull=True).exists():
        job.state='completed'
        job.save(update_fields=['state'])
    return f'{step.name} tamamlandı.'
