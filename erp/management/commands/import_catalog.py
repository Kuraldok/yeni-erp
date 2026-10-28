import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from erp.catalog import import_catalog, read_catalog

class Command(BaseCommand):
    help='Excel ürün/reçetelerini boşları doldurmadan aktarır; aynı dosyayı çoğaltmaz.'
    def add_arguments(self,parser):
        parser.add_argument('file')
        parser.add_argument('--check',action='store_true',help='Veri tabanına yazmadan dosyayı denetler.')
    def handle(self,*args,**options):
        path=Path(options['file'])
        try:
            content=path.read_bytes()
            if options['check']:
                rows=read_catalog(content)
                self.stdout.write(json.dumps({'rows':len(rows),'sheets':len({r['sheet'] for r in rows})},ensure_ascii=False))
                return
            batch,created=import_catalog(content,path.name)
        except (OSError,ValidationError) as exc:raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps({'created':created,'id':batch.pk,**batch.summary},ensure_ascii=False,indent=2))
