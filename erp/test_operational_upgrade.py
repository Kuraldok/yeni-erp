from decimal import Decimal
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class OperationalUpgradeTests(TransactionTestCase):
    def test_existing_stock_orders_and_completed_production_survive_upgrade_without_fake_costs(self):
        previous=[('erp','0005_preserve_history_and_flag_counts')]
        current=[('erp','0011_reversal_value_adjustment')]
        executor=MigrationExecutor(connection);executor.migrate(previous)
        apps=executor.loader.project_state(previous).apps
        try:
            User=apps.get_model('auth','User');user=User.objects.create(username='operational-legacy')
            Product=apps.get_model('erp','Product')
            raw=Product.objects.create(code='OLD-RAW',name='Eski hammadde',kind='raw',unit='kg')
            main=Product.objects.create(code='OLD-MAIN',name='Eski mamul',kind='finished',unit='adet')
            warehouse=apps.get_model('erp','Warehouse').objects.create(name='Eski stok deposu')
            Movement=apps.get_model('erp','StockMovement')
            incoming=Movement.objects.create(product=raw,warehouse=warehouse,quantity=20,kind='in',unit='kg',note='Eski belge',created_by=user)
            partner=apps.get_model('erp','Partner').objects.create(name='Eski cari',kind='customer')
            order=apps.get_model('erp','Order').objects.create(number='OLD-SALE',kind='sale',partner=partner,date='2026-10-28',status='completed')
            line=apps.get_model('erp','OrderLine').objects.create(order=order,product=main,quantity=2,unit_price=12)
            flow=apps.get_model('erp','ProductionFlow').objects.create(code='old-complete',name='Eski üretim',area='wood')
            job=apps.get_model('erp','ProductionJob').objects.create(number='OLD-JOB',product=main,quantity=2,actual_quantity=2,unit='adet',flow=flow,state='completed',stock_mode='inventory',created_by=user)
            posting=apps.get_model('erp','ProductionPosting').objects.create(job=job,quantity=2,unit='adet',recipe_label='Eski reçete',input_warehouse=warehouse,output_warehouse=warehouse,created_by=user)
            out=Movement.objects.create(product=raw,warehouse=warehouse,quantity=4,kind='out',unit='kg',note='Eski tüketim',created_by=user,posting=posting)
            output=Movement.objects.create(product=main,warehouse=warehouse,quantity=2,kind='in',unit='adet',note='Eski üretim',created_by=user,posting=posting)
            before=list(Movement.objects.order_by('pk').values_list('pk','quantity','kind','unit','note','created_at','created_by_id','posting_id'))
            executor=MigrationExecutor(connection);executor.migrate(current)
            new=executor.loader.project_state(current).apps
            Movement=new.get_model('erp','StockMovement')
            self.assertEqual(list(Movement.objects.order_by('pk').values_list('pk','quantity','kind','unit','note','created_at','created_by_id','posting_id')),before)
            self.assertTrue(all(m.unit_cost is None and m.total_cost is None for m in Movement.objects.all()))
            saved=new.get_model('erp','OrderLine').objects.get(pk=line.pk)
            self.assertEqual((saved.unit,saved.product_code,saved.product_name),('adet','OLD-MAIN','Eski mamul'))
            self.assertEqual(new.get_model('erp','Order').objects.get(pk=order.pk).status,'completed')
            saved_job=new.get_model('erp','ProductionJob').objects.get(pk=job.pk)
            self.assertEqual((saved_job.state,saved_job.actual_quantity,saved_job.posting_version),('completed',Decimal(2),1))
            saved_posting=new.get_model('erp','ProductionPosting').objects.get(pk=posting.pk)
            self.assertEqual(saved_posting.quantity,2);self.assertIsNone(saved_posting.total_cost)
            self.assertFalse(new.get_model('erp','StockDocument').objects.exists())
            self.assertFalse(new.get_model('erp','StockReservation').objects.exists())
            self.assertFalse(new.get_model('erp','AuditEvent').objects.exists())
        finally:MigrationExecutor(connection).migrate(current)
