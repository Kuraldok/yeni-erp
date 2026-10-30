from django.db.models.signals import pre_save, post_save, pre_delete, post_delete, m2m_changed
from django.core.exceptions import ValidationError
from django.dispatch import receiver
from .audit import BusinessModel, event, snapshot


@receiver(pre_save)
def before_business_save(sender, instance, raw=False, **kwargs):
    if raw or not isinstance(instance, BusinessModel):
        return
    previous = sender.objects.filter(pk=instance.pk).first() if instance.pk else None
    instance._audit_before = snapshot(previous) if previous else None


@receiver(post_save)
def after_business_save(sender, instance, created, raw=False, **kwargs):
    if raw or not isinstance(instance, BusinessModel):
        return
    action = instance.__dict__.pop('_audit_action', 'create' if created else 'update')
    reason = instance.__dict__.pop('_audit_reason', '')
    event(instance, action, instance.__dict__.pop('_audit_before',None), reason)
    from .quantities import review_quantities
    review_quantities(instance)


@receiver(post_delete)
def after_business_delete(sender, instance, **kwargs):
    if isinstance(instance, BusinessModel):
        event(instance, 'delete', snapshot(instance))
        from .models import ConsumptionRecipeLine
        if isinstance(instance, ConsumptionRecipeLine):
            recipe = instance.recipe
            if recipe.confirmed:
                recipe.confirmed = False
                recipe.save(update_fields=['confirmed','confirmed_by','confirmed_at'])


@receiver(pre_delete)
def protect_consumption_history(sender, instance, **kwargs):
    from .models import ConsumptionRecipe, ConsumptionRecipeLine
    if isinstance(instance, ConsumptionRecipe) and instance.locked:
        raise ValidationError('Üretimde kullanılmış tüketim reçetesi silinemez.')
    if isinstance(instance, ConsumptionRecipeLine) and instance.recipe.locked:
        raise ValidationError('Üretimde kullanılmış tüketim girdisi silinemez.')


@receiver(m2m_changed)
def after_job_dependencies(sender, instance, action, reverse=False, **kwargs):
    from .models import ProductionJob
    if sender is not ProductionJob.preceding_jobs.through or reverse:
        return
    if action in ('pre_add','pre_remove','pre_clear'):
        instance._audit_dependencies = sorted(instance.preceding_jobs.values_list('pk',flat=True))
    elif action in ('post_add','post_remove','post_clear'):
        old = instance.__dict__.pop('_audit_dependencies',[])
        new = sorted(instance.preceding_jobs.values_list('pk',flat=True))
        if old != new:
            from .audit import current_actor
            from .models import AuditEvent
            actor = current_actor()
            AuditEvent.objects.create(actor=actor,actor_label=actor.get_username() if actor else 'Sistem / komut',action='update',
                model_label=instance._meta.label_lower,object_id=str(instance.pk),object_label=str(instance),
                changes={'preceding_jobs':{'before':old,'after':new}})
