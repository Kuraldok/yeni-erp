from django.db import transaction
from django.utils import timezone

from .audit import with_actor
from .models import CatalogImport
from .routing import apply_component_routes


ROUTE_REVIEW_PERMISSIONS = ('view_catalogimport', 'view_materialrecipe', 'view_materialrequirement',
    'view_product', 'change_product', 'add_productionroute', 'change_productionroute', 'add_routestep')


def can_apply_source_routes(user):
    return all(user.has_perm('erp.' + code) for code in ROUTE_REVIEW_PERMISSIONS)


@with_actor
@transaction.atomic
def apply_source_routes(source_id, actor=None):
    batch = CatalogImport.objects.select_for_update().get(pk=source_id)
    result = apply_component_routes(source=batch)
    result['checked_at'] = timezone.now().isoformat()
    result['checked_by'] = actor.get_username() if actor else 'Sistem / komut'
    batch.summary = {**batch.summary, 'routing': result}
    batch._audit_reason = 'Aktarıma bağlı rota kuralları değerlendirildi.'
    batch.save(update_fields=['summary'])
    return result
