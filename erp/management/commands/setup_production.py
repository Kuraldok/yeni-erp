from django.core.management.base import BaseCommand
from erp.workflows import ensure_workflows


class Command(BaseCommand):
    help = 'Tanımlanan üretim akışlarını kurar; mevcut akışları veya süreleri değiştirmez.'

    def handle(self, *args, **kwargs):
        ensure_workflows()
        self.stdout.write(self.style.SUCCESS('Ahşap, döküm ve ortak bitirme akışları hazır; bilinmeyen süreler boş.'))
