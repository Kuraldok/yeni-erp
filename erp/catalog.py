"""Header-based XLSX import. Never evaluate formulas or guess missing values."""
from collections import defaultdict, Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from xml.etree.ElementTree import ParseError
from defusedxml.common import DefusedXmlException
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl import load_workbook
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import Product, CatalogImport, MaterialRecipe, MaterialRequirement

HEADERS = {
    'parent_code':'Üst Ürün Kodu', 'parent_name':'Üst Ürün Adı',
    'component_code':'Bileşen Kodu', 'component_name':'Bileşen Adı',
    'component_kind':'Bileşen Türü', 'quantity':'Birim İhtiyacı', 'unit':'Ölçü Birimi',
}
KINDS = {'Mamul':'finished','Yarı Mamul':'semi','Hammadde':'raw','Parça':'part'}
UNITS = {'Adet':'adet','KG':'kg','Metre':'m','Litre':'lt'}

def text(value):
    return '' if value is None else str(value).strip()

def read_catalog(content):
    if len(content)>8*1024*1024:
        raise ValidationError('Excel dosyası en fazla 8 MB olabilir.')
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries=archive.infolist()
            if len(entries)>2000 or sum(e.file_size for e in entries)>50*1024*1024:
                raise ValidationError('Excel dosyasının açılmış boyutu sınırı aşıyor.')
        values=load_workbook(BytesIO(content),read_only=True,data_only=True,keep_links=False)
        formulas=load_workbook(BytesIO(content),read_only=True,data_only=False,keep_links=False)
    except (BadZipFile, KeyError, ValueError, OSError, ParseError, DefusedXmlException, InvalidFileException) as exc:
        raise ValidationError('Geçerli bir .xlsx dosyası seçin.') from exc
    rows=[]
    try:
        for sheet in values:
            header=None
            for number,r in enumerate(sheet.iter_rows(min_row=1,max_row=min(sheet.max_row or 20,20),values_only=True),1):
                names=[text(v) for v in r]
                if 'Üst Ürün Kodu' in names and 'Bileşen Adı' in names:
                    if len([v for v in names if v])!=len(set(v for v in names if v)):
                        raise ValidationError(f'{sheet.title}: tekrarlanan sütun başlığı var.')
                    header={v:i for i,v in enumerate(names) if v}
                    header_row=number
                    break
            if header is None:
                raise ValidationError(f'{sheet.title}: beklenen tablo başlıkları bulunamadı.')
            if not set(HEADERS.values())-{'Bileşen Kodu'} <= set(header):
                raise ValidationError(f'{sheet.title}: zorunlu tablo başlığı eksik.')
            if (sheet.max_row or 0)>20000 or (sheet.max_column or 0)>100:
                raise ValidationError(f'{sheet.title}: tablo boyutu sınırı aşıyor.')
            frows=formulas[sheet.title].iter_rows(min_row=header_row+1,values_only=True)
            for number,(r,fr) in enumerate(zip(sheet.iter_rows(min_row=header_row+1,values_only=True),frows),header_row+1):
                raw={label:(r[index] if index<len(r) else None) for label,index in header.items()}
                field_values={field:raw.get(label) for field,label in HEADERS.items()}
                if not any(v is not None for v in field_values.values()):
                    continue
                record={field:text(v) for field,v in field_values.items() if field!='quantity'}
                record.update(sheet=sheet.title,row_number=number,raw=raw,formulas={
                    label:fr[index] for label,index in header.items()
                    if index<len(fr) and isinstance(fr[index],str) and fr[index].startswith('=')
                },issues=[])
                for field,label in HEADERS.items():
                    if field_values[field] is None or text(field_values[field])=='':
                        record['issues'].append(f'{label} boş.')
                if record['component_kind'] and record['component_kind'] not in KINDS:
                    record['issues'].append('Bileşen türü tanınmıyor; kaynak değer korundu.')
                if record['unit'] and record['unit'] not in UNITS:
                    record['issues'].append('Ölçü birimi tanınmıyor; kaynak değer korundu.')
                quantity=field_values['quantity']
                record['quantity']=None
                if quantity is not None and text(quantity):
                    try:
                        if isinstance(quantity,bool): raise InvalidOperation
                        parsed=Decimal(str(quantity).replace(',','.'))
                        if not parsed.is_finite() or parsed<0 or parsed.as_tuple().exponent < -12 or parsed>=Decimal('1e12'):
                            raise InvalidOperation
                        record['quantity']=parsed
                        if parsed==0: record['issues'].append('İhtiyaç miktarı sıfır; inceleyin.')
                    except (InvalidOperation,ValueError):
                        record['issues'].append('İhtiyaç miktarı geçerli sayı değil; kaynak değer korundu.')
                for label in record['formulas']:
                    if raw.get(label) is None:
                        record['issues'].append(f'{label}: formülün kayıtlı sonucu yok; hesaplanmadı.')
                # Raw JSON retains all blanks, original text, numbers and header layout.
                if any(not isinstance(v,(str,int,float,bool,type(None))) for v in raw.values()):
                    raise ValidationError(f'{sheet.title}:{number}: beklenmeyen hücre türü.')
                rows.append(record)
                if len(rows)>10000: raise ValidationError('En fazla 10.000 veri satırı aktarılabilir.')
    except (KeyError, ValueError, TypeError, ParseError, DefusedXmlException, OSError, OverflowError) as exc:
        raise ValidationError('Excel satırları okunamadı; dosyanın geçerli ve bozulmamış olduğunu kontrol edin.') from exc
    finally:
        values.close()
        formulas.close()
    if not rows: raise ValidationError('Dosyada aktarılacak veri satırı yok.')
    return rows


def import_catalog(content, filename, actor=None):
    checksum=sha256(content).hexdigest()
    existing=CatalogImport.objects.filter(checksum=checksum).first()
    if existing: return existing,False
    rows=read_catalog(content)
    specifications=defaultdict(lambda: {'names':set(),'kinds':set(),'units':set(),'categories':set()})
    for row in rows:
        for code,name,kind,unit in [
            (row['parent_code'],row['parent_name'],'',''),
            (row['component_code'],row['component_name'],KINDS.get(row['component_kind'],''),UNITS.get(row['unit'],'')),
        ]:
            if not code: continue
            if len(code)>100 or len(name)>200:
                raise ValidationError(f"{row['sheet']}:{row['row_number']}: kod veya ad çok uzun.")
            spec=specifications[code]
            for key,value in [('names',name),('kinds',kind),('units',unit),('categories',row['sheet'])]:
                if value:spec[key].add(value)
    conflicts={code for code,spec in specifications.items() if any(len(spec[key])>1 for key in ['names','kinds','units'])}
    products={}
    with transaction.atomic():
        batch,created=CatalogImport.objects.get_or_create(checksum=checksum,defaults={'filename':Path(filename).name[:240], 'created_by':actor})
        if not created:return batch,False
        new_products=0
        for code,spec in specifications.items():
            if code in conflicts:continue
            attrs={'name':next(iter(spec['names']),''),'kind':next(iter(spec['kinds']),''),'unit':next(iter(spec['units']),''),'category':next(iter(spec['categories'])) if len(spec['categories'])==1 else ''}
            product=Product.objects.filter(code=code).first()
            if product:
                # Existing business data is not rewritten, including existing blanks.
                if any(value and getattr(product,key)!=value for key,value in attrs.items() if key!='category'):
                    conflicts.add(code)
                    continue
            else:
                product=Product(code=code,minimum_stock=None,**attrs)
                product.full_clean()
                product.save()
                new_products+=1
            products[code]=product
        recipes={}
        records=[]
        for row in rows:
            issues=list(row['issues'])
            for key in ['parent_code','component_code']:
                if row[key] in conflicts:
                    issues.append(f'{HEADERS[key]} için çelişkili kart bilgisi var; kart eşleştirilmedi.')
            if row['component_code'] and row['component_code']==row['parent_code']:
                issues.append('Üst ürün ve bileşen kodu aynı; kendine bağlı reçete varsayılmadı.')
            recipe=None
            if row['parent_code']:
                key=(row['sheet'],row['parent_code'],row['parent_name'])
                if key not in recipes:
                    recipe_issues=[]
                    if row['parent_code'] in conflicts:recipe_issues.append('Üst ürün kodu çelişkili; eşleştirme gerekli.')
                    if not row['parent_name']:recipe_issues.append('Üst ürün adı boş.')
                    recipes[key]=MaterialRecipe.objects.create(source=batch,sheet=row['sheet'],parent_code=row['parent_code'],parent_name=row['parent_name'],product=products.get(row['parent_code']),issues=recipe_issues)
                recipe=recipes[key]
            records.append(MaterialRequirement(
                source=batch,recipe=recipe,sheet=row['sheet'],row_number=row['row_number'],parent_code=row['parent_code'],
                component_code=row['component_code'],component_name=row['component_name'],component_kind=row['component_kind'],
                quantity=row['quantity'],unit=row['unit'],component=products.get(row['component_code']),raw=row['raw'],formulas=row['formulas'],issues=issues,
            ))
        MaterialRequirement.objects.bulk_create(records)
        batch.summary={
            'sheets':len(set(r['sheet'] for r in rows)), 'rows':len(rows), 'recipes':len(recipes),
            'new_products':new_products, 'conflict_codes':sorted(conflicts),
            'rows_with_issues':sum(bool(r.issues) for r in records),
            'blank_fields':{label:sum(r['raw'].get(label) in (None,'') for r in rows) for label in HEADERS.values()},
            'types':dict(Counter(r['component_kind'] for r in rows)),
        }
        batch.save(update_fields=['summary'])
    return batch,True
