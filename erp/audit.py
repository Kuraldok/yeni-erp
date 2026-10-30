from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction

_actor = ContextVar('erp_audit_actor', default=None)


def current_actor():
    return _actor.get()


@contextmanager
def audit_actor(actor):
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)


def with_actor(function):
    parameters = signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        actor = parameters.bind_partial(*args, **kwargs).arguments.get('actor')
        if actor is None:
            return function(*args, **kwargs)
        with audit_actor(actor):
            return function(*args, **kwargs)
    return wrapped


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with audit_actor(request.user if request.user.is_authenticated else None):
            return self.get_response(request)


def snapshot(instance):
    return json.loads(json.dumps({field.attname:getattr(instance,field.attname)
        for field in instance._meta.concrete_fields}, cls=DjangoJSONEncoder))


def event(instance, action, before=None, reason=''):
    from .models import AuditEvent
    actor = _actor.get()
    after = {} if action == 'delete' else snapshot(instance)
    before = before or {}
    changes = {key:{'before':before.get(key),'after':after.get(key)}
        for key in set(before)|set(after) if before.get(key) != after.get(key)}
    if action == 'update' and not changes:
        return
    AuditEvent.objects.create(actor=actor, actor_label=actor.get_username() if actor else 'Sistem / komut',
        action=action, model_label=instance._meta.label_lower, object_id=str(instance.pk),
        object_label=str(instance)[:240], changes=changes, reason=reason)


class AuditedQuerySet(models.QuerySet):
    def bulk_create(self, objs, **kwargs):
        if kwargs.get('ignore_conflicts') or kwargs.get('update_conflicts'):
            raise ValueError('Denetimli toplu kayıtta çelişkiler sessizce geçilemez.')
        with transaction.atomic(using=self.db):
            from .quantities import enforce_counts, review_quantities
            objs = list(objs)
            for instance in objs:
                enforce_counts(instance,source_bulk=True)
            result = super().bulk_create(objs, **kwargs)
            for instance in result:
                event(instance, 'create')
                review_quantities(instance)
            return result

    def update(self, **kwargs):
        with transaction.atomic(using=self.db):
            old = {instance.pk:snapshot(instance) for instance in self.select_for_update()}
            count = super().update(**kwargs)
            from .quantities import enforce_counts, review_quantities
            for instance in self.model.objects.filter(pk__in=old):
                enforce_counts(instance,old[instance.pk])
                event(instance, 'update', old[instance.pk])
                review_quantities(instance)
            return count


class BusinessModel(models.Model):
    objects = AuditedQuerySet.as_manager()
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        with transaction.atomic(using=kwargs.get('using')):
            from .quantities import enforce_counts
            previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
            enforce_counts(self,snapshot(previous) if previous else None)
            return super().save(*args, **kwargs)
