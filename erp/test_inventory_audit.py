from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import OperationalError, transaction
from django.test import TestCase
from django.urls import reverse

from .audit import audit_actor
from .catalog import import_catalog
from .inventory import copy_consumption_recipe, post_production
from .models import (AuditEvent, ConsumptionRecipe, ConsumptionRecipeLine, MaterialRecipe,
    MaterialRequirement, Order, OrderLine, Partner, Product, ProductionFlow, ProductionInputSnapshot,
    ProductionJob, ProductionPosting, QuantityReview, StockMovement, Warehouse, stock_balance)
from .operations import cancel_line
from .production import advance_job, material_plan
from .test_routing import workbook


class InventoryAuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles',verbosity=0)
        call_command('setup_production',verbosity=0)
        cls.admin=User.objects.create_superuser('inventory-admin',password='Inventory-Test-4862')
        cls.operator=User.objects.create_user('inventory-operator')
        cls.operator.groups.add(Group.objects.get(name='Üretim sorumlusu'))
        cls.observer=User.objects.create_user('inventory-viewer')
        cls.observer.groups.add(Group.objects.get(name='Gözlemci'))
        cls.raw=Product.objects.create(code='WOOD',name='Ahşap',kind='raw',unit='kg')
        cls.semi=Product.objects.create(code='SEMI',name='Ahşap bölüm',kind='semi',unit='adet')
        cls.main=Product.objects.create(code='MAIN',name='Mamul',kind='finished',unit='adet')
        cls.paint=Product.objects.create(code='PAINT',name='Boya',kind='raw',unit='kg')
        cls.source=Warehouse.objects.create(name='Girdi deposu')
        cls.output=Warehouse.objects.create(name='Ürün deposu')
        cls.customer=Partner.objects.create(name='Stok testi müşteri',kind='customer')

    def setUp(self):
        self.client.force_login(self.admin)

    def stock(self,product,quantity,warehouse=None):
        return StockMovement.objects.create(product=product,quantity=Decimal(str(quantity)),
            warehouse=warehouse or self.source,kind='in',note='Test açılış',created_by=self.admin)

    def recipe(self,product=None,inputs=None):
        product=product or self.semi
        version=ConsumptionRecipe.objects.filter(product=product).count()+1
        with audit_actor(self.admin):
            recipe=ConsumptionRecipe.objects.create(product=product,name='Doğrudan tüketim',unit=product.unit,version=version)
            for material,quantity in inputs or [(self.raw,Decimal('5'))]:
                ConsumptionRecipeLine.objects.create(recipe=recipe,product=material,quantity=Decimal(str(quantity)),unit=material.unit)
            recipe.confirmed=True
            recipe.full_clean()
            recipe.save()
        return recipe

    def job(self,consumption=None,**kwargs):
        consumption=consumption or self.recipe()
        attrs={'number':f'INV-{ProductionJob.objects.count()+1}','product':consumption.product,
            'quantity':2,'unit':consumption.unit,'flow':ProductionFlow.objects.get(code='wood'),
            'created_by':self.admin,'consumption_recipe':consumption,'input_warehouse':self.source,'output_warehouse':self.output}
        attrs.update(kwargs)
        return ProductionJob.objects.create(**attrs)

    def last_step(self,job,actor=None):
        actor=actor or self.admin
        advance_job(job.pk,actor,'start')
        steps=list(job.steps.all())
        for step in steps[:-1]:
            advance_job(job.pk,actor,str(step.pk))
        return steps[-1]

    def finish(self,job,quantity=2,actor=None):
        actor=actor or self.admin
        last=self.last_step(job,actor)
        advance_job(job.pk,actor,str(last.pk),actual_quantity=Decimal(str(quantity)))
        job.refresh_from_db()

    def test_adet_rules_cover_models_and_other_units_keep_decimals(self):
        order=Order.objects.create(number='INT',kind='sale',partner=self.customer,date=date(2026,10,30))
        operations=[
            lambda:Product.objects.create(code='BAD',unit='adet',minimum_stock=Decimal('1.5')),
            lambda:OrderLine.objects.create(order=order,product=self.main,quantity=Decimal('1.5'),unit_price=Decimal('2.25')),
            lambda:self.stock(self.semi,'1.5'),
            lambda:self.job(quantity=Decimal('1.5')),
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ValidationError):operation()
        order.kind='purchase'
        order.partner=Partner.objects.create(name='Tedarikçi',kind='supplier')
        order.save()
        line=OrderLine.objects.create(order=order,product=self.raw,quantity=Decimal('1.375'),unit_price=Decimal('2.25'))
        self.assertEqual(line.total,Decimal('3.09'))
        self.stock(self.raw,'1.375')
        self.assertEqual(stock_balance(self.raw,self.source),Decimal('1.375'))

    def test_fractional_source_is_preserved_reviewed_and_blocks_use(self):
        batch,_=import_catalog(workbook([('Soba',[[1,'SRC','Soba','COUNT','Gövde','Yarı Mamul',1.5,'Adet']])]),'fraction.xlsx',self.admin)
        line=MaterialRequirement.objects.get(source=batch)
        self.assertEqual(line.quantity,Decimal('1.5'))
        self.assertEqual(line.raw['Birim İhtiyacı'],1.5)
        self.assertTrue(any('tam sayı' in issue for issue in line.issues))
        review=QuantityReview.objects.get(model_label='erp.materialrequirement',object_id=str(line.pk))
        self.assertEqual(review.quantity,Decimal('1.5'))
        self.assertIsNone(review.resolved_at)
        _,issues=material_plan(line.recipe,Decimal(2))
        self.assertTrue(any('tam sayı' in issue for issue in issues))
        response=self.client.get(reverse('listing',args=['recete-satirlari']))
        self.assertContains(response,'inceleme')
        self.assertEqual(MaterialRequirement.objects.get(pk=line.pk).quantity,Decimal('1.5'))

    def test_integer_checks_apply_to_order_screen_and_stock_screen(self):
        payload={'number':'BAD-WEB','kind':'sale','partner':self.customer.pk,'status':'draft','date':'2026-10-30','currency':'TRY',
            'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-0-product':self.main.pk,'lines-0-quantity':'1.5','lines-0-unit_price':'2.25'}
        response=self.client.post(reverse('create',args=['siparisler']),payload)
        self.assertEqual(response.status_code,200)
        self.assertFalse(Order.objects.filter(number='BAD-WEB').exists())
        self.assertContains(response,'tam sayı')
        response=self.client.post(reverse('create',args=['stok']),{'product':self.semi.pk,'warehouse':self.source.pk,'kind':'in','quantity':'1.5','note':'Hatalı adet'})
        self.assertContains(response,'tam sayı')
        self.assertFalse(StockMovement.objects.exists())

    def test_adet_values_display_without_fraction_and_calculation_rejects_fractional_output(self):
        self.stock(self.main,2)
        self.main.minimum_stock=Decimal('4.000')
        self.main.save()
        order=Order.objects.create(number='DISPLAY',kind='sale',partner=self.customer,date=date(2026,10,30),status='draft')
        line=OrderLine.objects.create(order=order,product=self.main,quantity=2,unit_price=Decimal('3.50'))
        for slug in ('urunler','stok','siparis-kalemleri'):
            response=self.client.get(reverse('listing',args=[slug]))
            values=response.context['rows'][0]['values']
            index={'urunler':5,'stok':3,'siparis-kalemleri':2}[slug]
            self.assertEqual(str(values[index]),'4' if slug=='urunler' else '2')
        batch,_=import_catalog(workbook([('Soba',[[1,'DISPLAY-OUTPUT','Mamul','RAW-OUTPUT','Pik','Hammadde',2,'KG']])]),'display.xlsx',self.admin)
        recipe=MaterialRecipe.objects.get(source=batch)
        recipe.product.unit='adet'
        recipe.product.save()
        response=self.client.get(reverse('recipe_detail',args=[recipe.pk]),{'quantity':'1.5'})
        self.assertContains(response,'tam sayı olmalıdır')
        self.assertEqual(response.context['rows'],[])
        cancel_line(line.pk,self.admin,'Geçmişte göster')
        order.status='cancelled'
        order.save()
        response=self.client.get(reverse('edit',args=['siparisler',order.pk]))
        self.assertEqual(list(response.context['existing_lines']),[])
        self.assertEqual(len(response.context['cancelled_lines']),1)

    def test_unit_change_cannot_reinterpret_fractional_order_history_as_adet(self):
        product=Product.objects.create(code='UNIT-CHANGE',kind='finished',unit='kg')
        order=Order.objects.create(number='UNIT-CHANGE',kind='sale',partner=self.customer,date=date(2026,10,30))
        OrderLine.objects.create(order=order,product=product,quantity=Decimal('1.5'),unit_price=2)
        response=self.client.post(reverse('edit',args=['urunler',product.pk]),{
            'code':product.code,'kind':'finished','unit':'adet','minimum_stock':0,'active':'on'})
        self.assertContains(response,'Kesirli miktarlı sipariş geçmişi')
        product.refresh_from_db()
        self.assertEqual(product.unit,'kg')

    def test_cancellation_keeps_line_excludes_total_and_has_actor_time_reason(self):
        order=Order.objects.create(number='CANCEL',kind='sale',partner=self.customer,date=date(2026,10,30))
        line=OrderLine.objects.create(order=order,product=self.main,quantity=2,unit_price=Decimal('3.50'))
        _,created=cancel_line(line.pk,self.admin,'Yanlış ürün seçildi')
        self.assertTrue(created)
        line.refresh_from_db()
        self.assertEqual(line.status,'cancelled')
        self.assertEqual(line.cancelled_by,self.admin)
        self.assertIsNotNone(line.cancelled_at)
        self.assertEqual(line.cancellation_reason,'Yanlış ürün seçildi')
        self.assertEqual(line.quantity,2)
        self.assertEqual(line.unit_price,Decimal('3.50'))
        self.assertEqual(order.total,0)
        event=AuditEvent.objects.get(action='cancel',model_label='erp.orderline',object_id=str(line.pk))
        self.assertEqual(event.actor,self.admin)
        self.assertEqual(event.reason,line.cancellation_reason)
        self.assertFalse(cancel_line(line.pk,self.admin,'Yeniden tıklama')[1])
        self.assertEqual(AuditEvent.objects.filter(action='cancel',object_id=str(line.pk)).count(),1)
        self.assertEqual(self.client.get(reverse('edit',args=['siparis-kalemleri',line.pk])).status_code,403)
        order.status='approved'
        with self.assertRaises(ValidationError):order.full_clean()
        self.assertFalse(StockMovement.objects.exists())

    def test_cancellation_requires_permission_reason_and_draft(self):
        order=Order.objects.create(number='LOCK',kind='sale',partner=self.customer,date=date(2026,10,30))
        line=OrderLine.objects.create(order=order,product=self.main,quantity=1,unit_price=1)
        with self.assertRaises(ValidationError):cancel_line(line.pk,self.admin,'  ')
        with self.assertRaises(PermissionDenied):cancel_line(line.pk,self.observer,'Neden')
        order.status='approved'
        order.save()
        with self.assertRaises(ValidationError):cancel_line(line.pk,self.admin,'Neden')
        line.refresh_from_db()
        self.assertEqual(line.status,'active')

    def test_cancellation_ui_post_only_mutation_and_history_visible(self):
        order=Order.objects.create(number='UI-CANCEL',kind='sale',partner=self.customer,date=date(2026,10,30))
        line=OrderLine.objects.create(order=order,product=self.main,quantity=1,unit_price=1)
        url=reverse('cancel_order_line',args=[line.pk])
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(OrderLine.objects.get(pk=line.pk).status,'active')
        self.assertEqual(self.client.post(url,{'reason':'Fazla satır'}).status_code,302)
        response=self.client.get(reverse('edit',args=['siparisler',order.pk]))
        self.assertContains(response,'İptal edilen kalemler')
        self.assertEqual(response.context['formset'].initial_form_count(),0)

    def test_audit_create_update_and_delete_have_stable_identity(self):
        with audit_actor(self.admin):
            warehouse=Warehouse.objects.create(name='Silinecek depo')
            identity=str(warehouse.pk)
            warehouse.name='Güncellenmiş depo'
            warehouse.save()
            warehouse.delete()
        logs=AuditEvent.objects.filter(model_label='erp.warehouse',object_id=identity).order_by('pk')
        self.assertEqual(list(logs.values_list('action',flat=True)),['create','update','delete'])
        self.assertTrue(all(log.actor_id==self.admin.pk and log.actor_label==self.admin.username and log.created_at for log in logs))
        self.assertEqual(logs.last().changes['name']['before'],'Güncellenmiş depo')
        self.assertEqual(logs.last().object_label,'Güncellenmiş depo')

    def test_request_actor_is_recorded_and_system_actions_are_explicit(self):
        self.client.post(reverse('create',args=['depolar']),{'name':'Web deposu'})
        warehouse=Warehouse.objects.get(name='Web deposu')
        event=AuditEvent.objects.get(model_label='erp.warehouse',object_id=str(warehouse.pk),action='create')
        self.assertEqual(event.actor,self.admin)
        system=Warehouse.objects.create(name='Komut deposu')
        event=AuditEvent.objects.get(model_label='erp.warehouse',object_id=str(system.pk),action='create')
        self.assertIsNone(event.actor)
        self.assertEqual(event.actor_label,'Sistem / komut')

    def test_bulk_import_audit_records_actor_including_source_rows(self):
        batch,_=import_catalog(workbook([('Soba',[[1,'AUD-P','Soba','AUD-C','Pik','Hammadde',2,'KG']])]),'audit.xlsx',self.admin)
        line=MaterialRequirement.objects.get(source=batch)
        event=AuditEvent.objects.get(model_label='erp.materialrequirement',object_id=str(line.pk),action='create')
        self.assertEqual(event.actor,self.admin)
        event=AuditEvent.objects.get(model_label='erp.product',object_id=str(line.component_id),action='create')
        self.assertEqual(event.actor,self.admin)

    def test_audit_is_immutable_and_read_access_is_restricted(self):
        event=AuditEvent.objects.first()
        with self.assertRaises(ValidationError):event.delete()
        with self.assertRaises(ValidationError):event.save()
        with self.assertRaises(ValidationError):AuditEvent.objects.filter(pk=event.pk).update(reason='Değiştir')
        with self.assertRaises(ValidationError):AuditEvent.objects.filter(pk=event.pk).delete()
        with self.assertRaises(ValidationError):
            AuditEvent.objects.bulk_create([event],update_conflicts=True,update_fields=['reason'],unique_fields=['id'])
        self.assertEqual(self.client.get(reverse('audit_detail',args=[event.pk])).status_code,200)
        self.client.force_login(self.observer)
        self.assertEqual(self.client.get(reverse('listing',args=['islem-gecmisi'])).status_code,403)

    def test_recipe_requires_complete_mapping_and_explicit_confirmation(self):
        recipe=ConsumptionRecipe.objects.create(product=self.semi,name='Eksik',unit='adet')
        job=self.job(recipe)
        with self.assertRaises(ValidationError):advance_job(job.pk,self.admin,'start')
        self.assertFalse(job.stock_inputs.exists())
        line=ConsumptionRecipeLine.objects.create(recipe=recipe,product=self.raw,quantity=None,unit='kg')
        recipe.confirmed=True
        with self.assertRaises(ValidationError):recipe.save()
        line.quantity=5
        line.save()
        recipe.confirmed=True
        recipe.save()
        self.stock(self.raw,100)
        advance_job(job.pk,self.admin,'start')
        self.assertEqual(job.stock_inputs.count(),1)

    def test_recipe_edit_clears_confirmation_and_used_recipe_is_locked(self):
        recipe=self.recipe()
        line=recipe.inputs.get()
        line.quantity=4
        line.save()
        recipe.refresh_from_db()
        self.assertFalse(recipe.confirmed)
        recipe.confirmed=True
        recipe.save()
        job=self.job(recipe)
        self.stock(self.raw,100)
        advance_job(job.pk,self.admin,'start')
        line.quantity=3
        with self.assertRaises(ValidationError):line.save()
        with self.assertRaises(ValidationError), transaction.atomic():line.delete()
        with self.assertRaises(ValidationError):recipe.save()
        self.assertEqual(job.stock_inputs.get().quantity,4)
        with self.assertRaises(ValidationError):job.stock_inputs.update(quantity=99)

    def test_inventory_completion_consumes_inputs_and_creates_output_once(self):
        self.stock(self.raw,100)
        job=self.job()
        self.finish(job,quantity=3)
        self.assertEqual(job.state,'completed')
        self.assertEqual(job.actual_quantity,3)
        self.assertEqual(stock_balance(self.raw,self.source),85)
        self.assertEqual(stock_balance(self.semi,self.output),3)
        posting=ProductionPosting.objects.get(job=job)
        self.assertEqual(posting.movements.count(),2)
        self.assertTrue(all(move.created_by_id==self.admin.pk and move.unit for move in posting.movements.all()))
        before=StockMovement.objects.count()
        with self.assertRaises(ValidationError):advance_job(job.pk,self.admin,str(job.steps.last().pk),actual_quantity=3)
        self.assertEqual(StockMovement.objects.count(),before)
        self.assertEqual(ProductionPosting.objects.count(),1)

    def test_short_stock_leaves_last_step_posting_and_audit_unchanged(self):
        self.stock(self.raw,10)
        job=self.job()
        last=self.last_step(job)
        audit_before=AuditEvent.objects.count()
        movements_before=StockMovement.objects.count()
        with self.assertRaises(ValidationError):advance_job(job.pk,self.admin,str(last.pk),actual_quantity=3)
        job.refresh_from_db()
        last.refresh_from_db()
        self.assertEqual(job.state,'running')
        self.assertIsNone(job.actual_quantity)
        self.assertIsNone(last.completed_at)
        self.assertFalse(ProductionPosting.objects.exists())
        self.assertEqual(StockMovement.objects.count(),movements_before)
        self.assertEqual(AuditEvent.objects.count(),audit_before)

    def test_save_failure_rolls_back_all_movements_and_receipt(self):
        self.stock(self.raw,100)
        job=self.job()
        last=self.last_step(job)
        original=StockMovement.save
        def fail_output(instance,*args,**kwargs):
            if instance.posting_id and instance.kind=='in':raise OperationalError('simulated output failure')
            return original(instance,*args,**kwargs)
        before=AuditEvent.objects.count()
        with patch.object(StockMovement,'save',new=fail_output):
            with self.assertRaises(OperationalError):advance_job(job.pk,self.admin,str(last.pk),actual_quantity=2)
        self.assertEqual(stock_balance(self.raw,self.source),100)
        self.assertEqual(stock_balance(self.semi,self.output),0)
        self.assertFalse(ProductionPosting.objects.exists())
        self.assertEqual(AuditEvent.objects.count(),before)

    def test_fractional_actual_adet_is_rejected_without_rounding(self):
        self.stock(self.raw,100)
        job=self.job()
        last=self.last_step(job)
        with self.assertRaises(ValidationError):advance_job(job.pk,self.admin,str(last.pk),actual_quantity=Decimal('1.5'))
        response=self.client.post(reverse('production_job',args=[job.pk]),{'expected':last.pk,'actual_quantity':'1.5'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'tam sayı')
        self.assertFalse(ProductionPosting.objects.exists())

    def test_duplicate_input_rows_aggregate_to_one_stock_leg(self):
        self.stock(self.raw,100)
        recipe=self.recipe(inputs=[(self.raw,3),(self.raw,2)])
        job=self.job(recipe)
        self.finish(job)
        self.assertEqual(stock_balance(self.raw,self.source),90)
        self.assertEqual(job.stock_posting.movements.filter(kind='out').count(),1)

    def test_mamul_consumes_semifinished_stock_without_repeating_its_raw_consumption(self):
        self.stock(self.raw,100)
        semi_job=self.job(output_warehouse=self.source)
        self.finish(semi_job,quantity=2)
        self.assertEqual(stock_balance(self.raw,self.source),90)
        self.stock(self.paint,10)
        recipe=self.recipe(self.main,[(self.semi,2),(self.paint,1)])
        main_job=self.job(recipe,quantity=1,flow=ProductionFlow.objects.get(code='finishing'),assembly_required=True)
        main_job.preceding_jobs.add(semi_job)
        self.finish(main_job,quantity=1)
        self.assertEqual(stock_balance(self.raw,self.source),90)
        self.assertEqual(stock_balance(self.semi,self.source),0)
        self.assertEqual(stock_balance(self.paint,self.source),9)
        self.assertEqual(stock_balance(self.main,self.output),1)
        self.assertFalse(main_job.stock_posting.movements.filter(product=self.raw).exists())

    def test_codeless_izgara_source_is_mapped_without_rewriting_original(self):
        batch,_=import_catalog(workbook([('Izgara',[[1,'GRILL','Izgara',None,'Pik','Hammadde',Decimal('3.5'),'KG']])]),'grill.xlsx',self.admin)
        source=MaterialRecipe.objects.get(source=batch)
        product=source.product
        product.unit='adet'
        product.save()
        source_line=source.lines.get()
        recipe=copy_consumption_recipe(source.pk,self.admin)
        self.assertIsNone(recipe.inputs.get().product_id)
        pig=Product.objects.create(code='PIG',name='Pik',kind='raw',unit='kg')
        line=recipe.inputs.get()
        line.product=pig
        line.full_clean()
        line.save()
        recipe.confirmed=True
        recipe.save()
        self.stock(pig,20)
        job=self.job(recipe,recipe=source,flow=ProductionFlow.objects.get(code='casting'),casting_material='pig')
        self.finish(job,quantity=2)
        self.assertEqual(stock_balance(pig,self.source),13)
        source_line.refresh_from_db()
        self.assertIsNone(source_line.component_id)
        self.assertEqual(source_line.component_code,'')
        self.assertEqual(source_line.quantity,Decimal('3.5'))

    def test_units_and_source_selection_are_validated(self):
        recipe=self.recipe()
        job=self.job(recipe,input_warehouse=None)
        with self.assertRaises(ValidationError):advance_job(job.pk,self.admin,'start')
        self.assertFalse(job.stock_inputs.exists())
        job.input_warehouse=self.source
        job.save()
        self.stock(self.raw,100)
        last=self.last_step(job)
        changed=Product.objects.get(pk=self.raw.pk)
        changed.unit='lt'
        with self.assertRaises(ValidationError):changed.full_clean()
        Product._base_manager.filter(pk=self.raw.pk).update(unit='lt')  # Simulate an out-of-app unit change.
        with self.assertRaises(ValidationError):advance_job(job.pk,self.admin,str(last.pk),actual_quantity=2)
        self.assertFalse(ProductionPosting.objects.exists())

    def test_operators_can_post_production_without_manual_stock_add_permission(self):
        self.assertFalse(self.operator.has_perm('erp.add_stockmovement'))
        self.assertTrue(self.operator.has_perm('erp.post_production_stock'))
        self.stock(self.raw,100)
        job=self.job(created_by=self.operator)
        self.finish(job,actor=self.operator)
        self.assertEqual(job.stock_posting.created_by,self.operator)
        logs=AuditEvent.objects.filter(model_label='erp.stockmovement',action='create',object_id__in=[str(pk) for pk in job.stock_posting.movements.values_list('pk',flat=True)])
        self.assertTrue(all(log.actor_id==self.operator.pk for log in logs))
        with self.assertRaises(PermissionDenied):advance_job(self.job().pk,self.observer,'start')

    def test_posting_is_for_final_step_only_and_is_immutable(self):
        self.stock(self.raw,100)
        job=self.job()
        advance_job(job.pk,self.admin,'start')
        job.refresh_from_db()
        with self.assertRaises(ValidationError):post_production(job,self.admin,2)
        for step in list(job.steps.all())[:-1]:advance_job(job.pk,self.admin,str(step.pk))
        advance_job(job.pk,self.admin,str(job.steps.last().pk),actual_quantity=2)
        posting=job.stock_posting
        with self.assertRaises(ValidationError):posting.save()
        with self.assertRaises(ValidationError):ProductionPosting.objects.filter(pk=posting.pk).update(quantity=99)
        with self.assertRaises(ValidationError):posting.delete()

    def test_tracking_job_never_reposts_packaged_finished_stock(self):
        self.stock(self.main,3,self.output)
        job=ProductionJob.objects.create(number='PACK',product=self.main,quantity=3,unit='adet',
            flow=ProductionFlow.objects.get(code='finishing'),assembly_required=False,stock_mode='tracking',created_by=self.admin)
        self.finish(job,3)
        self.assertEqual(stock_balance(self.main,self.output),3)
        self.assertFalse(ProductionPosting.objects.exists())

    def test_quantity_review_does_not_expose_unviewable_order_lines(self):
        review=QuantityReview.objects.create(model_label='erp.orderline',object_id='999',field_name='quantity',quantity=Decimal('1.5'))
        self.client.force_login(self.operator)
        response=self.client.get(reverse('listing',args=['miktar-incelemeleri']))
        self.assertEqual(response.status_code,200)
        self.assertNotIn(review.pk,{row['pk'] for row in response.context['rows']})
