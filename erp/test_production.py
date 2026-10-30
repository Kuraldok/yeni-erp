from decimal import Decimal
from io import BytesIO
from django.contrib.auth.models import User,Group,Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook
from .catalog import import_catalog,read_catalog,HEADERS
from .models import Product,CatalogImport,MaterialRecipe,MaterialRequirement,ProductionFlow,ProductionFlowStep,ProductionJob,ProductionJobStep,StockMovement,Warehouse
from .production import material_plan,advance_job

FULL_HEADER=['Sıra No',*HEADERS.values()]

def workbook(rows,headers=None,title='Banklar'):
    wb=Workbook()
    ws=wb.active
    ws.title=title
    ws.append(['','Ürün ve Ara Mamul Listesi'])
    ws.append([])
    ws.append(headers or FULL_HEADER)
    for row in rows:ws.append(row)
    data=BytesIO()
    wb.save(data)
    wb.close()
    return data.getvalue()


def complete_workbook():
    return workbook([
        [1,'U-1','Ürün','YM-1','Ahşap bölüm','Yarı Mamul',2,'Adet'],
        [2,'U-1','Ürün','HM-1','Alüminyum Külçe','Hammadde',Decimal('3.5'),'KG'],
    ])

class CatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles',verbosity=0)
        cls.admin=User.objects.create_superuser('catalog-admin',password='TestOnly-Secure-7245')

    def test_header_mapping_handles_missing_component_code_column(self):
        headers=[h for h in FULL_HEADER if h!='Bileşen Kodu']
        content=workbook([[1,'IZ-1','Izgara','Alüminyum Külçe','Hammadde',Decimal('8.4'),'KG']],headers,'Izgaralar')
        batch,_=import_catalog(content,'list.xlsx')
        line=MaterialRequirement.objects.get()
        self.assertEqual(line.component_code,'')
        self.assertEqual(line.component_name,'Alüminyum Külçe')
        self.assertEqual(line.quantity,Decimal('8.4'))
        self.assertEqual(line.unit,'KG')
        self.assertIsNone(line.component_id)
        self.assertNotIn('Bileşen Kodu',line.raw)
        self.assertFalse(Product.objects.filter(code='Alüminyum Külçe').exists())
        self.assertEqual(batch.summary['blank_fields']['Bileşen Kodu'],1)

    def test_blanks_remain_blank_including_quantity_and_parent_unit(self):
        content=workbook([[1,'P1','Üst ürün','C1',None,'Hammadde',None,None]])
        batch,_=import_catalog(content,'list.xlsx')
        line=MaterialRequirement.objects.get()
        self.assertIsNone(line.quantity)
        self.assertIsNone(line.raw['Birim İhtiyacı'])
        self.assertIsNone(line.raw['Bileşen Adı'])
        self.assertEqual(line.component_name,'')
        parent=Product.objects.get(code='P1')
        self.assertEqual(parent.unit,'')
        self.assertEqual(parent.kind,'')
        self.assertEqual(parent.production_area,'')
        self.assertIsNone(parent.minimum_stock)
        self.assertEqual(Product.objects.get(code='C1').name,'')

    def test_orphan_parent_not_forward_filled(self):
        content=workbook([[1,'P1','Ürün','C1','Ayak','Yarı Mamul',1,'Adet'],[2,None,'Ürün',None,None,'Hammadde',5,'KG']])
        import_catalog(content,'list.xlsx')
        line=MaterialRequirement.objects.get(row_number=5)
        self.assertEqual(line.parent_code,'')
        self.assertIsNone(line.recipe_id)
        self.assertEqual(MaterialRecipe.objects.count(),1)

    def test_repeat_import_is_idempotent(self):
        content=complete_workbook()
        first,created=import_catalog(content,'list.xlsx')
        self.assertTrue(created)
        counts=(Product.objects.count(),MaterialRecipe.objects.count(),MaterialRequirement.objects.count())
        second,created=import_catalog(content,'renamed.xlsx')
        self.assertFalse(created)
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(counts,(Product.objects.count(),MaterialRecipe.objects.count(),MaterialRequirement.objects.count()))

    def test_conflicting_code_not_arbitrarily_assigned(self):
        content=workbook([[1,'P1','Ürün','C1','Ayak','Yarı Mamul',1,'Adet'],[2,'P2','Ürün','C1','Kapak','Yarı Mamul',2,'Adet']])
        batch,_=import_catalog(content,'list.xlsx')
        self.assertEqual(batch.summary['conflict_codes'],['C1'])
        self.assertFalse(Product.objects.filter(code='C1').exists())
        self.assertTrue(all(l.component_id is None and l.issues for l in MaterialRequirement.objects.all()))

    def test_existing_cards_never_overwritten(self):
        original=Product.objects.create(code='U-1',name='Mevcut özel ürün',unit='kg',minimum_stock=10)
        batch,_=import_catalog(complete_workbook(),'list.xlsx')
        original.refresh_from_db()
        self.assertEqual(original.name,'Mevcut özel ürün')
        self.assertEqual(original.unit,'kg')
        self.assertEqual(original.minimum_stock,10)
        self.assertIn('U-1',batch.summary['conflict_codes'])
        self.assertIsNone(MaterialRecipe.objects.get().product_id)

    def test_formula_without_cached_value_not_evaluated(self):
        content=workbook([[1,'P1','Ürün','=B4&"A"','Ayak','Yarı Mamul',None,'Adet']])
        import_catalog(content,'list.xlsx')
        line=MaterialRequirement.objects.get()
        self.assertEqual(line.component_code,'')
        self.assertEqual(line.formulas['Bileşen Kodu'],'=B4&"A"')
        self.assertIsNone(line.raw['Bileşen Kodu'])
        self.assertTrue(any('formülün kayıtlı sonucu yok' in i for i in line.issues))

    def test_invalid_file_and_quantity_preserve_source(self):
        with self.assertRaises(ValidationError):read_catalog(b'not an xlsx')
        self.assertEqual(CatalogImport.objects.count(),0)
        content=workbook([[1,'P1','Ürün','C1','Ayak','Yarı Mamul','bilinmiyor','Adet']])
        import_catalog(content,'list.xlsx')
        line=MaterialRequirement.objects.get()
        self.assertIsNone(line.quantity)
        self.assertEqual(line.raw['Birim İhtiyacı'],'bilinmiyor')

    def test_atomic_failure_does_not_leave_partial_import(self):
        content=workbook([[1,'P1','Ürün','C1','Ayak','Yarı Mamul',1,'Adet'],[2,'X'*101,'Uzun kod','C2','Ayak','Yarı Mamul',1,'Adet']])
        with self.assertRaises(ValidationError):import_catalog(content,'list.xlsx')
        self.assertEqual(Product.objects.count(),0)
        self.assertEqual(CatalogImport.objects.count(),0)

    def test_requirements_decimal_and_unknown_not_replaced(self):
        content=workbook([[1,'U1','Ürün','C1','Ayak','Yarı Mamul',Decimal('2.5'),'Adet'],[2,'U1','Ürün','C2','Pik','Hammadde',None,'KG']])
        import_catalog(content,'list.xlsx')
        rows,issues=material_plan(MaterialRecipe.objects.get(),Decimal('4'))
        self.assertEqual(rows[0]['required'],10)
        self.assertIsNone(rows[1]['required'])
        self.assertTrue(issues)

    def test_self_component_is_reported_not_a_recursive_bom(self):
        import_catalog(workbook([[1,'P1','Ürün','P1','Ürün','Mamul',1,'Adet']]),'self.xlsx')
        line=MaterialRequirement.objects.get()
        self.assertTrue(any('Üst ürün ve bileşen kodu aynı' in i for i in line.issues))
        _,issues=material_plan(MaterialRecipe.objects.get(),Decimal('1'))
        self.assertTrue(issues)

    def test_upload_permissions_report_and_idempotency(self):
        viewer=User.objects.create_user('viewer')
        viewer.groups.add(Group.objects.get(name='Gözlemci'))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get('/excel-aktar/').status_code,403)
        self.client.force_login(self.admin)
        content=complete_workbook()
        response=self.client.post('/excel-aktar/',{'file':SimpleUploadedFile('urun.xlsx',content)})
        batch=CatalogImport.objects.get()
        self.assertRedirects(response,f'/excel-aktarim/{batch.pk}/')
        self.assertContains(self.client.get(f'/excel-aktarim/{batch.pk}/'),'Kaynak satırı')
        response=self.client.post('/excel-aktar/',{'file':SimpleUploadedFile('urun.xlsx',content)})
        self.assertRedirects(response,f'/excel-aktarim/{batch.pk}/')
        self.assertEqual(CatalogImport.objects.count(),1)
        self.assertEqual(self.client.get('/modul/receteler/yeni/').status_code,403)
        self.assertEqual(self.client.get(f'/modul/receteler/{MaterialRecipe.objects.get().pk}/').status_code,403)

    def test_recipe_calculation_and_invalid_input(self):
        import_catalog(complete_workbook(),'list.xlsx')
        recipe=MaterialRecipe.objects.get()
        self.client.force_login(self.admin)
        response=self.client.get(f'/recete/{recipe.pk}/?quantity=4')
        self.assertEqual(response.context['rows'][0]['required'],Decimal('8'))
        self.assertEqual(response.context['rows'][1]['required'],Decimal('14'))
        for value in ['NaN','-1','0','Infinity','1e1000','1.0001']:
            response=self.client.get(f'/recete/{recipe.pk}/',{'quantity':value})
            self.assertTrue(response.context['error'])
            self.assertEqual(response.context['rows'],[])

    def test_imported_unknown_threshold_is_not_displayed_as_sufficient(self):
        import_catalog(complete_workbook(),'list.xlsx')
        self.client.force_login(self.admin)
        response=self.client.get('/')
        self.assertContains(response,'Eşik belirtilmedi')
        self.assertContains(response,'Ahşap üretimi')

    def test_stock_requires_explicit_unit_on_imported_parent(self):
        import_catalog(complete_workbook(),'list.xlsx')
        with self.assertRaises(ValidationError):StockMovement.objects.create(product=Product.objects.get(code='U-1'),warehouse=Warehouse.objects.create(name='Depo'),kind='in',quantity=1,note='Açılış',created_by=self.admin)

class ProductionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_production',verbosity=0)
        call_command('setup_roles',verbosity=0)
        cls.user=User.objects.create_user('ustasi')
        cls.user.groups.add(Group.objects.get(name='Üretim sorumlusu'))
        cls.user.user_permissions.add(Permission.objects.get(content_type__app_label='erp',codename='track_production_without_stock'))
        cls.product=Product.objects.create(code='Y1',name='Yarı mamul')

    def job(self,flow='wood',**kwargs):
        attrs={'stock_mode':'tracking','number':f'IE-{ProductionJob.objects.count()+1}','product':self.product,'quantity':2,'unit':'adet','flow':ProductionFlow.objects.get(code=flow),'created_by':self.user}
        attrs.update(kwargs)
        return ProductionJob.objects.create(**attrs)

    def finish(self,job):
        advance_job(job.pk,self.user,'start')
        for step in job.steps.all():advance_job(job.pk,self.user,str(step.pk))
        job.refresh_from_db()

    def test_standard_flows_exact_order_unknown_times(self):
        wood=ProductionFlow.objects.get(code='wood')
        self.assertEqual(list(wood.steps.values_list('name',flat=True)),['Ahşap hammadde','Lata üretimi','Boya','Hazır ahşap parçalar'])
        casting=ProductionFlow.objects.get(code='casting')
        self.assertEqual(list(casting.steps.values_list('name',flat=True))[1:],['Kalıplama ve döküm','Kumlama','Taşlama','Delme','Boya','Hazır döküm parçalar'])
        self.assertFalse(ProductionFlowStep.objects.exclude(duration_minutes=None).exists())
        before=ProductionFlowStep.objects.count()
        call_command('setup_production',verbosity=0)
        self.assertEqual(ProductionFlowStep.objects.count(),before)

    def test_wood_ordered_progress_actor_and_no_auto_stock(self):
        job=self.job()
        self.finish(job)
        self.assertEqual(job.state,'completed')
        self.assertEqual(job.steps.count(),4)
        self.assertEqual(job.steps.filter(completed_by=self.user).count(),4)
        self.assertEqual(StockMovement.objects.count(),0)

    def test_casting_requires_selected_raw_material(self):
        job=self.job('casting')
        with self.assertRaises(ValidationError):advance_job(job.pk,self.user,'start')
        self.assertEqual(job.steps.count(),0)
        job.casting_material='pig'
        job.save()
        self.finish(job)
        self.assertEqual(job.state,'completed')

    def test_optional_assembly_explicit_and_no_skip_package(self):
        job=self.job('finishing')
        with self.assertRaises(ValidationError):advance_job(job.pk,self.user,'start')
        job.assembly_required=False
        job.save()
        advance_job(job.pk,self.user,'start')
        self.assertEqual(list(job.steps.values_list('name',flat=True)),['Paketleme','Teslimata hazır'])
        job2=self.job('finishing',assembly_required=True)
        advance_job(job2.pk,self.user,'start')
        self.assertEqual(list(job2.steps.values_list('name',flat=True)),['Montaj','Paketleme','Teslimata hazır'])

    def test_preceding_wood_and_casting_must_complete(self):
        wood=self.job()
        casting=self.job('casting',casting_material='aluminium')
        assembly=self.job('finishing',assembly_required=True)
        assembly.preceding_jobs.add(wood,casting)
        with self.assertRaises(ValidationError):advance_job(assembly.pk,self.user,'start')
        self.finish(wood)
        with self.assertRaises(ValidationError):advance_job(assembly.pk,self.user,'start')
        self.finish(casting)
        advance_job(assembly.pk,self.user,'start')
        assembly.refresh_from_db()
        self.assertEqual(assembly.state,'running')

    def test_stale_click_does_not_advance_next_step(self):
        job=self.job()
        advance_job(job.pk,self.user,'start')
        with self.assertRaises(ValidationError):advance_job(job.pk,self.user,'start')
        step=job.steps.first()
        advance_job(job.pk,self.user,str(step.pk))
        with self.assertRaises(ValidationError):advance_job(job.pk,self.user,str(step.pk))
        self.assertEqual(job.steps.exclude(completed_at=None).count(),1)

    def test_incomplete_recipe_cannot_start(self):
        import_catalog(workbook([[1,'U1','Ürün','C1','Ayak','Yarı Mamul',None,'Adet']]),'list.xlsx')
        recipe=MaterialRecipe.objects.get()
        job=self.job(product=recipe.product,recipe=recipe)
        with self.assertRaises(ValidationError):advance_job(job.pk,self.user,'start')
        self.assertEqual(job.steps.count(),0)

    def test_casting_cannot_silently_switch_pig_and_aluminium(self):
        import_catalog(complete_workbook(),'list.xlsx')
        recipe=MaterialRecipe.objects.get()
        job=self.job('casting',product=recipe.product,recipe=recipe,casting_material='pig')
        with self.assertRaises(ValidationError):advance_job(job.pk,self.user,'start')
        job.casting_material='aluminium'
        job.save()
        advance_job(job.pk,self.user,'start')
        job.refresh_from_db()
        self.assertEqual(job.state,'running')

    def test_started_job_definition_locked_in_ui(self):
        job=self.job()
        advance_job(job.pk,self.user,'start')
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/modul/uretim-isleri/{job.pk}/').status_code,403)
        self.assertContains(self.client.get(f'/uretim-isi/{job.pk}/'),'Sıradaki aşama')

    def test_viewer_cannot_advance(self):
        viewer=User.objects.create_user('gozlemci')
        viewer.groups.add(Group.objects.get(name='Gözlemci'))
        job=self.job()
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(f'/uretim-isi/{job.pk}/').status_code,200)
        self.assertEqual(self.client.post(f'/uretim-isi/{job.pk}/',{'expected':'start'}).status_code,403)
        job.refresh_from_db()
        self.assertEqual(job.state,'draft')

    def test_create_job_with_dependencies_and_advance_via_http(self):
        previous=self.job()
        self.finish(previous)
        self.client.force_login(self.user)
        response=self.client.post('/modul/uretim-isleri/yeni/',{'stock_mode':'tracking','number':'FORM-1','product':self.product.pk,'quantity':'2','unit':'adet','flow':ProductionFlow.objects.get(code='finishing').pk,'assembly_required':'True','preceding_jobs':[previous.pk]})
        job=ProductionJob.objects.get(number='FORM-1')
        self.assertRedirects(response,f'/uretim-isi/{job.pk}/')
        self.assertEqual(list(job.preceding_jobs.all()),[previous])
        self.assertRedirects(self.client.post(f'/uretim-isi/{job.pk}/',{'expected':'start'}),f'/uretim-isi/{job.pk}/')
        self.assertEqual(job.steps.count(),3)

    def test_flow_page_renders_operations(self):
        self.client.force_login(self.user)
        flow=ProductionFlow.objects.get(code='casting')
        self.assertContains(self.client.get(f'/uretim-akisi/{flow.pk}/'),'Kalıplama ve döküm')
