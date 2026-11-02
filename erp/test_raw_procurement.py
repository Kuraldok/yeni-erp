from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .catalog import import_catalog
from .models import (AuditEvent, ConsumptionRecipe, ConsumptionRecipeLine, MaterialRequirement,
    Product, ProductionFlow, ProductionJob, ProductionRoute, RouteStep, StockMovement, Warehouse, stock_balance)
from .production import advance_job
from .routing import RAW_RULE_KEY, apply_component_routes, apply_raw_material_routes
from .test_routing import workbook
from .workflows import ensure_workflows


class RawProcurementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('raw-route-admin', password='Fixture-only-raw-routes-4826')

    def raw(self, code='RAW', **fields):
        values = dict(code=code, name='Satın alınan malzeme', kind='raw', unit='kg')
        values.update(fields)
        return Product.objects.create(**values)

    def legacy_raw(self, code='RAW', **fields):
        values = dict(code=code, name='Satın alınan malzeme', kind='raw', unit='kg')
        values.update(fields)
        product=Product(**values)
        Product._base_manager.bulk_create([product])
        return product

    def test_all_raw_cards_are_purchased_without_inventing_production_flows(self):
        for index, category in enumerate(('', 'Sehpalar', 'Izgara', 'Ahşap saksı', 'Tanımsız grup')):
            self.raw(f'R-{index}', category=category)
        untouched = Product.objects.create(code='SEMI', kind='semi')
        result = apply_component_routes()
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['existing'], 5)
        self.assertEqual(result['assignments'], {'purchase': 5})
        self.assertEqual(ProductionFlow.objects.count(), 0)
        self.assertFalse(ProductionRoute.objects.filter(product=untouched).exists())
        for route in ProductionRoute.objects.select_related('product'):
            self.assertEqual((route.supply_method, route.flow_id, route.product.supply_method, route.product.production_area), ('purchase', None, 'purchase', ''))
            self.assertEqual(route.rule_key, RAW_RULE_KEY)
            step = route.steps.get()
            self.assertEqual(step.name, 'Dışarıdan hazır temin')
            self.assertIsNone(step.duration_minutes)

    def test_import_applies_procurement_and_preserves_source_blanks(self):
        import_catalog(workbook([('Sehpalar', [
            [1, 'P', 'Sehpa', 'R', 'Pik', 'Hammadde', None, 'KG'],
            [2, 'P', 'Sehpa', 'TOP', 'Masa tablası', 'Yarı Mamul', 1, 'Adet'],
        ])]), 'raw-import.xlsx')
        raw = Product.objects.get(code='R')
        self.assertEqual(raw.supply_method, 'purchase')
        self.assertIsNone(ProductionRoute.objects.get(product=raw).flow_id)
        self.assertEqual(ProductionRoute.objects.get(product__code='TOP').flow.code, 'wood')
        line = MaterialRequirement.objects.get(component=raw)
        before = MaterialRequirement.objects.filter(pk=line.pk).values().get()
        self.assertIsNone(line.quantity)
        self.assertIsNone(line.raw['Birim İhtiyacı'])
        apply_component_routes()
        self.assertEqual(MaterialRequirement.objects.filter(pk=line.pk).values().get(), before)

    def test_repeated_setup_preserves_existing_procurement_steps_and_times(self):
        raw = self.legacy_raw()
        custom = ProductionRoute.objects.create(product=raw, name='Özel tedarik', supply_method='purchase')
        step = RouteStep.objects.create(route=custom, sequence=1, name='Tedarikçi kontrolü', workstation='Tedarik', duration_minutes=17)
        for _ in range(2):
            result = apply_component_routes()
            self.assertEqual(result['created'], 0)
            self.assertEqual(result['existing'], 1)
        step.refresh_from_db()
        custom.refresh_from_db()
        self.assertEqual((custom.name, custom.rule_key, step.name, step.duration_minutes), ('Özel tedarik', '', 'Tedarikçi kontrolü', 17))
        self.assertEqual(ProductionRoute.objects.count(), 1)

    def test_old_manufacture_routes_are_retained_as_inactive_versions(self):
        ensure_workflows()
        raw = self.legacy_raw(supply_method='manufacture', production_area='wood')
        flow = ProductionFlow.objects.get(code='wood')
        old = ProductionRoute.objects.create(product=raw, name='Eski rota', flow=flow, supply_method='manufacture', version=4)
        step = RouteStep.objects.create(route=old, sequence=1, name='Eski işlem', workstation='Atölye', duration_minutes=19)
        job = ProductionJob.objects.create(number='RAW-HISTORY', product=raw, quantity=1, actual_quantity=1, unit='kg',
            flow=flow, route=old, state='completed', stock_mode='tracking', created_by=self.admin)
        before = ProductionJob.objects.filter(pk=job.pk).values().get()
        apply_component_routes()
        old.refresh_from_db()
        step.refresh_from_db()
        raw.refresh_from_db()
        self.assertFalse(old.active)
        self.assertEqual((old.name, old.flow_id, old.supply_method, step.duration_minutes), ('Eski rota', flow.pk, 'manufacture', 19))
        new = ProductionRoute.objects.get(product=raw, active=True)
        self.assertEqual((new.version, new.supply_method, new.flow_id), (5, 'purchase', None))
        self.assertEqual((raw.supply_method, raw.production_area), ('purchase', ''))
        self.assertEqual(ProductionJob.objects.filter(pk=job.pk).values().get(), before)
        self.assertEqual(apply_component_routes()['created'], 0)

    def test_open_jobs_are_reported_and_not_rewritten(self):
        ensure_workflows()
        raw = self.legacy_raw(supply_method='manufacture', production_area='wood')
        flow = ProductionFlow.objects.get(code='wood')
        old = ProductionRoute.objects.create(product=raw, name='Açık iş rotası', flow=flow, supply_method='manufacture')
        job = ProductionJob.objects.create(number='RAW-OPEN', product=raw, quantity=1, unit='kg', flow=flow,
            route=old, stock_mode='tracking', created_by=self.admin)
        for state in ('draft', 'running'):
            job.state = state
            job.save(update_fields=['state'])
            result = apply_component_routes()
            self.assertEqual(result['created'], 0)
            self.assertTrue(any('RAW: açık üretim işi' in issue for issue in result['skipped']))
            raw.refresh_from_db()
            old.refresh_from_db()
            self.assertEqual(raw.supply_method, 'manufacture')
            self.assertTrue(old.active)
        job.state = 'cancelled'
        job.save(update_fields=['state'])
        self.assertEqual(apply_component_routes()['created'], 1)

    def test_import_scope_does_not_modify_unrelated_raw_cards(self):
        unrelated = self.legacy_raw('UNRELATED')
        batch, _ = import_catalog(workbook([('Soba', [[1, 'P', 'Soba', 'R', 'Pik', 'Hammadde', 1, 'KG']])]), 'raw-scope.xlsx')
        self.assertEqual(apply_component_routes(source=batch)['created'], 0)
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.supply_method, '')
        self.assertFalse(ProductionRoute.objects.filter(product=unrelated).exists())
        self.assertEqual(apply_component_routes()['created'], 1)

    def test_unmapped_and_codeless_materials_preserve_the_source(self):
        import_catalog(workbook([
            ('Soba', [[1, 'P', 'Soba', None, 'Pik', 'Hammadde', 2, 'KG']]),
            ('Izgara', [[1, 'G', 'Izgara', None, 'Pik', 'Hammadde', 3, 'KG']]),
        ]), 'raw-unmapped.xlsx')
        before = list(MaterialRequirement.objects.order_by('pk').values())
        result = apply_component_routes()
        self.assertTrue(any('Soba:' in issue and 'hammadde kartı eşleştirilmemiş' in issue for issue in result['skipped']))
        self.assertFalse(any('Izgara:' in issue for issue in result['skipped']))
        self.assertFalse(Product.objects.filter(kind='raw').exists())
        self.assertEqual(list(MaterialRequirement.objects.order_by('pk').values()), before)

    def test_procurement_route_cannot_start_internal_production(self):
        ensure_workflows()
        raw = self.raw()
        apply_raw_material_routes()
        route = ProductionRoute.objects.get(product=raw)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('create', args=['uretim-isleri']), {'route': route.pk}).status_code, 403)
        job = ProductionJob.objects.create(number='RAW-BAD', product=raw, quantity=1, unit='kg', route=route,
            flow=ProductionFlow.objects.get(code='wood'), stock_mode='tracking', created_by=self.admin)
        with self.assertRaises(ValidationError):
            advance_job(job.pk, self.admin, 'start')
        self.assertFalse(job.steps.exists())


class ManualStockSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('manual-stock-admin', password='Fixture-only-stock-selection-9157')
        cls.raw = Product.objects.create(code='RAW', kind='raw', unit='kg')
        cls.semi = Product.objects.create(code='SEMI', kind='semi', unit='adet')
        cls.main = Product.objects.create(code='MAIN', kind='finished', unit='adet')
        cls.part = Product.objects.create(code='PART', kind='part', unit='adet')
        cls.inactive = Product.objects.create(code='INACTIVE', kind='raw', unit='kg', active=False)
        cls.warehouse = Warehouse.objects.create(name='Seçim test deposu')
        cls.url = reverse('create', args=['stok'])

    def setUp(self):
        self.client.force_login(self.admin)

    def test_get_and_forged_posts_exclude_semifinished_cards(self):
        response = self.client.get(self.url)
        choices = set(response.context['form'].fields['product'].queryset.values_list('pk', flat=True))
        self.assertEqual(choices, {self.raw.pk, self.main.pk, self.part.pk})
        self.assertNotIn(str(self.semi.pk), response.context['product_units'])
        for kind in ('in', 'out'):
            count = AuditEvent.objects.count()
            response = self.client.post(self.url, {'product': self.semi.pk, 'warehouse': self.warehouse.pk,
                'kind': kind, 'quantity': '1', 'note': 'Elle yarı mamul denemesi'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('product', response.context['form'].errors)
            self.assertFalse(StockMovement.objects.exists())
            self.assertEqual(AuditEvent.objects.count(), count)

    def test_source_mamul_semifinished_is_excluded_without_retyping_the_card(self):
        import_catalog(workbook([('Sehpalar', [
            [1, 'P', 'Sehpa', 'TOP', 'Masa tablası', 'Mamul', 1, 'Adet'],
            [2, 'P', 'Sehpa', 'LEG', 'Ayak', 'Yarı Mamul', 4, 'Adet'],
        ])]), 'stock-source-kind.xlsx')
        top = Product.objects.get(code='TOP')
        self.assertEqual(top.kind, 'finished')
        response = self.client.get(self.url)
        self.assertNotIn(top.pk, response.context['form'].fields['product'].queryset.values_list('pk', flat=True))
        response = self.client.post(self.url, {'product': top.pk, 'warehouse': self.warehouse.pk,
            'kind': 'in', 'quantity': '1', 'note': 'Kaynakta Mamul yazan yarı mamul'})
        self.assertIn('product', response.context['form'].errors)
        top.refresh_from_db()
        self.assertEqual(top.kind, 'finished')
        self.assertEqual(MaterialRequirement.objects.get(component=top).raw['Bileşen Türü'], 'Mamul')

    def test_raw_main_and_parts_keep_manual_stock_entry_and_actor(self):
        for product, quantity in ((self.raw, '1.25'), (self.main, '2'), (self.part, '3')):
            response = self.client.post(self.url, {'product': product.pk, 'warehouse': self.warehouse.pk,
                'kind': 'in', 'quantity': quantity, 'note': 'İzin verilen manuel giriş'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(stock_balance(product), Decimal(quantity))
            self.assertEqual(StockMovement.objects.get(product=product).created_by_id, self.admin.pk)

    def test_product_view_permission_is_not_expanded_by_the_selector(self):
        user = User.objects.create_user('stock-without-product-view')
        user.user_permissions.add(Permission.objects.get(content_type__app_label='erp', codename='add_stockmovement'))
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertFalse(response.context['form'].fields['product'].queryset.exists())
        self.assertEqual(response.context['product_units'], {})

    def test_automatic_semifinished_production_and_consumption_remain_working(self):
        ensure_workflows()
        apply_raw_material_routes()
        StockMovement.objects.create(product=self.raw, warehouse=self.warehouse, kind='in', quantity=10,
            unit_cost=4, note='Sentetik açılış', created_by=self.admin)
        for product, input_product, input_quantity, code in ((self.semi, self.raw, 2, 'wood'), (self.main, self.semi, 1, 'finishing')):
            recipe = ConsumptionRecipe.objects.create(product=product, name='Doğrudan tüketim', unit=product.unit)
            ConsumptionRecipeLine.objects.create(recipe=recipe, product=input_product, quantity=input_quantity, unit=input_product.unit)
            recipe.confirmed = True
            recipe.save(update_fields=['confirmed'])
            job = ProductionJob.objects.create(number='AUTO-' + product.code, product=product, quantity=2, unit=product.unit,
                flow=ProductionFlow.objects.get(code=code), assembly_required=False, consumption_recipe=recipe, input_warehouse=self.warehouse,
                output_warehouse=self.warehouse, created_by=self.admin)
            advance_job(job.pk, self.admin, 'start')
            steps = list(job.steps.all())
            for step in steps[:-1]:
                advance_job(job.pk, self.admin, str(step.pk))
            advance_job(job.pk, self.admin, str(steps[-1].pk), actual_quantity=2, expected_version=0)
        self.assertEqual((stock_balance(self.raw), stock_balance(self.semi), stock_balance(self.main)), (6, 0, 2))
        self.assertEqual(StockMovement.objects.filter(product=self.semi, kind='in', posting__isnull=False).count(), 1)
        self.assertEqual(StockMovement.objects.filter(product=self.semi, kind='out', posting__isnull=False).count(), 1)
