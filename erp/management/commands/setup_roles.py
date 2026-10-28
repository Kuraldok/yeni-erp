from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission

class Command(BaseCommand):
    help = 'Standart ERP rollerini oluşturur; mevcut ek izinleri kaldırmaz.'
    def handle(self, *args, **options):
        roles = {
            'ERP yöneticisi': None,
            'Gözlemci': ['view_product','view_warehouse','view_stockmovement','view_partner','view_order','view_orderline','view_productionroute','view_routestep'],
            'Depo sorumlusu': ['view_product','view_warehouse','view_stockmovement','add_stockmovement'],
            'Sipariş sorumlusu': ['view_product','view_partner','add_partner','change_partner','view_order','add_order','change_order','view_orderline','add_orderline','change_orderline'],
            'Üretim sorumlusu': ['view_product','view_productionroute','add_productionroute','change_productionroute','view_routestep','add_routestep','change_routestep'],
        }
        read_models=['catalogimport','materialrecipe','materialrequirement','productionflow','productionflowstep','productionjob','productionjobstep']
        roles['Gözlemci'] += ['view_'+m for m in read_models]
        roles['Üretim sorumlusu'] += ['view_'+m for m in read_models]+['add_productionjob','change_productionjob','advance_productionjob']
        for name, codes in roles.items():
            group, _ = Group.objects.get_or_create(name=name)
            permissions = Permission.objects.filter(content_type__app_label='erp').exclude(codename__startswith='delete_')
            if codes is not None: permissions = permissions.filter(codename__in=codes)
            group.permissions.add(*permissions)
        self.stdout.write(self.style.SUCCESS('Roller hazır. Kullanıcılara grupları yönetim ekranından atayın.'))
