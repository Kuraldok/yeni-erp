import json
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from erp.routing import apply_component_routes


class Command(BaseCommand):
    help = 'Bileşen üretim rotalarını ve hammaddelerin dış temin rotalarını uygular; eski rota adımlarını ve açık işleri korur.'

    def handle(self,*args,**options):
        try:
            result=apply_component_routes()
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result,ensure_ascii=False,indent=2))
