from django.core.management.base import BaseCommand
from django.db import transaction
from erp.models import ProductionFlow, ProductionFlowStep

FLOWS=[
 ('wood','Ahşap üretimi','wood', ['Ahşap hammadde','Lata üretimi','Boya','Hazır ahşap parçalar']),
 ('casting','Döküm üretimi','casting',['Hammadde seçimi: Alüminyum Külçe / Pik','Kalıplama ve döküm','Kumlama','Taşlama','Delme','Boya','Hazır döküm parçalar']),
 ('finishing','Montaj ve teslimata hazırlık','shared',['Montaj','Paketleme','Teslimata hazır']),
]
class Command(BaseCommand):
    help='Tanımlanan üretim akışlarını kurar; mevcut akışları veya süreleri değiştirmez.'
    @transaction.atomic
    def handle(self,*args,**kwargs):
        for code,name,area,steps in FLOWS:
            flow,created=ProductionFlow.objects.get_or_create(code=code,defaults={'name':name,'area':area})
            if not created:continue
            for seq,title in enumerate(steps,1):
                ProductionFlowStep.objects.create(flow=flow,sequence=seq,name=title,optional_assembly=(code=='finishing' and seq==1),duration_minutes=None)
        self.stdout.write(self.style.SUCCESS('Ahşap, döküm ve ortak bitirme akışları hazır; bilinmeyen süreler boş.'))
