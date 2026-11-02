from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .catalog import import_catalog
from .models import (AuditEvent, ConsumptionRecipe, ConsumptionRecipeLine, MaterialRequirement, Order, OrderLine,
    Partner, Product, ProductionFlow, ProductionJob, ProductionRoute, StockDocument, StockDocumentLine,
    StockMovement, Warehouse, stock_balance)
from .operational_views import stock_card_rows
from .production import advance_job
from .production_ops import cancel_job, reverse_production
from .reports import order_rows, production_rows, production_totals, stock_rows
from .route_reviews import apply_source_routes
from .stock import post_document
from .test_routing import workbook
from .workflows import ensure_workflows


class SevenFixesFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)
        ensure_workflows()
        cls.admin=User.objects.create_superuser('seven-fixes-admin',password='Fixture-only-seven-7284')
        cls.raw=Product.objects.create(code='RAW',name='Pik',kind='raw',unit='kg')
        cls.main=Product.objects.create(code='MAIN',name='Mamul',kind='finished',unit='adet')
        cls.semi=Product.objects.create(code='SEMI',name='Yarı mamul',kind='semi',unit='adet')
        cls.source=Warehouse.objects.create(name='Girdi deposu')
        cls.output=Warehouse.objects.create(name='Çıktı deposu')
        cls.flow=ProductionFlow.objects.get(code='wood')
        StockMovement.objects.create(product=cls.raw,warehouse=cls.source,kind='in',quantity=100,
            unit_cost=2,note='Sentetik başlangıç',created_by=cls.admin)

    def setUp(self):
        self.client.force_login(self.admin)

    def job(self,product=None,quantity=2):
        product=product or self.main
        recipe=ConsumptionRecipe.objects.create(product=product,name='Doğrudan tüketim',unit=product.unit,
            version=ConsumptionRecipe.objects.filter(product=product).count()+1)
        ConsumptionRecipeLine.objects.create(recipe=recipe,product=self.raw,quantity=1,unit='kg')
        recipe.confirmed=True;recipe.save()
        return ProductionJob.objects.create(number=f'FIX-{ProductionJob.objects.count()+1}',product=product,
            quantity=quantity,unit=product.unit,flow=self.flow,consumption_recipe=recipe,
            input_warehouse=self.source,output_warehouse=self.output,created_by=self.admin)

    def ready(self,job):
        advance_job(job.pk,self.admin,'start')
        steps=list(job.steps.all())
        for step in steps[:-1]:advance_job(job.pk,self.admin,str(step.pk))
        return str(steps[-1].pk)

    def complete(self,job,quantity=None,**kwargs):
        final=self.ready(job)
        advance_job(job.pk,self.admin,final,actual_quantity=quantity or job.quantity,expected_version=0,**kwargs)
        return job.stock_postings.get()

    def card_payload(self,product,**changes):
        values=dict(code=product.code,name=product.name,kind=product.kind,unit=product.unit,minimum_stock='0',
            category=product.category,supply_method=product.supply_method,production_area=product.production_area,active='on')
        values.update(changes)
        return values

    def document(self,kind='transfer',quantity=2):
        doc=StockDocument.objects.create(number=f'DRAFT-{StockDocument.objects.count()+1}',kind=kind,
            source_warehouse=self.source,target_warehouse=self.output if kind=='transfer' else None,
            reason='Sentetik taslak',created_by=self.admin)
        StockDocumentLine.objects.create(document=doc,product=self.raw,quantity=quantity)
        return doc

    def draft_payload(self,doc,**changes):
        rows=list(doc.lines.filter(active=True))
        values={'number':doc.number,'kind':doc.kind,'order':str(doc.order_id or ''),
            'source_warehouse':str(doc.source_warehouse_id or ''),'target_warehouse':str(doc.target_warehouse_id or ''),
            'date':'2026-11-02','reason':'Taslak düzeltmesi','expected_revision':str(doc.revision),
            'items-TOTAL_FORMS':str(len(rows)),'items-INITIAL_FORMS':str(len(rows)),
            'items-MIN_NUM_FORMS':'0','items-MAX_NUM_FORMS':'200'}
        for index,row in enumerate(rows):
            values.update({f'items-{index}-line_id':str(row.pk),f'items-{index}-product':str(row.product_id),
                f'items-{index}-order_line':str(row.order_line_id or ''),f'items-{index}-quantity':str(row.quantity),
                f'items-{index}-unit_cost':str(row.unit_cost) if row.unit_cost is not None else ''})
        values.update(changes)
        return values


class CardProtectionTests(SevenFixesFixture):
    def test_open_output_card_changes_are_rejected_in_forms(self):
        job=self.job();self.ready(job)
        for field,value in (('unit','kg'),('kind','raw'),('active',''),('production_area','casting'),('supply_method','purchase')):
            response=self.client.post(reverse('edit',args=['stok-kartlari',self.main.pk]),self.card_payload(self.main,**{field:value}))
            self.assertEqual(response.status_code,200)
            self.assertIn(field,response.context['form'].errors)
        self.main.refresh_from_db()
        self.assertEqual((self.main.unit,self.main.kind,self.main.active),('adet','finished',True))
        advance_job(job.pk,self.admin,str(job.steps.last().pk),actual_quantity=2,expected_version=0)
        self.assertEqual(stock_balance(self.main),2)

    def test_open_inputs_are_protected_and_labels_remain_editable(self):
        self.ready(self.job())
        self.raw.active=False
        with self.assertRaises(ValidationError):self.raw.save(update_fields=['active'])
        self.raw.refresh_from_db();self.raw.name='Pik açıklaması güncel';self.raw.save(update_fields=['name'])
        self.assertEqual(Product.objects.get(pk=self.raw.pk).name,'Pik açıklaması güncel')

    def test_draft_inputs_and_bulk_card_edits_cannot_bypass_protection(self):
        self.job()
        with self.assertRaises(ValidationError):Product.objects.filter(pk=self.main.pk).update(kind='raw')
        self.main.unit='kg'
        with self.assertRaises(ValidationError):Product.objects.bulk_update([self.main],['unit'])
        with self.assertRaises(ValidationError):Product.objects.filter(pk=self.raw.pk).update(active=False)
        self.assertEqual(Product.objects.get(pk=self.main.pk).unit,'adet')

    def test_completed_card_type_and_unit_history_are_protected(self):
        self.complete(self.job())
        for field,value in (('unit','kg'),('kind','raw')):
            card=Product.objects.get(pk=self.main.pk);setattr(card,field,value)
            with self.assertRaises(ValidationError):card.save(update_fields=[field])
        self.main.active=False;self.main.save(update_fields=['active'])
        self.assertFalse(Product.objects.get(pk=self.main.pk).active)

    def test_raw_cards_get_routes_on_create_edit_and_bulk_creation(self):
        response=self.client.post(reverse('create',args=['stok-kartlari']),self.card_payload(
            Product(code='MANUAL-RAW',name='Yeni hammadde',kind='raw',unit='kg'),supply_method='manufacture',production_area='wood'))
        self.assertEqual(response.status_code,302)
        raw=Product.objects.get(code='MANUAL-RAW')
        self.assertEqual((raw.supply_method,raw.production_area),('purchase',''))
        route=ProductionRoute.objects.get(product=raw)
        self.assertIsNone(route.flow_id)
        self.assertEqual(route.supply_method,'purchase')
        Product.objects.filter(pk=self.semi.pk).update(kind='raw')
        self.assertEqual(ProductionRoute.objects.get(product=self.semi).supply_method,'purchase')
        cards=Product.objects.bulk_create([Product(code='BULK-RAW',kind='raw',unit='kg')])
        self.assertEqual(ProductionRoute.objects.get(product=cards[0]).supply_method,'purchase')

    def test_raw_output_is_rejected_for_both_production_flows(self):
        for code in ('wood','finishing'):
            job=ProductionJob(number='BAD-'+code,product=self.raw,quantity=1,unit='kg',
                flow=ProductionFlow.objects.get(code=code),assembly_required=False,created_by=self.admin,stock_mode='tracking')
            with self.assertRaises(ValidationError):job.full_clean()

    def test_raw_manufacture_routes_cannot_be_added_through_the_form_or_orm(self):
        count=ProductionRoute.objects.filter(product=self.raw).count()
        response=self.client.post(reverse('create',args=['rotalar']),{'name':'Geçersiz hammadde rotası',
            'product':self.raw.pk,'version':2,'active':'on','flow':self.flow.pk,'supply_method':'manufacture'})
        self.assertEqual(response.status_code,200)
        self.assertIn('supply_method',response.context['form'].errors)
        with self.assertRaises(ValidationError):
            ProductionRoute.objects.create(name='Geçersiz ORM rotası',product=self.raw,version=2,flow=self.flow,supply_method='manufacture')
        self.assertEqual(ProductionRoute.objects.filter(product=self.raw).count(),count)


class HistoricalProductionTests(SevenFixesFixture):
    def prepare_reversal(self):
        first=datetime(2026,11,1,9,tzinfo=timezone.utc)
        second=datetime(2026,11,2,9,tzinfo=timezone.utc)
        with patch('django.utils.timezone.now',return_value=first):
            job=self.job();posting=self.complete(job,labor_cost=3,overhead_cost=1)
        with patch('django.utils.timezone.now',return_value=second):
            reverse_production(posting.pk,self.admin,'Ertesi gün düzeltme')
        return job

    def test_later_reversal_preserves_the_previous_day_production(self):
        job=self.prepare_reversal()
        totals=production_totals(job,date(2026,11,1),date(2026,11,1))
        self.assertEqual((totals['good'],totals['material'],totals['labor'],totals['total']),(2,4,3,8))
        _,rows=stock_rows(self.admin,'',None,date(2026,11,1),self.output)
        self.assertEqual(rows[0][7],2)

    def test_reversal_is_negative_in_its_own_period_and_lifetime_is_zero(self):
        job=self.prepare_reversal()
        totals=production_totals(job,date(2026,11,2),date(2026,11,2))
        self.assertEqual((totals['good'],totals['material'],totals['labor'],totals['total']),(-2,-4,-3,-8))
        self.assertIn('fark 0',totals['variance'])
        lifetime=production_totals(job)
        self.assertEqual((lifetime['good'],lifetime['material'],lifetime['total']),(0,0,0))

    def test_same_day_reversal_nets_to_zero_without_removing_the_report_row(self):
        job=self.job();posting=self.complete(job)
        reverse_production(posting.pk,self.admin,'Aynı gün düzeltme')
        totals=production_totals(job)
        self.assertTrue(totals['has_correction'])
        self.assertEqual(totals['good'],0)
        _,rows=production_rows(self.admin,'',None,None)
        self.assertEqual(len(rows),1)

    def test_negative_period_quantities_are_exported_as_numbers(self):
        self.prepare_reversal()
        response=self.client.get(reverse('reports'),{'kind':'production','start':'2026-11-02','end':'2026-11-02','format':'csv'})
        self.assertContains(response,';-2;')
        self.assertNotContains(response,";'-2;")

    def test_unknown_cost_stays_unknown_in_the_reversal_period(self):
        first=datetime(2026,11,1,9,tzinfo=timezone.utc);second=datetime(2026,11,2,9,tzinfo=timezone.utc)
        StockMovement.objects.create(product=self.raw,warehouse=self.source,kind='in',quantity=1,note='Bilinmeyen maliyet',created_by=self.admin)
        with patch('django.utils.timezone.now',return_value=first):
            job=self.job();post=self.complete(job)
        with patch('django.utils.timezone.now',return_value=second):reverse_production(post.pk,self.admin,'Maliyet bilinmiyor')
        self.assertIsNone(production_totals(job,date(2026,11,2),date(2026,11,2))['material'])
        self.assertEqual(production_totals(job)['material'],0)


class RouteReviewTests(SevenFixesFixture):
    def warning_import(self):
        # A legacy definition from before automatic raw-card enforcement.
        Product._base_manager.filter(pk=self.raw.pk).update(supply_method='manufacture',production_area='wood')
        ProductionJob.objects.create(number='LEGACY-RAW',product=self.raw,quantity=1,unit='kg',flow=self.flow,
            stock_mode='tracking',created_by=self.admin)
        return import_catalog(workbook([('Soba',[[1,'WARN-P','Soba','RAW','Pik','Hammadde',1,'KG']])]),'warning.xlsx',self.admin)[0]

    def test_routing_failures_are_persisted_and_visible_with_zero_source_issues(self):
        batch=self.warning_import()
        response=self.client.get(reverse('catalog_report',args=[batch.pk]))
        self.assertEqual(response.context['report_stats']['issues'],0)
        self.assertEqual(response.context['report_stats']['routing_issues'],1)
        self.assertEqual(response.context['report_stats']['review_count'],1)
        self.assertContains(response,'açık üretim işi')
        self.assertContains(response,'Rotaları yeniden değerlendir')
        grouped=self.client.get(reverse('catalog_report',args=[batch.pk]),{'category':'Soba'})
        self.assertEqual(grouped.context['report_stats']['routing_issues'],1)
        other=self.client.get(reverse('catalog_report',args=[batch.pk]),{'category':'Banklar'})
        self.assertEqual(other.context['report_stats']['routing_issues'],0)

    def test_retry_clears_resolved_warnings_and_preserves_source_values(self):
        batch=self.warning_import()
        before=list(MaterialRequirement.objects.filter(source=batch).values())
        cancel_job(ProductionJob.objects.get(number='LEGACY-RAW').pk,self.admin,'Eski iş kapatıldı')
        response=self.client.post(reverse('reapply_source_routes',args=[batch.pk]),{'category':'Soba'})
        self.assertEqual(response.status_code,302)
        batch.refresh_from_db()
        self.assertEqual(batch.summary['routing']['warnings'],[])
        self.assertEqual(batch.summary['routing']['checked_by'],self.admin.username)
        self.assertEqual(list(MaterialRequirement.objects.filter(source=batch).values()),before)
        self.assertTrue(AuditEvent.objects.filter(actor=self.admin,model_label='erp.catalogimport',object_id=str(batch.pk)).exists())

    def test_retry_requires_permissions_post_and_keeps_existing_review_when_denied(self):
        batch=self.warning_import();before=batch.summary
        viewer=User.objects.create_user('route-review-reader')
        viewer.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp',codename__in=[
            'view_catalogimport','view_materialrecipe','view_materialrequirement','view_product']))
        self.client.force_login(viewer)
        response=self.client.get(reverse('catalog_report',args=[batch.pk]))
        self.assertNotContains(response,'Rotaları yeniden değerlendir')
        self.assertEqual(self.client.post(reverse('reapply_source_routes',args=[batch.pk])).status_code,403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('reapply_source_routes',args=[batch.pk])).status_code,405)
        batch.refresh_from_db();self.assertEqual(batch.summary,before)

    def test_open_input_card_changes_are_reviewed_instead_of_aborting_import(self):
        self.job()
        Product._base_manager.filter(pk=self.raw.pk).update(supply_method='manufacture',production_area='wood')
        batch=import_catalog(workbook([('Soba',[[1,'INPUT-P','Soba','RAW','Pik','Hammadde',1,'KG']])]),'open-input.xlsx',self.admin)[0]
        self.assertEqual(len(batch.summary['routing']['warnings']),1)
        self.assertEqual(Product.objects.get(pk=self.raw.pk).supply_method,'manufacture')


class CriticalStockTests(SevenFixesFixture):
    def test_critical_card_beyond_the_first_fifty_is_prioritized(self):
        Product.objects.bulk_create([Product(code=f'A-{i:02}',kind='finished',category='Alert') for i in range(55)])
        critical=Product.objects.create(code='ZZZ-CRITICAL',name='Kritik kart',kind='finished',category='Alert',minimum_stock=10)
        response=self.client.get(reverse('dashboard'),{'category':'Alert'})
        self.assertEqual(response.context['critical_count'],1)
        self.assertEqual(response.context['balances'][0]['product'].pk,critical.pk)
        self.assertContains(response,'Tüm kritik stoklar (1)')

    def test_all_critical_cards_are_paginated_and_filtered(self):
        Product.objects.bulk_create([Product(code=f'LOW-{i:02}',kind='finished',category='Alert',minimum_stock=1) for i in range(61)])
        first=self.client.get(reverse('critical_stock'),{'category':'Alert'})
        second=self.client.get(reverse('critical_stock'),{'category':'Alert','page':2})
        self.assertEqual(first.context['page'].paginator.count,61)
        self.assertEqual((len(first.context['rows']),len(second.context['rows'])),(50,11))
        self.assertFalse({r['product'].pk for r in first.context['rows']} & {r['product'].pk for r in second.context['rows']})
        self.assertContains(first,'category=Alert')

    def test_thresholds_follow_stock_and_permissions_are_required(self):
        self.raw.minimum_stock=101;self.raw.save(update_fields=['minimum_stock'])
        response=self.client.get(reverse('critical_stock'))
        self.assertEqual(response.context['rows'][0]['shortage'],1)
        viewer=User.objects.create_user('no-stock-reader');self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('critical_stock')).status_code,403)


class DraftDocumentTests(SevenFixesFixture):
    def test_edit_updates_the_existing_draft_without_stock_movements(self):
        doc=self.document();line=doc.lines.get()
        created=(doc.created_at,doc.created_by_id)
        count=StockMovement.objects.count()
        response=self.client.post(reverse('edit_stock_document',args=[doc.pk]),self.draft_payload(doc,**{'items-0-quantity':'5'}))
        self.assertEqual(response.status_code,302)
        doc.refresh_from_db();line.refresh_from_db()
        self.assertEqual((line.quantity,doc.revision),(5,1))
        self.assertEqual((doc.created_at,doc.created_by_id),created)
        self.assertEqual(StockMovement.objects.count(),count)
        self.assertTrue(AuditEvent.objects.filter(actor=self.admin,model_label='erp.stockdocument',object_id=str(doc.pk),action='update').exists())

    def test_removed_lines_are_preserved_and_are_not_posted(self):
        doc=self.document()
        removed=StockDocumentLine.objects.create(document=doc,product=self.raw,quantity=7)
        response=self.client.post(reverse('edit_stock_document',args=[doc.pk]),self.draft_payload(doc,**{'items-1-remove':'on'}))
        self.assertEqual(response.status_code,302)
        removed.refresh_from_db()
        self.assertFalse(removed.active)
        self.assertEqual(removed.quantity,7)
        self.assertEqual(doc.lines.count(),2)
        post_document(doc.pk,self.admin)
        self.assertEqual(stock_balance(self.raw,self.output),2)
        self.assertFalse(StockMovement.objects.filter(document_line=removed).exists())
        self.assertContains(self.client.get(reverse('stock_document_detail',args=[doc.pk])),'Taslakta çıkarıldı')

    def test_foreign_missing_and_duplicate_line_ids_are_rejected(self):
        doc=self.document();foreign=self.document().lines.get()
        invalids=[self.draft_payload(doc,**{'items-0-line_id':str(foreign.pk)}),
            self.draft_payload(doc,**{'items-0-line_id':''}),
            self.draft_payload(doc,**{'items-TOTAL_FORMS':'2','items-1-line_id':str(doc.lines.get().pk),
                'items-1-product':str(self.raw.pk),'items-1-quantity':'1'})]
        for data in invalids:
            response=self.client.post(reverse('edit_stock_document',args=[doc.pk]),data)
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.context['lines'].errors or response.context['lines'].non_form_errors())
        doc.refresh_from_db();self.assertEqual(doc.revision,0)
        self.assertEqual(doc.lines.get().quantity,2)

    def test_stale_edit_and_stale_cancel_are_rejected(self):
        doc=self.document();payload=self.draft_payload(doc)
        self.assertEqual(self.client.post(reverse('edit_stock_document',args=[doc.pk]),payload).status_code,302)
        payload['reason']='Eski ekrandan değişiklik'
        self.assertContains(self.client.post(reverse('edit_stock_document',args=[doc.pk]),payload),'başka bir işlemde değişti')
        response=self.client.post(reverse('cancel_stock_document',args=[doc.pk]),{'reason':'Eski ekrandan iptal','expected_revision':'0'})
        self.assertContains(response,'başka bir işlemde değişti')
        doc.refresh_from_db();self.assertEqual(doc.status,'draft')

    def test_cancellation_records_actor_reason_and_time_without_deleting_lines(self):
        doc=self.document();before=list(doc.lines.values());count=StockMovement.objects.count()
        response=self.client.post(reverse('cancel_stock_document',args=[doc.pk]),{'reason':'Yanlış depo seçildi','expected_revision':'0'})
        self.assertEqual(response.status_code,302)
        doc.refresh_from_db()
        self.assertEqual((doc.status,doc.cancelled_by_id,doc.cancellation_reason),('cancelled',self.admin.pk,'Yanlış depo seçildi'))
        self.assertIsNotNone(doc.cancelled_at)
        self.assertEqual(list(doc.lines.values()),before)
        self.assertEqual(StockMovement.objects.count(),count)
        self.assertEqual(AuditEvent.objects.filter(model_label='erp.stockdocument',object_id=str(doc.pk),action='cancel',actor=self.admin).count(),1)
        with self.assertRaises(ValidationError):post_document(doc.pk,self.admin)
        self.assertEqual(self.client.get(reverse('edit_stock_document',args=[doc.pk])).status_code,403)
        self.assertEqual(self.client.post(reverse('cancel_stock_document',args=[doc.pk]),{'reason':'Tekrar','expected_revision':'0'}).status_code,302)
        self.assertEqual(AuditEvent.objects.filter(model_label='erp.stockdocument',object_id=str(doc.pk),action='cancel').count(),1)

    def test_posted_documents_and_lines_remain_immutable(self):
        doc=self.document();post_document(doc.pk,self.admin)
        self.assertEqual(self.client.get(reverse('edit_stock_document',args=[doc.pk])).status_code,403)
        self.assertEqual(self.client.get(reverse('cancel_stock_document',args=[doc.pk])).status_code,403)
        line=doc.lines.get();line.quantity=8
        with self.assertRaises(ValidationError):line.save()
        doc.refresh_from_db();doc.reason='Sonradan değişiklik'
        with self.assertRaises(ValidationError):doc.save()

    def test_stale_review_cannot_post_an_edited_draft(self):
        doc=self.document()
        self.client.post(reverse('edit_stock_document',args=[doc.pk]),self.draft_payload(doc,reason='Yeni taslak'))
        count=StockMovement.objects.count()
        response=self.client.post(reverse('stock_document_detail',args=[doc.pk]),{'action':'post','expected_revision':'0'},follow=True)
        self.assertContains(response,'İşlemeden önce güncel taslağı tekrar inceleyin')
        self.assertEqual(StockMovement.objects.count(),count)
        doc.refresh_from_db();self.assertEqual(doc.status,'draft')
        response=self.client.post(reverse('stock_document_detail',args=[doc.pk]),{'action':'post','expected_revision':'1'})
        self.assertEqual(response.status_code,302)
        doc.refresh_from_db();self.assertEqual(doc.status,'posted')

    def test_edit_and_cancellation_require_their_own_permissions_and_csrf(self):
        doc=self.document();viewer=User.objects.create_user('draft-readonly')
        viewer.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp',codename__in=[
            'view_stockdocument','view_stockdocumentline','view_product','view_warehouse']))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('edit_stock_document',args=[doc.pk])).status_code,403)
        self.assertEqual(self.client.post(reverse('cancel_stock_document',args=[doc.pk]),{'reason':'Yetkisiz','expected_revision':0}).status_code,403)
        from django.test import Client
        secure=Client(enforce_csrf_checks=True);secure.force_login(self.admin)
        self.assertEqual(secure.post(reverse('edit_stock_document',args=[doc.pk]),self.draft_payload(doc)).status_code,403)

    def test_count_baseline_is_only_refreshed_after_explicit_recount(self):
        doc=self.document('count',99)
        StockMovement.objects.create(product=self.raw,warehouse=self.source,kind='in',quantity=1,unit_cost=2,note='Sayım sonrası giriş',created_by=self.admin)
        response=self.client.post(reverse('edit_stock_document',args=[doc.pk]),self.draft_payload(doc))
        self.assertEqual(response.status_code,302)
        self.assertEqual(doc.lines.get().baseline_quantity,100)
        with self.assertRaises(ValidationError):post_document(doc.pk,self.admin)
        doc.refresh_from_db()
        response=self.client.post(reverse('edit_stock_document',args=[doc.pk]),self.draft_payload(doc,
            **{'items-0-quantity':'101','refresh_count_baseline':'on'}))
        self.assertEqual(response.status_code,302)
        self.assertEqual(doc.lines.get().baseline_quantity,101)
        post_document(doc.pk,self.admin)
        self.assertEqual(stock_balance(self.raw,self.source),101)


class ReportPerformanceTests(SevenFixesFixture):
    def test_stock_card_query_count_does_not_grow_with_warehouses(self):
        with CaptureQueriesContext(connection) as first:
            rows=stock_card_rows(self.admin,self.raw)
        self.assertEqual(rows[0]['valuation']['average'],2)
        for index in range(20):Warehouse.objects.create(name=f'Ek depo {index:02}')
        with CaptureQueriesContext(connection) as many:
            rows=stock_card_rows(self.admin,self.raw)
        self.assertEqual(len(rows),22)
        self.assertEqual(len(first),len(many))
        self.assertLessEqual(len(many),3)

    def test_production_report_query_count_is_bounded(self):
        jobs=[]
        for index in range(8):
            product=Product.objects.create(code=f'PERF-{index}',kind='finished',unit='adet')
            job=self.job(product);self.complete(job);jobs.append(job.pk)
        with CaptureQueriesContext(connection) as one:
            production_rows(self.admin,'',None,None,jobs=ProductionJob.objects.filter(pk=jobs[0]).select_related('product','order_line__order'))
        with CaptureQueriesContext(connection) as many:
            headers,rows=production_rows(self.admin,'',None,None,jobs=ProductionJob.objects.filter(pk__in=jobs).select_related('product','order_line__order'))
        self.assertEqual(len(rows),8)
        self.assertEqual(len(one),len(many))
        self.assertLessEqual(len(many),6)

    def test_order_report_uses_a_single_fulfillment_aggregation(self):
        customer=Partner.objects.create(name='Rapor müşterisi',kind='customer')
        order=Order.objects.create(number='PERF-ORDER',kind='sale',partner=customer,date=date(2026,11,2))
        for _ in range(12):OrderLine.objects.create(order=order,product=self.main,quantity=1,unit_price=2)
        with CaptureQueriesContext(connection) as captured:
            headers,rows=order_rows(self.admin,'',None,None)
        self.assertEqual(len(rows),12)
        self.assertLessEqual(len(captured),2)

    def test_html_reports_paginate_and_csv_keeps_all_rows(self):
        for index in range(56):
            product=Product.objects.create(code=f'PAGE-{index:02}',kind='finished',unit='adet',category='Paged')
            StockMovement.objects.create(product=product,warehouse=self.source,kind='in',quantity=1,unit_cost=2,note='Rapor sayfası',created_by=self.admin)
        response=self.client.get(reverse('reports'),{'kind':'stock','category':'Paged'})
        self.assertEqual((response.context['page'].paginator.count,len(response.context['rows'])),(56,50))
        second=self.client.get(reverse('reports'),{'kind':'stock','category':'Paged','page':2})
        self.assertEqual(len(second.context['rows']),6)
        self.assertContains(second,'category=Paged')
        export=self.client.get(reverse('reports'),{'kind':'stock','category':'Paged','page':2,'format':'csv'})
        self.assertContains(export,'PAGE-00')
        self.assertContains(export,'PAGE-55')
