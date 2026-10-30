from io import BytesIO
from decimal import Decimal
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from .catalog import import_catalog
from .models import Product, MaterialRequirement, MaterialRecipe, ProductionRoute, RouteStep, ProductionFlow, ProductionJob, StockMovement
from .production import advance_job
from .routing import apply_component_routes

HEADERS=['Sıra No','Üst Ürün Kodu','Üst Ürün Adı','Bileşen Kodu','Bileşen Adı','Bileşen Türü','Birim İhtiyacı','Ölçü Birimi']


def workbook(sheets):
    book=Workbook()
    book.remove(book.active)
    for title,rows in sheets:
        sheet=book.create_sheet(title)
        sheet.append(['','Ürün ve Ara Mamul Listesi'])
        sheet.append([])
        sheet.append(HEADERS)
        for row in rows:sheet.append(row)
    data=BytesIO()
    book.save(data)
    book.close()
    return data.getvalue()


def route_catalog():
    return workbook([
        ('Çeşmeler',[
            [1,'F-1','Çeşme','FC-1','Pirinç Musluk','Yarı Mamul',1,'Adet'],
            [2,'F-1','Çeşme','FC-2','Lavabo','Yarı Mamul',1,'Adet'],
        ]),
        ('Banklar',[
            [1,'B-1','Park Bahçe Bankı','BC-1','Taşıyıcı Ayak','Yarı Mamul',2,'Adet'],
            [2,'B-1','Park Bahçe Bankı','BC-2','Gövde','Yarı Mamul',1,'Adet'],
            [3,'B-1','Park Bahçe Bankı','BC-3','Ahşap Bölüm','Yarı Mamul',5,'Adet'],
            [4,'B-1','Park Bahçe Bankı','BC-4','Mil','Yarı Mamul',1,'Adet'],
            [5,'B-1','Park Bahçe Bankı','BC-5','Alt Bağlantı Mili','Yarı Mamul',None,'Adet'],
            [6,'B-1','Park Bahçe Bankı','BR-1','Alüminyum Külçe','Hammadde',None,'KG'],
        ]),
        ('Masalar',[[1,'T-1','Masa','TC-1','Taşıyıcı Ayak','Yarı Mamul',2,'Adet']]),
    ])


class ComponentRoutingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.batch,_=import_catalog(route_catalog(),'routing.xlsx')
        cls.user=User.objects.create_superuser('route-admin',password='Only-Routing-Test-3741')

    def route(self,code):
        return ProductionRoute.objects.get(product__code=code,rule_key='fountain-bench-v1')

    def test_all_fountain_semifinished_including_faucet_are_casting(self):
        for code in ['FC-1','FC-2']:
            route=self.route(code)
            self.assertEqual(route.supply_method,'manufacture')
            self.assertEqual(route.flow.code,'casting')
            self.assertEqual(route.product.production_area,'casting')
            self.assertEqual(route.product.supply_method,'manufacture')
            self.assertEqual(list(route.steps.values_list('name',flat=True)),['Hammadde seçimi: Alüminyum Külçe / Pik','Kalıplama ve döküm','Kumlama','Taşlama','Delme','Boya','Hazır döküm parçalar'])

    def test_bench_leg_body_wood_and_remaining_purchase(self):
        self.assertEqual(self.route('BC-1').flow.code,'casting')
        self.assertEqual(self.route('BC-2').flow.code,'casting')
        wood=self.route('BC-3')
        self.assertEqual(wood.product.production_area,'wood')
        self.assertEqual(list(wood.steps.values_list('name',flat=True)),['Ahşap hammadde','Lata üretimi','Boya','Hazır ahşap parçalar'])
        for code in ['BC-4','BC-5']:
            route=self.route(code)
            self.assertEqual(route.supply_method,'purchase')
            self.assertIsNone(route.flow_id)
            self.assertEqual(route.product.production_area,'')
            self.assertEqual(route.product.supply_method,'purchase')
            self.assertEqual(list(route.steps.values_list('name',flat=True)),['Dışarıdan hazır temin'])

    def test_only_selected_categories_and_semifinished_are_assigned(self):
        self.assertEqual(self.route('TC-1').flow.code,'casting')
        self.assertFalse(ProductionRoute.objects.filter(product__code__in=['BR-1','B-1','F-1','T-1']).exists())
        for code in ['BR-1','B-1','F-1','T-1']:
            p=Product.objects.get(code=code)
            self.assertEqual(p.production_area,'')
            self.assertEqual(p.supply_method,'')
        self.assertEqual(ProductionFlow.objects.count(),3)

    def test_rules_repeat_without_duplicates_or_overwriting_steps(self):
        route=self.route('BC-1')
        step=route.steps.get(sequence=2)
        step.name='Özel kalıplama'
        step.duration_minutes=Decimal('30')
        step.save()
        count=RouteStep.objects.count()
        result=apply_component_routes()
        self.assertEqual(result['created'],0)
        self.assertEqual(result['existing'],8)
        self.assertEqual(result['assignments'],{'casting':5,'wood':1,'purchase':2})
        self.assertEqual(ProductionRoute.objects.count(),8)
        self.assertEqual(RouteStep.objects.count(),count)
        step.refresh_from_db()
        self.assertEqual(step.name,'Özel kalıplama')
        self.assertEqual(step.duration_minutes,30)

    def test_unknown_quantities_and_times_remain_unknown(self):
        self.assertIsNone(MaterialRequirement.objects.get(component_code='BC-5').quantity)
        raw=MaterialRequirement.objects.get(component_code='BR-1')
        self.assertIsNone(raw.quantity)
        self.assertIsNone(raw.raw['Birim İhtiyacı'])
        self.assertFalse(RouteStep.objects.filter(duration_minutes__isnull=False).exists())
        self.assertEqual(StockMovement.objects.count(),0)

    def test_manufacturing_job_uses_product_route_snapshot(self):
        route=self.route('BC-1')
        step=route.steps.get(sequence=2)
        step.name='Ürüne özel kalıplama'
        step.save(update_fields=['name'])
        job=ProductionJob.objects.create(number='IE-ROUTE',product=route.product,quantity=2,unit='adet',flow=route.flow,casting_material='pig',created_by=self.user)
        advance_job(job.pk,self.user,'start')
        job.refresh_from_db()
        self.assertEqual(job.route_id,route.pk)
        self.assertIn('Ürüne özel kalıplama',list(job.steps.values_list('name',flat=True)))
        step.name='Sonradan değişen rota'
        step.save(update_fields=['name'])
        self.assertIn('Ürüne özel kalıplama',list(job.steps.values_list('name',flat=True)))
        self.assertNotIn('Sonradan değişen rota',list(job.steps.values_list('name',flat=True)))

    def test_casting_material_is_still_explicit(self):
        route=self.route('BC-1')
        job=ProductionJob.objects.create(number='IE-NO-MATERIAL',product=route.product,quantity=1,unit='adet',flow=route.flow,created_by=self.user)
        with self.assertRaises(ValidationError):advance_job(job.pk,self.user,'start')
        self.assertEqual(job.steps.count(),0)
        self.assertEqual(job.casting_material,'')

    def test_purchased_components_cannot_be_manufactured(self):
        route=self.route('BC-4')
        flow=ProductionFlow.objects.get(code='casting')
        job=ProductionJob.objects.create(number='IE-PURCHASE',product=route.product,quantity=1,unit='adet',flow=flow,casting_material='pig',created_by=self.user)
        with self.assertRaises(ValidationError):advance_job(job.pk,self.user,'start')
        self.assertEqual(job.steps.count(),0)
        job.route=route
        with self.assertRaises(ValidationError):job.full_clean()

    def test_route_cannot_be_used_for_other_product_or_flow(self):
        route=self.route('BC-1')
        job=ProductionJob(number='IE-WRONG',product=Product.objects.get(code='BC-2'),quantity=1,unit='adet',flow=route.flow,route=route,casting_material='pig',created_by=self.user)
        with self.assertRaises(ValidationError):job.full_clean()
        job.product=route.product
        job.flow=ProductionFlow.objects.get(code='wood')
        with self.assertRaises(ValidationError):job.full_clean()

    def test_route_detail_component_link_and_prefilled_job_form(self):
        self.client.force_login(self.user)
        route=self.route('BC-3')
        self.assertContains(self.client.get(reverse('route_detail',args=[route.pk])),'Lata üretimi')
        parent=Product.objects.get(code='B-1')
        self.assertContains(self.client.get(reverse('product_detail',args=[parent.pk])),reverse('route_detail',args=[route.pk]))
        response=self.client.get(reverse('create',args=['uretim-isleri']),{'route':route.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['form'].initial['product'],route.product_id)
        self.assertEqual(response.context['form'].initial['flow'],route.flow_id)
        self.assertEqual(response.context['form'].initial['route'],route.pk)
        self.assertIsNone(response.context['form'].initial.get('quantity'))
        purchase=self.route('BC-4')
        self.assertContains(self.client.get(reverse('route_detail',args=[purchase.pk])),'Dışarıdan hazır temin')
        self.assertNotContains(self.client.get(reverse('route_detail',args=[purchase.pk])),'Bu rotayla iş emri aç')
        self.assertEqual(self.client.get(reverse('create',args=['uretim-isleri']),{'route':purchase.pk}).status_code,403)

    def test_multiple_active_routes_require_explicit_selection(self):
        route=self.route('BC-1')
        ProductionRoute.objects.create(product=route.product,name='Alternatif rota',version=2,flow=route.flow,supply_method='manufacture')
        job=ProductionJob.objects.create(number='IE-CHOOSE',product=route.product,quantity=1,unit='adet',flow=route.flow,casting_material='aluminium',created_by=self.user)
        with self.assertRaises(ValidationError):advance_job(job.pk,self.user,'start')
        job.route=route
        job.save(update_fields=['route'])
        advance_job(job.pk,self.user,'start')
        self.assertEqual(job.steps.count(),7)


class RoutingPreservationTests(TestCase):
    def test_existing_manual_route_is_preserved_as_new_version_added(self):
        product=Product.objects.create(code='S1',name='Gövde',kind='semi')
        manual=ProductionRoute.objects.create(product=product,name='Elle tanımlanan rota',version=1)
        step=RouteStep.objects.create(route=manual,sequence=1,name='Özel işlem',workstation='Atölye',duration_minutes=12)
        content=workbook([('Banklar',[[1,'P1','Bank','S1','Gövde','Yarı Mamul',1,'Adet']])])
        import_catalog(content,'manual.xlsx')
        manual.refresh_from_db();step.refresh_from_db()
        self.assertEqual(manual.name,'Elle tanımlanan rota')
        self.assertEqual(step.name,'Özel işlem')
        self.assertEqual(step.duration_minutes,12)
        generated=ProductionRoute.objects.get(product=product,rule_key='fountain-bench-v1')
        self.assertEqual(generated.version,2)

    def test_cross_category_conflict_not_arbitrarily_assigned(self):
        content=workbook([
            ('Banklar',[[1,'P1','Bank','S1','Ahşap Bölüm','Yarı Mamul',1,'Adet']]),
            ('Çeşmeler',[[1,'P2','Çeşme','S1','Ahşap Bölüm','Yarı Mamul',1,'Adet']]),
        ])
        import_catalog(content,'conflict.xlsx')
        product=Product.objects.get(code='S1')
        self.assertEqual(product.production_area,'')
        self.assertEqual(product.supply_method,'')
        self.assertFalse(ProductionRoute.objects.filter(product=product).exists())
        self.assertTrue(apply_component_routes()['skipped'])

    def test_conflicting_manual_production_area_not_overwritten(self):
        product=Product.objects.create(code='S1',name='Gövde',kind='semi',production_area='wood')
        import_catalog(workbook([('Banklar',[[1,'P1','Bank','S1','Gövde','Yarı Mamul',1,'Adet']])]),'conflict.xlsx')
        product.refresh_from_db()
        self.assertEqual(product.production_area,'wood')
        self.assertFalse(ProductionRoute.objects.filter(product=product).exists())
        self.assertTrue(apply_component_routes()['skipped'])

    def test_blank_bench_name_not_assumed_to_be_purchased(self):
        content=workbook([('Banklar',[[1,'P1','Bank','S1',None,'Yarı Mamul',1,'Adet']])])
        import_catalog(content,'blank.xlsx')
        product=Product.objects.get(code='S1')
        self.assertEqual(product.name,'')
        self.assertEqual(product.supply_method,'')
        self.assertFalse(ProductionRoute.objects.filter(product=product).exists())
        self.assertTrue(apply_component_routes()['skipped'])

    def test_reimport_new_version_reuses_component_routes(self):
        import_catalog(route_catalog(),'first.xlsx')
        content=workbook([('Banklar',[[1,'B-1','Park Bahçe Bankı','BC-1','Taşıyıcı Ayak','Yarı Mamul',3,'Adet']])])
        import_catalog(content,'second.xlsx')
        self.assertEqual(ProductionRoute.objects.filter(product__code='BC-1').count(),1)
