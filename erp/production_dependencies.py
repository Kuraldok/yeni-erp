"""Validate dependency edits and preserve the order of related production jobs."""
from collections import defaultdict

from django.core.exceptions import ValidationError


def prepare_dependency_change(instance, action, reverse, pk_set, using):
    from .models import ProductionJob

    # Serialize graph edits before reading paths. Stock operations only need to
    # lock their own job and its predecessors, rather than this entire graph.
    states = dict(ProductionJob.objects.using(using).select_for_update().order_by('pk').values_list('pk', 'state'))
    field = ProductionJob._meta.get_field('preceding_jobs')
    source = field.m2m_field_name() + '_id'
    target = field.m2m_reverse_field_name() + '_id'
    edges = set(field.remote_field.through.objects.using(using).values_list(source, target))
    if action == 'pre_clear':
        changed = {edge for edge in edges if edge[1 if reverse else 0] == instance.pk}
    else:
        requested = {(pk, instance.pk) if reverse else (instance.pk, pk) for pk in pk_set or ()}
        changed = requested - edges if action == 'pre_add' else requested & edges
    if not changed:
        return {}
    dependent_ids = {dependent for dependent, _ in changed}
    if any(states.get(pk) != 'draft' for pk in dependent_ids):
        raise ValidationError('Başlatılmış veya iptal edilmiş işin önceki iş bağlantıları değiştirilemez.')
    adjacency = defaultdict(set)
    for dependent, predecessor in edges:
        adjacency[dependent].add(predecessor)
    before = {pk: sorted(adjacency[pk]) for pk in dependent_ids}
    if action == 'pre_add':
        for dependent, predecessor in changed:
            adjacency[dependent].add(predecessor)
        for dependent, predecessor in changed:
            pending, visited = [predecessor], set()
            while pending:
                current = pending.pop()
                if current == dependent:
                    raise ValidationError('Üretim bağımlılıklarında döngü oluşuyor. Bir iş kendisini doğrudan veya dolaylı olarak bekleyemez.')
                if current not in visited:
                    visited.add(current)
                    pending.extend(adjacency[current] - visited)
    return before


def ensure_no_started_following_jobs(job):
    # Starting/advancing a dependent job locks this predecessor before changing
    # its state. The caller already holds this job's row lock.
    blocking = list(job.following_jobs.filter(state__in=['running', 'completed']).order_by('pk').values_list('number', flat=True)[:5])
    if blocking:
        raise ValidationError('Bağlı üretim işleri başlamış veya tamamlanmış; önce bu işleri düzeltin: ' + ', '.join(blocking))
