from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class HistoryUpgradeTests(TransactionTestCase):
    def test_existing_records_are_kept_fractional_counts_flagged_without_retroactive_stock_or_actor(self):
        previous=[('erp','0003_product_supply_method_productionjob_route_and_more')]
        current=[('erp','0011_reversal_value_adjustment')]
        executor=MigrationExecutor(connection)
        executor.migrate(previous)
        apps=executor.loader.project_state(previous).apps
        try:
            User=apps.get_model('auth','User')
            Product=apps.get_model('erp','Product')
            Warehouse=apps.get_model('erp','Warehouse')
            Movement=apps.get_model('erp','StockMovement')
            user=User.objects.create(username='legacy-user',password='unusable-test-only')
            product=Product.objects.create(code='LEGACY',name='Eski mamul',unit='adet',minimum_stock=Decimal('1.5'))
            warehouse=Warehouse.objects.create(name='Eski depo')
            movement=Movement.objects.create(product=product,warehouse=warehouse,kind='in',quantity=Decimal('1.5'),note='Eski belge',created_by=user)
            original_date=movement.created_at
            partner=apps.get_model('erp','Partner').objects.create(name='Eski cari',kind='customer')
            order=apps.get_model('erp','Order').objects.create(number='LEGACY-ORDER',kind='sale',partner=partner,date=date(2026,10,30))
            apps.get_model('erp','OrderLine').objects.create(order=order,product=product,quantity=Decimal('1.5'),unit_price=2)
            source=apps.get_model('erp','CatalogImport').objects.create(filename='legacy.xlsx',checksum='1'*64)
            apps.get_model('erp','MaterialRequirement').objects.create(source=source,sheet='Eski grup',row_number=1,
                quantity=Decimal('1.5'),unit='Adet',raw={'Birim İhtiyacı':1.5})
            flow=apps.get_model('erp','ProductionFlow').objects.create(code='legacy-flow',name='Eski akış',area='wood')
            job=apps.get_model('erp','ProductionJob').objects.create(number='LEGACY-JOB',product=product,quantity=Decimal('1.5'),unit='adet',flow=flow,state='completed',created_by=user)
            executor=MigrationExecutor(connection)
            executor.migrate(current)
            new=executor.loader.project_state(current).apps
            self.assertEqual(new.get_model('erp','Product').objects.get(pk=product.pk).minimum_stock,Decimal('1.5'))
            saved=new.get_model('erp','StockMovement').objects.get(pk=movement.pk)
            self.assertEqual(saved.quantity,Decimal('1.5'))
            self.assertEqual(saved.note,'Eski belge')
            self.assertEqual(saved.created_at,original_date)
            self.assertEqual(saved.unit,'adet')
            saved_job=new.get_model('erp','ProductionJob').objects.get(pk=job.pk)
            self.assertEqual(saved_job.stock_mode,'tracking')
            self.assertEqual(saved_job.state,'completed')
            self.assertEqual(saved_job.quantity,Decimal('1.5'))
            self.assertIsNone(saved_job.actual_quantity)
            self.assertEqual(new.get_model('erp','OrderLine').objects.get().quantity,Decimal('1.5'))
            self.assertEqual(new.get_model('erp','MaterialRequirement').objects.get().raw,{'Birim İhtiyacı':1.5})
            self.assertEqual(new.get_model('erp','QuantityReview').objects.count(),5)
            self.assertFalse(new.get_model('erp','ProductionPosting').objects.exists())
            self.assertFalse(new.get_model('erp','AuditEvent').objects.exists())
        finally:
            MigrationExecutor(connection).migrate(current)
