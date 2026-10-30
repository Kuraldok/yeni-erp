from django.db import migrations


def preserve_and_review(apps, schema_editor):
    Movement = apps.get_model('erp','StockMovement')
    Review = apps.get_model('erp','QuantityReview')
    # Migration 0004 initializes existing jobs as tracking; new jobs default to inventory.
    for movement in Movement.objects.select_related('product').filter(unit='').iterator():
        Movement.objects.filter(pk=movement.pk).update(unit=movement.product.unit)
    specifications = [
        ('Product','minimum_stock','unit'), ('OrderLine','quantity','product.unit'),
        ('StockMovement','quantity','unit'), ('MaterialRequirement','quantity','unit'),
        ('ProductionJob','quantity','unit'),
    ]
    for name, field, unit_path in specifications:
        Model = apps.get_model('erp',name)
        queryset = Model.objects.all()
        if '.' in unit_path:
            queryset = queryset.select_related(unit_path.split('.')[0])
        for instance in queryset.iterator():
            unit = instance
            for attribute in unit_path.split('.'):
                unit = getattr(unit,attribute)
            quantity = getattr(instance,field)
            if str(unit or '').strip().casefold() == 'adet' and quantity is not None and quantity != quantity.to_integral_value():
                Review.objects.get_or_create(model_label=Model._meta.label_lower,object_id=str(instance.pk),field_name=field,
                    defaults={'quantity':quantity,'note':'Geçmiş Adet miktarı kesirli; özgün değer korundu. İşlemi yapan kişi ve eski tarih tahmin edilmedi.'})


class Migration(migrations.Migration):
    dependencies = [('erp','0004_inventory_audit_and_cancellation')]
    operations = [migrations.RunPython(preserve_and_review,migrations.RunPython.noop)]
