from django.db import transaction
from .models import ProductionFlow, ProductionFlowStep

FLOWS = [
    ('wood', 'Ahşap üretimi', 'wood', ['Ahşap hammadde', 'Lata üretimi', 'Boya', 'Hazır ahşap parçalar']),
    ('casting', 'Döküm üretimi', 'casting', ['Hammadde seçimi: Alüminyum Külçe / Pik', 'Kalıplama ve döküm', 'Kumlama', 'Taşlama', 'Delme', 'Boya', 'Hazır döküm parçalar']),
    ('finishing', 'Montaj ve teslimata hazırlık', 'shared', ['Montaj', 'Paketleme', 'Teslimata hazır']),
]


@transaction.atomic
def ensure_workflows():
    for code, name, area, steps in FLOWS:
        flow, created = ProductionFlow.objects.get_or_create(code=code, defaults={'name': name, 'area': area})
        if not created:
            continue
        for sequence, name in enumerate(steps, 1):
            ProductionFlowStep.objects.create(flow=flow, sequence=sequence, name=name, optional_assembly=(code=='finishing' and sequence==1), duration_minutes=None)
