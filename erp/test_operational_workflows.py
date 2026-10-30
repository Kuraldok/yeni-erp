from datetime import date,datetime,timezone
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import Group,User
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.management import call_command
from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse
from .audit import audit_actor
from .inventory import validate_consumption_recipe
from .models import (AuditEvent,ConsumptionRecipe,ConsumptionRecipeLine,Order,OrderLine,Partner,
    Product,ProductionFlow,ProductionJob,ProductionPosting,StockDocument,StockDocumentLine,
    StockMovement,StockReservation,Warehouse,stock_balance)
from .operations import cancel_line
from .production import advance_job
from .production_ops import cancel_job,reverse_production
from .reports import production_totals
from .stock import (available_stock,post_document,release_reservation,reserve_order_line,
    reserved_stock,reverse_document,valuation)


class OperationalWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles',verbosity=0);call_command('setup_production',verbosity=0)
        cls.admin=User.objects.create_superuser('workflow-admin',password='Fixture-only-7861')
        cls.observer=User.objects.create_user('workflow-observer');cls.observer.groups.add(Group.objects.get(name='Gözlemci'))
        cls.operator=User.objects.create_user('workflow-operator');cls.operator.groups.add(Group.objects.get(name='Üretim sorumlusu'))
        cls.depot=User.objects.create_user('workflow-depot');cls.depot.groups.add(Group.objects.get(name='Depo sorumlusu'))
        cls.raw=Product.objects.create(code='WF-RAW',name='Ahşap hammadde',unit='kg',kind='raw',category='Banklar')
        cls.main=Product.objects.create(code='WF-MAIN',name='Mamul',unit='adet',kind='finished',category='Banklar')
        cls.semi=Product.objects.create(code='WF-SEMI',name='Yarı mamul',unit='adet',kind='semi',category='Banklar')
        cls.wh=Warehouse.objects.create(name='Hammadde deposu');cls.output=Warehouse.objects.create(name='Ürün deposu')
        cls.customer=Partner.objects.create(name='Test müşteri',kind='customer');cls.supplier=Partner.objects.create(name='Test tedarikçi',kind='supplier')

    def setUp(self):self.client.force_login(self.admin)
    def stock(self,product=None,quantity=100,warehouse=None,cost=2):
        return StockMovement.objects.create(product=product or self.raw,warehouse=warehouse or self.wh,kind='in',quantity=Decimal(str(quantity)),unit_cost=cost,note='Sentetik açılış',created_by=self.admin)
    def recipe(self,product=None,inputs=None):
        product=product or self.main
        recipe=ConsumptionRecipe.objects.create(product=product,name='Doğrudan tüketim',unit=product.unit,version=ConsumptionRecipe.objects.filter(product=product).count()+1)
        for material,quantity in inputs or [(self.raw,2)]:ConsumptionRecipeLine.objects.create(recipe=recipe,product=material,quantity=quantity,unit=material.unit)
        recipe.confirmed=True;recipe.save();return recipe
    def job(self,quantity=5,recipe=None,**kwargs):
        recipe=recipe or self.recipe()
        values=dict(number=f'WF-JOB-{ProductionJob.objects.count()+1}',product=recipe.product,quantity=quantity,unit=recipe.unit,
            consumption_recipe=recipe,flow=ProductionFlow.objects.get(code='wood'),input_warehouse=self.wh,output_warehouse=self.output,created_by=self.admin)
        values.update(kwargs);return ProductionJob.objects.create(**values)
    def ready(self,job):
        advance_job(job.pk,self.admin,'start')
        steps=list(job.steps.all())
        for step in steps[:-1]:advance_job(job.pk,self.admin,str(step.pk))
        return steps[-1]
    def batch(self,job,quantity,**kwargs):
        job.refresh_from_db()
        return advance_job(job.pk,self.admin,str(job.steps.last().pk),actual_quantity=quantity,expected_version=job.posting_version,**kwargs)
    def order(self,kind='sale',quantity=5,product=None,price=10):
        order=Order.objects.create(number=f'WF-ORDER-{Order.objects.count()+1}',kind=kind,partner=self.customer if kind=='sale' else self.supplier,date=date(2026,10,30))
        line=OrderLine.objects.create(order=order,product=product or (self.main if kind=='sale' else self.raw),quantity=quantity,unit_price=price)
        order.status='approved';order.full_clean();order.save();return order,line
    def document(self,kind,product=None,quantity=1,order=None,line=None,source=None,target=None,cost=None):
        doc=StockDocument.objects.create(number=f'WF-DOC-{StockDocument.objects.count()+1}',kind=kind,order=order,
            source_warehouse=source,target_warehouse=target,reason='İzole test fişi',created_by=self.admin)
        StockDocumentLine.objects.create(document=doc,product=product or (line.product if line else self.raw),order_line=line,quantity=quantity,unit_cost=cost)
        return doc

    def test_selected_casting_material_cannot_consume_both_alternatives(self):
        pig=Product.objects.create(code='WF-PIG',name='Pik',kind='raw',unit='kg')
        aluminium=Product.objects.create(code='WF-AL',name='  ALÜMİNYUM KÜLÇE  ',kind='raw',unit='kg')
        self.stock(pig);self.stock(aluminium)
        recipe=self.recipe(inputs=[(pig,2),(aluminium,3)])
        job=self.job(recipe=recipe,flow=ProductionFlow.objects.get(code='casting'),casting_material='pig')
        with self.assertRaisesMessage(ValidationError,'Seçilmeyen'):advance_job(job.pk,self.admin,'start')
        self.assertFalse(job.steps.exists());self.assertFalse(job.stock_inputs.exists());self.assertFalse(job.reservations.exists())
        line=recipe.inputs.get(product=aluminium);line.active=False;line.save()
        recipe.confirmed=True;recipe.save()
        self.ready(job);self.batch(job,5)
        self.assertEqual(stock_balance(pig),90);self.assertEqual(stock_balance(aluminium),100)

    def test_order_unit_and_product_snapshot_stay_stable(self):
        order,line=self.order()
        original_code=line.product_code
        self.main.unit='m'
        with self.assertRaises(ValidationError):self.main.save()
        self.main.refresh_from_db();self.main.code='WF-RENAMED';self.main.name='Yeni ad';self.main.save()
        line.refresh_from_db()
        self.assertEqual((line.unit,line.product_code,line.product_name),('adet',original_code,'Mamul'))
        response=self.client.get(reverse('order_tracking',args=[order.pk]))
        self.assertContains(response,original_code)
        self.assertContains(response,'Adet')

    def test_inactive_products_are_rejected_in_orders_stock_and_production(self):
        self.main.active=False;self.main.save()
        order=Order.objects.create(number='WF-INACTIVE',kind='sale',partner=self.customer,date=date(2026,10,30))
        with self.assertRaises(ValidationError):OrderLine.objects.create(order=order,product=self.main,quantity=1,unit_price=1)
        response=self.client.get(reverse('order_product_options'),{'kind':'sale'})
        self.assertNotIn(self.main.pk,[p['id'] for p in response.json()['products']])
        for slug in ('stok','uretim-isleri','tuketim-receteleri','tuketim-girdileri'):
            response=self.client.get(reverse('create',args=[slug]))
            self.assertNotIn(self.main.pk,response.context['form'].fields['product'].queryset.values_list('pk',flat=True))
        with self.assertRaises(ValidationError):self.stock(self.main)
        recipe=ConsumptionRecipe.objects.create(product=self.main,name='Pasif çıktı',unit='adet')
        with self.assertRaises(ValidationError):validate_consumption_recipe(recipe)

    def test_stock_cards_expose_components_without_changing_main_product_list(self):
        main=self.client.get(reverse('listing',args=['urunler']))
        stock=self.client.get(reverse('listing',args=['stok-kartlari']))
        self.assertNotIn(self.raw.pk,{r['pk'] for r in main.context['rows']})
        self.assertIn(self.raw.pk,{r['pk'] for r in stock.context['rows']})
        self.assertEqual(self.client.get(reverse('stock_card_detail',args=[self.raw.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('edit',args=['stok-kartlari',self.raw.pk])).status_code,200)

    def test_start_checks_net_materials_and_reserves_them_atomically(self):
        self.stock(quantity=5)
        job=self.job(quantity=5)
        count=AuditEvent.objects.count()
        with self.assertRaises(ValidationError):advance_job(job.pk,self.admin,'start')
        self.assertFalse(job.stock_inputs.exists());self.assertFalse(job.reservations.exists());self.assertFalse(job.steps.exists())
        self.assertEqual(AuditEvent.objects.count(),count)
        self.stock(quantity=10)
        self.ready(job)
        self.assertEqual(reserved_stock(self.raw,self.wh),10)
        self.assertEqual(available_stock(self.raw,self.wh),5)
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(product=self.raw,warehouse=self.wh,kind='out',quantity=6,note='Başka işe ayrılmış stok',created_by=self.admin)
        other=self.job(quantity=3)
        with self.assertRaises(ValidationError):advance_job(other.pk,self.admin,'start')

    def test_partial_batches_track_remaining_and_reject_duplicate_requests(self):
        self.stock()
        job=self.job(quantity=10)
        last=self.ready(job)
        self.batch(job,3)
        job.refresh_from_db();last.refresh_from_db()
        self.assertEqual((job.state,job.actual_quantity,job.remaining_quantity),('running',3,7))
        self.assertIsNone(last.completed_at)
        self.assertEqual(stock_balance(self.raw),94);self.assertEqual(stock_balance(self.main),3)
        self.assertEqual(reserved_stock(self.raw,self.wh),14)
        with self.assertRaises(ValidationError):advance_job(job.pk,self.admin,str(last.pk),actual_quantity=3,expected_version=0)
        self.assertEqual(job.stock_postings.count(),1)
        self.batch(job,7);job.refresh_from_db()
        self.assertEqual((job.state,job.actual_quantity),('completed',10))
        self.assertEqual(job.stock_postings.count(),2);self.assertEqual(reserved_stock(self.raw),0)

    def test_scrap_and_actual_consumption_are_separate_from_good_output(self):
        self.stock()
        job=self.job(quantity=5);self.ready(job)
        self.batch(job,2,scrap_quantity=1,scrap_reason='Döküm hatası',actual_inputs={self.raw.pk:7})
        job.refresh_from_db();posting=job.stock_postings.get()
        self.assertEqual((posting.quantity,posting.scrap_quantity),(2,1))
        self.assertEqual(stock_balance(self.raw),93);self.assertEqual(stock_balance(self.main),2)
        self.assertEqual(job.remaining_quantity,3)
        totals=production_totals(job)
        self.assertEqual(totals['scrap'],1);self.assertIn('fark 1',totals['variance'])
        before=StockMovement.objects.count()
        with self.assertRaises(ValidationError):self.batch(job,1,scrap_quantity=1)
        self.assertEqual(StockMovement.objects.count(),before)

    def test_all_scrap_is_recorded_without_creating_finished_stock(self):
        self.stock()
        job=self.job(quantity=2);self.ready(job)
        self.batch(job,0,scrap_quantity=2,scrap_reason='Tam ret',close_job=True,close_reason='Parti tamamen reddedildi')
        job.refresh_from_db()
        self.assertEqual((job.state,job.actual_quantity),('completed',0))
        self.assertEqual(stock_balance(self.main),0);self.assertEqual(stock_balance(self.raw),96)
        self.assertEqual(job.stock_postings.get().material_cost,8)

    def test_short_close_requires_reason_and_releases_remaining_materials(self):
        self.stock();job=self.job();self.ready(job)
        with self.assertRaises(ValidationError):self.batch(job,2,close_job=True)
        self.assertEqual(stock_balance(self.raw),100)
        self.batch(job,2,close_job=True,close_reason='Talep düşürüldü')
        job.refresh_from_db()
        self.assertEqual(job.state,'completed');self.assertEqual(job.remaining_quantity,3)
        self.assertEqual(reserved_stock(self.raw),0)

    def test_cancel_running_job_keeps_posted_batches_and_releases_reservations(self):
        self.stock();job=self.job();self.ready(job);self.batch(job,2)
        with self.assertRaises(ValidationError):cancel_job(job.pk,self.admin,'')
        cancel_job(job.pk,self.admin,'Kalan talep iptal edildi');job.refresh_from_db()
        self.assertEqual(job.state,'cancelled');self.assertEqual(job.actual_quantity,2)
        self.assertEqual(stock_balance(self.main),2);self.assertEqual(reserved_stock(self.raw),0)
        self.assertEqual(AuditEvent.objects.get(model_label='erp.productionjob',object_id=str(job.pk),action='cancel').actor,self.admin)
        with self.assertRaises(ValidationError):self.batch(job,3)

    def test_order_job_link_and_automatic_output_reservation(self):
        self.stock();order,line=self.order(quantity=5)
        job=self.job(order_line=line);job.full_clean();self.ready(job);self.batch(job,2)
        self.assertEqual(reserved_stock(self.main,self.output),2)
        self.assertEqual(available_stock(self.main,self.output),0)
        self.assertEqual(line.production_jobs.get(),job)
        wrong=self.job(product=self.semi,order_line=line)
        with self.assertRaises(ValidationError):wrong.full_clean()

    def test_production_reversal_restores_inputs_preserves_original_and_reopens_job(self):
        self.stock();job=self.job(quantity=2);self.ready(job);self.batch(job,2)
        posting=job.stock_postings.get()
        reversal=reverse_production(posting.pk,self.admin,'Yanlış üretim miktarı')
        job.refresh_from_db()
        self.assertEqual((job.state,job.actual_quantity),('running',0))
        self.assertEqual(stock_balance(self.raw),100);self.assertEqual(stock_balance(self.main),0)
        self.assertEqual(ProductionPosting.objects.get(pk=posting.pk).quantity,2)
        self.assertEqual(reverse_production(posting.pk,self.admin,'Tekrar'),reversal)
        self.assertEqual(StockDocument.objects.filter(production_posting=posting).count(),1)
        self.assertIsNone(job.steps.last().completed_at)

    def test_production_reversal_cannot_take_stock_reserved_for_order(self):
        self.stock();_,line=self.order(quantity=2)
        job=self.job(quantity=2,order_line=line);self.ready(job);self.batch(job,2)
        posting=job.stock_postings.get();before=StockMovement.objects.count()
        with self.assertRaises(ValidationError):reverse_production(posting.pk,self.admin,'Düzeltme')
        self.assertEqual(StockMovement.objects.count(),before);self.assertFalse(StockDocument.objects.exists())
        release_reservation(line.reservations.get(state='active').pk,self.admin,'Üretim düzeltilecek')
        reverse_production(posting.pk,self.admin,'Düzeltme')
        self.assertEqual(stock_balance(self.main),0)

    def test_delivery_tracks_partial_fulfillment_and_completion_only_after_all_lines(self):
        self.stock(self.main,5,self.output,10);order,line=self.order(quantity=5)
        order.status='completed'
        with self.assertRaises(ValidationError):order.save()
        order.refresh_from_db()
        doc=self.document('delivery',line=line,order=order,quantity=2,source=self.output)
        self.assertEqual(stock_balance(self.main),5)
        post_document(doc.pk,self.admin);order.refresh_from_db()
        self.assertEqual(order.status,'approved');self.assertEqual(line.fulfilled_quantity,2);self.assertEqual(line.remaining_quantity,3)
        over=self.document('delivery',line=line,order=order,quantity=4,source=self.output)
        with self.assertRaises(ValidationError):post_document(over.pk,self.admin)
        self.assertEqual(stock_balance(self.main),3)
        last=self.document('delivery',line=line,order=order,quantity=3,source=self.output)
        post_document(last.pk,self.admin);order.refresh_from_db()
        self.assertEqual(order.status,'completed');self.assertEqual(line.remaining_quantity,0)
        before=StockMovement.objects.count();post_document(last.pk,self.admin)
        self.assertEqual(StockMovement.objects.count(),before)
        reverse_document(last.pk,self.admin,'Müşteri iadesi');order.refresh_from_db()
        self.assertEqual(order.status,'approved');self.assertEqual(line.remaining_quantity,3);self.assertEqual(stock_balance(self.main),3)

    def test_purchase_receipt_sets_cost_and_foreign_currency_requires_explicit_tl_value(self):
        order,line=self.order(kind='purchase',quantity=10,price=Decimal('4.5'))
        doc=self.document('receipt',line=line,order=order,quantity=10,target=self.wh)
        post_document(doc.pk,self.admin)
        self.assertEqual(valuation(self.raw,self.wh)['value'],45)
        self.assertEqual(line.remaining_quantity,0)
        foreign,foreign_line=self.order(kind='purchase',quantity=1)
        foreign.currency='EUR';foreign.save()
        doc=self.document('receipt',line=foreign_line,order=foreign,quantity=1,target=self.wh)
        with self.assertRaises(ValidationError):post_document(doc.pk,self.admin)
        self.assertEqual(stock_balance(self.raw),10)

    def test_receipt_reversal_is_blocked_when_received_stock_has_been_consumed(self):
        order,line=self.order(kind='purchase',quantity=10)
        doc=self.document('receipt',line=line,order=order,quantity=10,target=self.wh)
        post_document(doc.pk,self.admin)
        StockMovement.objects.create(product=self.raw,warehouse=self.wh,kind='out',quantity=5,note='Tüketildi',created_by=self.admin)
        count=StockDocument.objects.count()
        with self.assertRaises(ValidationError):reverse_document(doc.pk,self.admin,'Yanlış mal kabul')
        self.assertEqual(StockDocument.objects.count(),count);self.assertEqual(stock_balance(self.raw),5)

    def test_transfer_is_atomic_respects_reservations_and_preserves_value(self):
        self.stock(quantity=10,cost=3)
        doc=self.document('transfer',quantity=4,source=self.wh,target=self.output)
        post_document(doc.pk,self.admin)
        self.assertEqual(stock_balance(self.raw,self.wh),6);self.assertEqual(stock_balance(self.raw,self.output),4)
        self.assertEqual(valuation(self.raw,self.wh)['value']+valuation(self.raw,self.output)['value'],30)
        reverse_document(doc.pk,self.admin,'Yanlış depo')
        self.assertEqual(stock_balance(self.raw,self.wh),10);self.assertEqual(stock_balance(self.raw,self.output),0)

    def test_failed_transfer_rolls_back_both_legs_and_audit(self):
        self.stock(quantity=10);doc=self.document('transfer',quantity=4,source=self.wh,target=self.output)
        original=StockMovement.save
        def fail(instance,*args,**kwargs):
            if instance.document_line_id and instance.kind=='in':raise OperationalError('simulated destination failure')
            return original(instance,*args,**kwargs)
        before=AuditEvent.objects.count()
        with patch.object(StockMovement,'save',new=fail):
            with self.assertRaises(OperationalError):post_document(doc.pk,self.admin)
        doc.refresh_from_db()
        self.assertEqual(doc.status,'draft');self.assertEqual(stock_balance(self.raw,self.wh),10)
        self.assertEqual(stock_balance(self.raw,self.output),0);self.assertEqual(AuditEvent.objects.count(),before)

    def test_count_uses_counted_balance_and_cannot_erase_reserved_materials(self):
        self.stock(quantity=10);job=self.job(quantity=4);self.ready(job)
        doc=self.document('count',quantity=5,source=self.wh)
        with self.assertRaises(ValidationError):post_document(doc.pk,self.admin)
        self.assertEqual(stock_balance(self.raw),10)
        cancel_job(job.pk,self.admin,'Sayım düzeltmesi')
        post_document(doc.pk,self.admin);self.assertEqual(stock_balance(self.raw),5)
        zero=self.document('count',quantity=0,source=self.wh)
        post_document(zero.pk,self.admin);self.assertEqual(stock_balance(self.raw),0)

    def test_reserve_release_and_delivery_consume_only_their_own_reservations(self):
        self.stock(self.main,5,self.output)
        order,line=self.order(quantity=3);_,other=self.order(quantity=2)
        reserved=reserve_order_line(line.pk,self.output.pk,3,self.admin)
        reserve_order_line(other.pk,self.output.pk,2,self.admin)
        with self.assertRaises(ValidationError):reserve_order_line(line.pk,self.output.pk,1,self.admin)
        doc=self.document('delivery',line=line,order=order,quantity=3,source=self.output)
        post_document(doc.pk,self.admin);reserved.refresh_from_db()
        self.assertEqual(reserved.state,'consumed');self.assertEqual(reserved_stock(self.main),2)
        self.assertEqual(available_stock(self.main),0)

    def test_cancelling_order_line_with_running_job_is_blocked_then_releases_output_reserve(self):
        self.stock();order,line=self.order()
        job=self.job(order_line=line);self.ready(job);self.batch(job,1)
        order.status='draft';order.save()
        with self.assertRaises(ValidationError):cancel_line(line.pk,self.admin,'Talep iptal')
        cancel_job(job.pk,self.admin,'Talep iptal');cancel_line(line.pk,self.admin,'Talep iptal')
        self.assertEqual(reserved_stock(self.main),0)

    def test_known_costs_and_unknown_costs_are_not_confused_with_zero(self):
        self.stock(cost=3);job=self.job(quantity=2);self.ready(job)
        self.batch(job,2,labor_cost=4,overhead_cost=2)
        posting=job.stock_postings.get()
        self.assertEqual((posting.material_cost,posting.total_cost),(12,18))
        self.assertEqual(valuation(self.main,self.output)['average'],9)
        # Another stock card with an unpriced opening never receives a made-up price.
        raw=Product.objects.create(code='WF-UNKNOWN',name='Fiyatsız',kind='raw',unit='kg')
        self.stock(raw,cost=None)
        job=self.job(quantity=1,recipe=self.recipe(inputs=[(raw,1)]));self.ready(job);self.batch(job,1)
        self.assertIsNone(job.stock_postings.get().total_cost)
        self.assertIsNone(job.stock_postings.get().movements.get(kind='in').unit_cost)

    def test_reports_filter_export_and_enforce_permissions(self):
        self.stock();job=self.job(quantity=1);self.ready(job);self.batch(job,1)
        for kind in ('stock','production','orders'):
            response=self.client.get(reverse('reports'),{'kind':kind,'category':'Banklar'})
            self.assertEqual(response.status_code,200)
            export=self.client.get(reverse('reports'),{'kind':kind,'format':'csv'})
            self.assertEqual(export.status_code,200);self.assertTrue(export['Content-Type'].startswith('text/csv'))
        bad=self.client.get(reverse('reports'),{'kind':'stock','start':'2026-11-01','end':'2026-10-01'})
        self.assertContains(bad,'Başlangıç bitişten sonra olamaz')
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse('reports'),{'kind':'orders'}).status_code,403)
        self.assertEqual(self.client.get(reverse('reports'),{'kind':'production'}).status_code,200)

    def test_document_form_rejects_foreign_line_and_preserves_draft_for_review(self):
        order,line=self.order();_,other=self.order()
        payload={'number':'WF-WEB-DOC','kind':'delivery','order':order.pk,'source_warehouse':self.output.pk,'date':'2026-10-30','items-TOTAL_FORMS':1,'items-INITIAL_FORMS':0,'items-0-order_line':other.pk,'items-0-quantity':1}
        response=self.client.post(reverse('create_stock_document'),payload)
        self.assertEqual(response.status_code,200);self.assertFalse(StockDocument.objects.filter(number='WF-WEB-DOC').exists())
        payload['items-0-order_line']=line.pk
        response=self.client.post(reverse('create_stock_document'),payload)
        self.assertEqual(response.status_code,302)
        doc=StockDocument.objects.get(number='WF-WEB-DOC')
        self.assertEqual(doc.status,'draft');self.assertFalse(StockMovement.objects.filter(document_line__document=doc).exists())
        self.assertEqual(self.client.get(reverse('stock_document_detail',args=[doc.pk])).status_code,200)

    def test_mutating_endpoints_require_permissions_and_get_never_changes_stock(self):
        self.stock();job=self.job();self.ready(job)
        before=StockMovement.objects.count()
        self.assertEqual(self.client.get(reverse('cancel_production_job',args=[job.pk])).status_code,200)
        job.refresh_from_db();self.assertEqual(job.state,'running');self.assertEqual(StockMovement.objects.count(),before)
        with self.assertRaises(PermissionDenied):cancel_job(job.pk,self.observer,'Yasak')
        doc=self.document('transfer',quantity=1,source=self.wh,target=self.output)
        with self.assertRaises(PermissionDenied):post_document(doc.pk,self.observer)
        self.client.force_login(self.observer)
        self.assertEqual(self.client.post(reverse('stock_document_detail',args=[doc.pk]),{'action':'post'}).status_code,403)

    def test_production_operator_can_link_sales_without_reading_order_prices(self):
        _,line=self.order()
        self.assertFalse(self.operator.has_perm('erp.view_orderline'))
        self.client.force_login(self.operator)
        response=self.client.get(reverse('create',args=['uretim-isleri']))
        choices=response.context['form'].fields['order_line'].queryset
        self.assertIn(line.pk,choices.values_list('pk',flat=True))
        self.assertNotContains(response,'Birim fiyat')

    def test_posted_documents_are_immutable_and_reversal_is_audited(self):
        self.stock(quantity=10);doc=self.document('transfer',quantity=1,source=self.wh,target=self.output)
        post_document(doc.pk,self.admin);doc.refresh_from_db()
        with self.assertRaises(ValidationError):doc.save()
        with self.assertRaises(ValidationError):doc.lines.get().save()
        reversal=reverse_document(doc.pk,self.admin,'Hatalı transfer')
        event=AuditEvent.objects.get(model_label='erp.stockdocument',object_id=str(reversal.pk),action='create')
        self.assertEqual(event.actor,self.admin)
        self.assertEqual(reversal.reverses_id,doc.pk)

    def test_count_rejects_stale_draft_after_a_new_stock_movement(self):
        self.stock(quantity=10)
        doc=self.document('count',quantity=9,source=self.wh)
        self.stock(quantity=2)
        with self.assertRaisesMessage(ValidationError,'Sayım taslağından sonra stok değişti'):post_document(doc.pk,self.admin)
        self.assertEqual(stock_balance(self.raw),12)

    def test_transfer_preserves_exact_value_even_when_average_cost_is_repeating(self):
        self.stock(quantity=1,cost=1);self.stock(quantity=2,cost=0)
        for quantity in (1,2):
            doc=self.document('transfer',quantity=quantity,source=self.wh,target=self.output)
            post_document(doc.pk,self.admin)
            self.assertEqual(valuation(self.raw,self.wh)['value']+valuation(self.raw,self.output)['value'],1)
        self.assertEqual(valuation(self.raw,self.output)['value'],1)

    def test_reversing_mixed_cost_batches_records_positive_and_negative_value_differences(self):
        self.stock(cost=2)
        first=self.job(quantity=1);self.ready(first);self.batch(first,1,labor_cost=6)
        second=self.job(quantity=1);self.ready(second);self.batch(second,1,labor_cost=16)
        self.assertEqual(valuation(self.main,self.output)['value'],30)
        reversal=reverse_production(first.stock_postings.get().pk,self.admin,'İlk partiyi düzelt')
        outgoing=StockMovement.objects.get(document_line__document=reversal,kind='out')
        self.assertEqual((outgoing.total_cost,outgoing.value_adjustment),(10,5))
        self.assertEqual(valuation(self.main,self.output)['value'],15)
        self.assertEqual(production_totals(first)['adjustment'],5)
        reversal=reverse_production(second.stock_postings.get().pk,self.admin,'İkinci partiyi düzelt')
        outgoing=StockMovement.objects.get(document_line__document=reversal,kind='out')
        self.assertEqual((outgoing.total_cost,outgoing.value_adjustment),(20,-5))
        self.assertEqual(valuation(self.main,self.output)['value'],0)
        self.assertEqual(valuation(self.raw,self.wh)['value'],200)

    def test_approved_order_with_documents_cannot_change_currency_or_customer(self):
        self.stock(self.main,5,self.output);order,line=self.order()
        post_document(self.document('delivery',line=line,order=order,quantity=1,source=self.output).pk,self.admin)
        response=self.client.post(reverse('edit',args=['siparisler',order.pk]),{'number':order.number,'kind':'sale','partner':self.customer.pk,'status':'approved','date':'2026-10-30','currency':'EUR'})
        self.assertContains(response,'para birimi değiştirilemez')
        order.refresh_from_db();self.assertEqual(order.currency,'TRY')

    def test_cancelling_order_releases_reservations_and_zeroes_open_demand(self):
        self.stock(self.main,5,self.output);order,line=self.order()
        reserve_order_line(line.pk,self.output.pk,5,self.admin)
        order.status='cancelled';order.save();line.refresh_from_db()
        self.assertEqual(reserved_stock(self.main),0);self.assertEqual(line.remaining_quantity,0)

    def test_job_view_does_not_expose_stock_plan_without_stock_permissions(self):
        from django.contrib.auth.models import Permission
        user=User.objects.create_user('only-job-view')
        user.user_permissions.add(Permission.objects.get(content_type__app_label='erp',codename='view_productionjob'))
        job=self.job()
        self.client.force_login(user)
        response=self.client.get(reverse('production_job',args=[job.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['net_plan'],[])

    def test_csv_text_is_safe_and_historical_stock_reports_hide_current_reservations(self):
        self.main.name='=1+1';self.main.save()
        move=self.stock(self.main,5,self.output);_,line=self.order()
        StockMovement._base_manager.filter(pk=move.pk).update(created_at=datetime(2020,1,1,tzinfo=timezone.utc))
        reserve_order_line(line.pk,self.output.pk,2,self.admin)
        response=self.client.get(reverse('reports'),{'kind':'stock','format':'csv'})
        self.assertIn("'=1+1",response.content.decode())
        response=self.client.get(reverse('reports'),{'kind':'stock','end':'2020-01-02'})
        self.assertEqual(response.context['rows'][0][7],5)
        self.assertEqual(response.context['rows'][0][8:10],[None,None])

    def test_fractional_count_in_document_and_actual_production_input_is_rejected(self):
        self.stock(self.semi,10,self.wh)
        with self.assertRaises(ValidationError):self.document('transfer',product=self.semi,quantity=Decimal('1.5'),source=self.wh,target=self.output)
        job=self.job(quantity=2,recipe=self.recipe(inputs=[(self.semi,2)]));self.ready(job)
        with self.assertRaises(ValidationError):self.batch(job,1,actual_inputs={self.semi.pk:Decimal('1.5')})
        self.assertEqual(stock_balance(self.semi),10)
