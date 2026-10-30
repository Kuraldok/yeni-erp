from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .catalog import import_catalog
from .models import (
    CatalogImport, MaterialRecipe, MaterialRequirement, Order, OrderLine, Partner,
    Product, ProductionFlow, ProductionJob, ProductionRoute, RouteStep,
    StockMovement, Warehouse,
)
from .production import advance_job, material_plan
from .registry import MODULES
from .routing import apply_component_routes
from .test_production import workbook as single_sheet_workbook, FULL_HEADER
from .test_routing import workbook


class DirectGrillRoutingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('grill-admin', password='Grill-Tests-7369')

    def catalog(self, quantity=Decimal('8.4')):
        return workbook([('Izgaralar', [
            [1, 'G1', 'Izgara', None, 'Alüminyum Külçe', 'Hammadde', quantity, 'KG'],
        ])])

    def test_raw_material_without_component_code_routes_the_main_product(self):
        batch, _ = import_catalog(self.catalog(), 'grill.xlsx')
        parent = Product.objects.get(code='G1')
        route = ProductionRoute.objects.get()
        self.assertEqual(route.product_id, parent.pk)
        self.assertEqual(route.flow.code, 'casting')
        self.assertEqual(route.steps.count(), 7)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(parent.kind, '')
        self.assertEqual(parent.unit, '')
        self.assertEqual(parent.production_area, 'casting')
        line = MaterialRequirement.objects.get()
        self.assertEqual(line.component_kind, 'Hammadde')
        self.assertEqual(line.component_code, '')
        self.assertIsNone(line.component_id)
        self.assertIsNone(line.raw['Bileşen Kodu'])
        self.assertEqual(line.issues, [])
        self.assertEqual(batch.summary['blank_fields']['Bileşen Kodu'], 1)
        self.assertEqual(apply_component_routes()['created'], 0)

    def test_missing_code_column_is_optional_only_for_grill_raw_materials(self):
        headers = [h for h in FULL_HEADER if h != 'Bileşen Kodu']
        content = single_sheet_workbook([
            [1, 'G2', 'Izgara', 'Pik', 'Hammadde', 3, 'KG'],
        ], headers=headers, title='Izgara')
        import_catalog(content, 'no-column.xlsx')
        self.assertEqual(ProductionRoute.objects.get().product.code, 'G2')
        self.assertEqual(MaterialRequirement.objects.get().issues, [])
        import_catalog(single_sheet_workbook([
            [1, 'S', 'Soba', 'Pik', 'Hammadde', 3, 'KG'],
        ], headers=headers, title='Soba'), 'other.xlsx')
        self.assertIn('Bileşen Kodu boş.', MaterialRequirement.objects.get(parent_code='S').issues)

    def test_complete_codeless_raw_recipe_can_start_casting_without_stock_changes(self):
        import_catalog(self.catalog(), 'job.xlsx')
        recipe = MaterialRecipe.objects.get()
        rows, issues = material_plan(recipe, Decimal('2'))
        self.assertEqual(issues, [])
        self.assertEqual(rows[0]['required'], Decimal('16.8'))
        route = ProductionRoute.objects.get()
        job = ProductionJob.objects.create(number='G-JOB', product=route.product, quantity=2,
            unit='adet', flow=route.flow, recipe=recipe, casting_material='aluminium', created_by=self.user)
        advance_job(job.pk, self.user, 'start')
        job.refresh_from_db()
        self.assertEqual(job.route_id, route.pk)
        self.assertEqual(job.steps.count(), 7)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_missing_quantity_is_still_blocking_and_historical_notes_are_preserved(self):
        import_catalog(self.catalog(None), 'missing.xlsx')
        line = MaterialRequirement.objects.get()
        line.issues += ['Bileşen Kodu boş.']
        line.save(update_fields=['issues'])
        recipe = MaterialRecipe.objects.get()
        _, problems = material_plan(recipe, Decimal('1'))
        self.assertTrue(any('Birim İhtiyacı boş.' in issue for issue in problems))
        self.assertFalse(any('Bileşen Kodu boş.' in issue for issue in problems))
        line.refresh_from_db()
        self.assertIn('Bileşen Kodu boş.', line.issues)
        route = ProductionRoute.objects.get()
        job = ProductionJob.objects.create(number='G-MISSING', product=route.product, quantity=1,
            unit='adet', flow=route.flow, recipe=recipe, casting_material='aluminium', created_by=self.user)
        with self.assertRaises(ValidationError):
            advance_job(job.pk, self.user, 'start')

    def test_manual_grill_main_product_routes_without_inventing_a_recipe(self):
        parent = Product.objects.create(code='GM', name='Elle açılan ızgara', category=' IZGARA ', kind='finished')
        other = Product.objects.create(code='SM', name='Soba', category='Soba', kind='finished')
        apply_component_routes()
        self.assertEqual(ProductionRoute.objects.get().product_id, parent.pk)
        self.assertFalse(ProductionRoute.objects.filter(product=other).exists())
        self.assertEqual(MaterialRecipe.objects.count(), 0)
        self.assertEqual(MaterialRequirement.objects.count(), 0)

    def test_existing_routes_and_source_scoping_are_preserved(self):
        parent = Product.objects.create(code='G1', name='Izgara', category='Izgaralar', kind='finished', unit='')
        manual = ProductionRoute.objects.create(product=parent, name='Eski rota', version=1)
        step = RouteStep.objects.create(route=manual, sequence=1, name='Özel döküm', workstation='Döküm', duration_minutes=21)
        unrelated = Product.objects.create(code='UNRELATED', category='Izgara')
        batch, _ = import_catalog(self.catalog(), 'preserve.xlsx')
        self.assertEqual(ProductionRoute.objects.filter(product=parent).count(), 2)
        self.assertFalse(ProductionRoute.objects.filter(product=unrelated).exists())
        self.assertEqual(apply_component_routes(source=batch)['created'], 0)
        manual.refresh_from_db()
        step.refresh_from_db()
        self.assertEqual(manual.name, 'Eski rota')
        self.assertEqual(step.duration_minutes, 21)
        self.assertEqual(Product.objects.get(pk=parent.pk).kind, 'finished')

    def test_product_screen_shows_direct_route_and_no_semifinished(self):
        import_catalog(self.catalog(), 'display.xlsx')
        self.client.force_login(self.user)
        route = ProductionRoute.objects.get()
        response = self.client.get(reverse('product_detail', args=[route.product_id]))
        self.assertContains(response, 'yarı mamul bulunmaz')
        self.assertContains(response, reverse('route_detail', args=[route.pk]))
        self.assertEqual(response.context['sections'][0]['rows'], [])
        self.assertEqual(response.context['sections'][1]['rows'][0]['line'].component_kind, 'Hammadde')


class GroupFilteringTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('filter-admin', password='Filter-Tests-6284')
        cls.batches = {}
        cls.records = {}
        for suffix, category in [('A', 'Banklar'), ('B', 'Ahşap saksı')]:
            batch, _ = import_catalog(workbook([(category, [
                [1, f'P-{suffix}', f'Ürün {suffix}', f'C-{suffix}', 'Ahşap Bölüm', 'Yarı Mamul', 2, 'Adet'],
            ])]), f'{suffix}.xlsx')
            cls.batches[suffix] = batch
            product = Product.objects.get(code=f'P-{suffix}')
            component = Product.objects.get(code=f'C-{suffix}')
            warehouse = Warehouse.objects.create(name=f'Depo {suffix}')
            movement = StockMovement.objects.create(product=component, warehouse=warehouse, kind='in',
                quantity=5, note=f'Belge {suffix}', created_by=cls.user)
            partner = Partner.objects.create(name=f'Cari {suffix}', kind='customer')
            order = Order.objects.create(number=f'SP-{suffix}', partner=partner, kind='sale', date=date(2026, 10, 30))
            line = OrderLine.objects.create(order=order, product=product, quantity=2, unit_price=10)
            flow = ProductionFlow.objects.create(code=f'filter-{suffix}', name=f'Akış {suffix}', area='wood')
            route = ProductionRoute.objects.create(product=product, name=f'Rota {suffix}', version=1,
                flow=flow, supply_method='manufacture')
            step = RouteStep.objects.create(route=route, sequence=1, name=f'Adım {suffix}', workstation='Ahşap')
            job = ProductionJob.objects.create(number=f'IE-{suffix}', product=product, quantity=1,
                unit='adet', flow=flow, created_by=cls.user)
            cls.records[suffix] = {'product':product, 'component':component, 'warehouse':warehouse,
                'movement':movement, 'partner':partner, 'order':order, 'line':line,
                'flow':flow, 'route':route, 'step':step, 'job':job}

    def setUp(self):
        self.client.force_login(self.user)

    def ids(self, response):
        return {row['pk'] for row in response.context['rows']}

    def test_every_module_has_a_working_group_filter(self):
        a, b = self.records['A'], self.records['B']
        pairs = {
            'urunler':(a['product'].pk,b['product'].pk),
            'depolar':(a['warehouse'].pk,b['warehouse'].pk),
            'stok':(a['movement'].pk,b['movement'].pk),
            'cariler':(a['partner'].pk,b['partner'].pk),
            'siparisler':(a['order'].pk,b['order'].pk),
            'siparis-kalemleri':(a['line'].pk,b['line'].pk),
            'rotalar':(a['route'].pk,b['route'].pk),
            'receteler':(MaterialRecipe.objects.get(source=self.batches['A']).pk,MaterialRecipe.objects.get(source=self.batches['B']).pk),
            'recete-satirlari':(MaterialRequirement.objects.get(source=self.batches['A']).pk,MaterialRequirement.objects.get(source=self.batches['B']).pk),
            'excel-aktarimlari':(self.batches['A'].pk,self.batches['B'].pk),
            'uretim-akislari':(a['flow'].pk,b['flow'].pk),
            'uretim-isleri':(a['job'].pk,b['job'].pk),
            'rota-adimlari':(a['step'].pk,b['step'].pk),
        }
        self.assertEqual(set(pairs), set(MODULES))
        for slug, (included, excluded) in pairs.items():
            with self.subTest(module=slug):
                response = self.client.get(reverse('listing', args=[slug]), {'category':'Banklar'})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="category"')
                self.assertIn(included, self.ids(response))
                self.assertNotIn(excluded, self.ids(response))
                self.assertEqual(response.context['category'], 'Banklar')

    def test_mixed_order_is_not_duplicated_and_keeps_its_full_total(self):
        order = self.records['A']['order']
        OrderLine.objects.create(order=order, product=self.records['A']['product'], quantity=1, unit_price=5)
        OrderLine.objects.create(order=order, product=self.records['B']['product'], quantity=3, unit_price=7)
        response = self.client.get(reverse('listing', args=['siparisler']), {'category':'Banklar'})
        self.assertEqual(response.context['page'].paginator.count, 1)
        self.assertEqual(response.context['rows'][0]['values'][-1], Decimal('46'))
        self.assertContains(response, '>Yeni Sipariş</a>')
        self.assertContains(self.client.get(reverse('listing', args=['depolar'])), '>+ Yeni kayıt</a>')

    def test_group_and_search_survive_non_product_pagination(self):
        product = self.records['A']['product']
        ProductionJob.objects.bulk_create([
            ProductionJob(number=f'SEARCH-{i:02}', product=product, quantity=1, unit='adet',
                flow=self.records['A']['flow'], created_by=self.user) for i in range(28)
        ])
        url = reverse('listing', args=['uretim-isleri'])
        response = self.client.get(url, {'category':'Banklar','q':'SEARCH'})
        self.assertEqual(response.context['page'].paginator.count, 28)
        self.assertContains(response, 'q=SEARCH&amp;category=Banklar')
        self.assertEqual(len(self.client.get(url, {'category':'Banklar','q':'SEARCH','page':2}).context['rows']), 3)

    def test_none_group_and_invalid_group(self):
        warehouse = Warehouse.objects.create(name='İlişkisiz depo')
        partner = Partner.objects.create(name='İlişkisiz cari', kind='customer')
        for slug, obj in [('depolar',warehouse),('cariler',partner)]:
            response = self.client.get(reverse('listing', args=[slug]), {'category':'__none__'})
            self.assertEqual(self.ids(response), {obj.pk})
            response = self.client.get(reverse('listing', args=[slug]), {'category':'Bilinmeyen'})
            self.assertEqual(self.ids(response), set())

    def test_group_filter_does_not_reveal_hidden_order_relations(self):
        limited = User.objects.create_user('partner-only')
        limited.user_permissions.add(Permission.objects.get(content_type__app_label='erp', codename='view_partner'))
        self.client.force_login(limited)
        url = reverse('listing', args=['cariler'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_filter_groups'])
        self.assertNotContains(response, 'Banklar')
        self.assertEqual(self.client.get(url, {'category':'Banklar'}).status_code, 403)

    def test_dashboard_counts_balances_flows_and_recent_movements_filter_together(self):
        response = self.client.get(reverse('dashboard'), {'category':'Banklar'})
        self.assertEqual(dict(response.context['stats']), {'Ürün':1,'Müşteri / tedarikçi':1,'Sipariş':1,'Stok hareketi':1})
        self.assertEqual({row['product'].pk for row in response.context['balances']},
            {self.records['A']['product'].pk,self.records['A']['component'].pk})
        self.assertEqual([row.pk for row in response.context['recent']], [self.records['A']['movement'].pk])
        self.assertIn(self.records['A']['flow'], response.context['flows'])
        self.assertNotIn(self.records['B']['flow'], response.context['flows'])
        self.assertEqual(response.context['catalog'].pk, self.batches['A'].pk)
        self.assertContains(response, 'id="category"')

    def test_unlinked_recipe_rows_filter_by_source_group(self):
        batch, _ = import_catalog(workbook([('Izgara', [
            [1, 'G', 'Izgara', None, 'Pik', 'Hammadde', 2, 'KG'],
        ])]), 'raw.xlsx')
        response = self.client.get(reverse('listing', args=['recete-satirlari']), {'category':'Izgara'})
        self.assertEqual(self.ids(response), {MaterialRequirement.objects.get(source=batch).pk})

    def test_report_filter_updates_counts_and_preserves_pagination(self):
        sheets = []
        for category, prefix, count in [('Banklar','A',52),('Ahşap saksı','B',2)]:
            sheets.append((category, [[i+1,f'ERR-{prefix}',f'Ürün {prefix}',f'M-{prefix}-{i}',
                'Bölüm','Yarı Mamul',None,'Adet'] for i in range(count)]))
        batch, _ = import_catalog(workbook(sheets), 'report.xlsx')
        url = reverse('catalog_report', args=[batch.pk])
        response = self.client.get(url, {'category':'Banklar'})
        self.assertEqual(response.context['page'].paginator.count, 52)
        self.assertEqual(response.context['report_stats']['rows'], 52)
        self.assertEqual(response.context['report_stats']['sheets'], 1)
        self.assertContains(response, 'category=Banklar')
        response = self.client.get(url, {'category':'Banklar','page':2})
        self.assertEqual(len(response.context['page']), 2)
        self.assertTrue(all(line.sheet == 'Banklar' for line in response.context['page']))
        recipe = MaterialRecipe.objects.get(source=self.batches['A'])
        response = self.client.get(reverse('recipe_detail', args=[recipe.pk]), {'category':'Ahşap saksı','quantity':3})
        self.assertFalse(response.context['matches_group'])
        self.assertEqual(response.context['rows'], [])
        self.assertContains(response, 'name="category" value="Ahşap saksı"')

    def test_product_cards_sort_by_displayed_group_then_code(self):
        a, b = self.records['A']['product'], self.records['B']['product']
        a.code = '000-LOW'
        a.category = ''  # Must still sort under its Banklar source group.
        a.save(update_fields=['code','category'])
        b.code = '999-HIGH'
        b.save(update_fields=['code'])
        Product.objects.create(code='ZZ', name='Aynı grupta son', category='Ahşap saksı')
        Product.objects.create(code='AAA', name='Aynı grupta ilk', category='Ahşap saksı')
        response = self.client.get(reverse('listing', args=['urunler']))
        self.assertEqual([row['values'][0] for row in response.context['rows']], ['999-HIGH','AAA','ZZ','000-LOW'])

    def test_requirement_list_precision_changes_display_only(self):
        base = MaterialRequirement.objects.get(source=self.batches['A'])
        for i, (unit, amount) in enumerate([('Adet','2'),('KG','12.345678901234'),('Metre','7'),('Litre',None)]):
            MaterialRequirement.objects.create(source=base.source, recipe=base.recipe, sheet='Banklar',
                row_number=100+i, parent_code='P-A', component_code=f'D-{i}', component_name='Ölçü',
                component_kind='Hammadde', unit=unit, quantity=Decimal(amount) if amount else None)
        response = self.client.get(reverse('listing', args=['recete-satirlari']), {'category':'Banklar'})
        values = {row['values'][3]:row['values'][6] for row in response.context['rows']}
        self.assertEqual(values['D-0'], '2')
        self.assertEqual(values['D-1'], '12,35')
        self.assertEqual(values['D-2'], '7,00')
        self.assertEqual(values['D-3'], '—')
        self.assertEqual(MaterialRequirement.objects.get(component_code='D-1').quantity, Decimal('12.345678901234'))
