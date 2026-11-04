from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .catalog import import_catalog
from .models import MaterialRecipe, MaterialRequirement, Product, ProductionFlow, ProductionJob, ProductionRoute, RouteStep, StockMovement
from .production import advance_job
from .routing import RULE_KEY, apply_component_routes
from .test_routing import workbook
from .workflows import ensure_workflows


class OutdoorWoodRoutingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('outdoor-wood-admin', password='Fixture-only-outdoor-wood-3917')

    def route(self, code):
        return ProductionRoute.objects.select_related('product', 'flow').get(product__code=code, rule_key=RULE_KEY)

    def catalog(self):
        return workbook([('Kamelyalar ve piknik masaları', [
            [1, 'P', 'Kamelya', 'S', 'Direk', 'Yarı Mamul', 4, 'Adet'],
            [2, 'P', 'Kamelya', 'F', 'Çatı bölümü', 'Mamul', None, 'Adet'],
            [3, 'P', 'Kamelya', 'R', 'Kereste', 'Hammadde', Decimal('2.75'), 'KG'],
            [4, 'P', 'Kamelya', 'PART', 'Vida', 'Parça', 8, 'Adet'],
        ])])

    def test_main_and_both_component_labels_get_wood_routes(self):
        import_catalog(self.catalog(), 'outdoor.xlsx', self.user)
        for code in ('P', 'S', 'F'):
            route = self.route(code)
            self.assertEqual((route.supply_method, route.flow.code, route.product.production_area), ('manufacture', 'wood', 'wood'))
            self.assertEqual(list(route.steps.values_list('name', flat=True)), ['Ahşap hammadde', 'Lata üretimi', 'Boya', 'Hazır ahşap parçalar'])
            self.assertTrue(all(step.duration_minutes is None for step in route.steps.all()))
        raw = ProductionRoute.objects.get(product__code='R')
        self.assertEqual(raw.supply_method, 'purchase')
        self.assertIsNone(raw.flow_id)
        self.assertFalse(ProductionRoute.objects.filter(product__code='PART').exists())
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_source_labels_unknown_amounts_and_blank_parent_fields_are_preserved(self):
        batch, _ = import_catalog(self.catalog(), 'preserve.xlsx')
        before = list(MaterialRequirement.objects.filter(source=batch).order_by('pk').values())
        for _ in range(2):
            self.assertEqual(apply_component_routes()['created'], 0)
        self.assertEqual(list(MaterialRequirement.objects.filter(source=batch).order_by('pk').values()), before)
        line = MaterialRequirement.objects.get(component_code='F')
        self.assertEqual((line.component_kind, line.component.kind, line.raw['Bileşen Türü']), ('Mamul', 'finished', 'Mamul'))
        self.assertIsNone(line.quantity)
        self.assertIsNone(line.raw['Birim İhtiyacı'])
        self.assertEqual(Product.objects.get(code='P').unit, '')

    def test_singular_plural_separate_combined_and_turkish_case_aliases(self):
        categories = ('Kamelya', 'Kamelyalar', 'Piknik masası', 'Piknik masaları',
            'Kamelya ve piknik masası', 'Kamelya ve piknik masaları',
            'Kamelyalar ve piknik masası', 'Kamelyalar ve piknik masaları',
            '  KAMELYA  ', ' PİKNİK  MASALARI ')
        for index, category in enumerate(categories):
            with self.subTest(category=category):
                import_catalog(workbook([(category, [[1, f'P-{index}', 'Ana ürün', f'C-{index}', 'Ahşap bölüm', 'Mamul', 1, 'Adet']])]), f'alias-{index}.xlsx')
                self.assertEqual(self.route(f'P-{index}').flow.code, 'wood')
                self.assertEqual(self.route(f'C-{index}').flow.code, 'wood')

    def test_manual_main_and_semifinished_cards_route_without_inventing_recipes(self):
        main = Product.objects.create(code='MANUAL', kind='finished', category='Kamelyalar')
        semi = Product.objects.create(code='MANUAL-SEMI', kind='semi', category='Piknik masaları')
        raw = Product.objects.create(code='RAW', kind='raw', unit='kg', category='Kamelyalar')
        part = Product.objects.create(code='PART', kind='part', category='Piknik masaları')
        other = Product.objects.create(code='OTHER', kind='finished', category='Masalar')
        unrelated_semi = Product.objects.create(code='GRILL-SEMI', kind='semi', category='Izgara')
        apply_component_routes()
        self.assertEqual(self.route(main.code).flow.code, 'wood')
        self.assertEqual(self.route(semi.code).flow.code, 'wood')
        self.assertEqual(ProductionRoute.objects.get(product=raw).supply_method, 'purchase')
        self.assertFalse(ProductionRoute.objects.filter(product__in=[part, other, unrelated_semi]).exists())
        self.assertEqual(MaterialRecipe.objects.count(), 0)
        self.assertEqual(MaterialRequirement.objects.count(), 0)

    def test_import_scope_leaves_unrelated_manual_cards_untouched(self):
        unrelated = Product.objects.create(code='UNRELATED', kind='finished', category='Kamelyalar')
        unrelated_semi = Product.objects.create(code='UNRELATED-SEMI', kind='semi', category='Piknik masaları')
        batch, _ = import_catalog(workbook([('Piknik masaları', [[1, 'P', 'Piknik masası', 'S', 'Oturak', 'Yarı Mamul', 2, 'Adet']])]), 'scope.xlsx')
        self.assertEqual(apply_component_routes(source=batch)['created'], 0)
        self.assertFalse(ProductionRoute.objects.filter(product__in=[unrelated, unrelated_semi]).exists())
        self.assertEqual(apply_component_routes()['created'], 2)

    def test_repeated_setup_preserves_custom_routes_steps_and_durations(self):
        parent = Product.objects.create(code='P', name='Kamelya', kind='finished', category='Kamelyalar ve piknik masaları')
        manual = ProductionRoute.objects.create(product=parent, name='Özel rota', version=1)
        old_step = RouteStep.objects.create(route=manual, sequence=1, name='Özel işlem', workstation='Atölye', duration_minutes=17)
        import_catalog(self.catalog(), 'custom.xlsx')
        route = self.route('P')
        self.assertEqual(route.version, 2)
        step = route.steps.get(sequence=2)
        step.name = 'Özel ahşap hazırlığı'; step.duration_minutes = 23
        step.save(update_fields=['name', 'duration_minutes'])
        count = ProductionRoute.objects.count()
        for _ in range(2):
            self.assertEqual(apply_component_routes()['created'], 0)
        old_step.refresh_from_db(); step.refresh_from_db(); manual.refresh_from_db()
        self.assertEqual((old_step.name, old_step.duration_minutes, step.name, step.duration_minutes), ('Özel işlem', 17, 'Özel ahşap hazırlığı', 23))
        self.assertEqual(manual.name, 'Özel rota')
        self.assertEqual(ProductionRoute.objects.count(), count)

    def test_cross_category_main_conflict_keeps_existing_route_and_groups_warning(self):
        first, _ = import_catalog(workbook([('Kamelyalar', [[1, 'P', 'Ortak ürün', 'S', 'Direk', 'Yarı Mamul', 1, 'Adet']])]), 'wood-first.xlsx')
        original = self.route('P')
        later, _ = import_catalog(workbook([('Izgara', [[1, 'P', 'Ortak ürün', None, 'Pik', 'Hammadde', 2, 'KG']])]), 'casting-later.xlsx')
        self.assertEqual(self.route('P').pk, original.pk)
        self.assertEqual(Product.objects.get(code='P').production_area, 'wood')
        warnings = later.summary['routing']['warnings']
        self.assertTrue(any(warning['code'] == 'P' and {'Kamelyalar', 'Izgara'} <= set(warning['categories']) for warning in warnings))
        self.client.force_login(self.user)
        response = self.client.get(reverse('catalog_report', args=[later.pk]), {'category':'Izgara'})
        self.assertContains(response, 'farklı rota kuralları')

    def test_component_conflict_keeps_the_previous_wood_assignment(self):
        import_catalog(workbook([('Piknik masaları', [[1, 'P1', 'Masa', 'C', 'Ayak', 'Mamul', 1, 'Adet']])]), 'first.xlsx')
        original = self.route('C')
        batch, _ = import_catalog(workbook([('Soba', [[1, 'P2', 'Soba', 'C', 'Ayak', 'Yarı Mamul', 1, 'Adet']])]), 'conflict.xlsx')
        self.assertEqual(self.route('C').pk, original.pk)
        self.assertEqual(self.route('C').flow.code, 'wood')
        self.assertTrue(batch.summary['routing']['warnings'])

    def test_open_main_and_semifinished_jobs_are_left_for_review(self):
        ensure_workflows()
        originals = []
        for code, kind in (('MAIN', 'finished'), ('SEMI', 'semi')):
            card = Product.objects.create(code=code, kind=kind, category='Kamelyalar')
            job = ProductionJob.objects.create(number=code, product=card, quantity=1, unit='adet',
                flow=ProductionFlow.objects.get(code='wood'), stock_mode='tracking', created_by=self.user)
            originals.append(ProductionJob.objects.filter(pk=job.pk).values().get())
        result = apply_component_routes()
        self.assertEqual(result['created'], 0)
        self.assertEqual(len(result['warnings']), 2)
        self.assertFalse(ProductionRoute.objects.exists())
        self.assertEqual(list(ProductionJob.objects.order_by('pk').values()), originals)
        self.assertFalse(Product.objects.exclude(supply_method='').exists())

    def test_main_and_component_jobs_use_the_wood_steps(self):
        batch, _ = import_catalog(workbook([('Kamelyalar ve piknik masaları', [
            [1, 'P', 'Kamelya', 'S', 'Direk', 'Yarı Mamul', 4, 'Adet'],
            [2, 'P', 'Kamelya', 'F', 'Çatı bölümü', 'Mamul', 1, 'Adet'],
        ])]), 'jobs.xlsx')
        for code in ('P', 'S'):
            route = self.route(code)
            job = ProductionJob.objects.create(number='WOOD-'+code, product=route.product, quantity=2, unit='adet',
                flow=route.flow, recipe=MaterialRecipe.objects.get(source=batch) if code == 'P' else None,
                stock_mode='tracking', created_by=self.user)
            advance_job(job.pk, self.user, 'start')
            job.refresh_from_db()
            self.assertEqual(job.route_id, route.pk)
            self.assertEqual(list(job.steps.values_list('name', flat=True)), list(route.steps.values_list('name', flat=True)))
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_product_details_show_main_route_and_mamul_components_as_semifinished(self):
        import_catalog(self.catalog(), 'display.xlsx')
        self.client.force_login(self.user)
        product = Product.objects.get(code='P')
        response = self.client.get(reverse('product_detail', args=[product.pk]))
        self.assertContains(response, reverse('route_detail', args=[self.route('P').pk]))
        self.assertContains(response, 'Ahşap üretimi')
        self.assertNotContains(response, 'yarı mamul bulunmaz')
        self.assertEqual({row['line'].component_code for row in response.context['sections'][0]['rows']}, {'S', 'F'})
        self.assertNotContains(response, 'Kaynak satırı')
        form = self.client.get(reverse('create', args=['stok'])).context['form']
        self.assertNotIn(Product.objects.get(code='F').pk, form.fields['product'].queryset.values_list('pk', flat=True))

    def test_missing_cards_and_self_reference_do_not_invent_components(self):
        batch, _ = import_catalog(workbook([('Kamelyalar', [
            [1, 'P', 'Kamelya', 'P', 'Kamelya', 'Mamul', 1, 'Adet'],
            [2, 'P', 'Kamelya', None, 'Direk', 'Yarı Mamul', 4, 'Adet'],
            [3, 'P', 'Kamelya', None, 'Kereste', 'Hammadde', None, 'KG'],
        ])]), 'missing.xlsx')
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(ProductionRoute.objects.count(), 1)
        self.assertEqual(self.route('P').flow.code, 'wood')
        self.assertTrue(MaterialRequirement.objects.get(row_number=4).issues)
        self.assertEqual(len(batch.summary['routing']['warnings']), 2)
