from django.db import migrations


def snapshot_existing(apps,schema_editor):
    Line=apps.get_model('erp','OrderLine')
    for line in Line.objects.select_related('product').all().iterator():
        Line.objects.filter(pk=line.pk).update(unit=line.product.unit,product_code=line.product.code,product_name=line.product.name)
    Posting=apps.get_model('erp','ProductionPosting')
    Job=apps.get_model('erp','ProductionJob')
    for posting in Posting.objects.all().iterator():
        Job.objects.filter(pk=posting.job_id).update(posting_version=posting.sequence)


class Migration(migrations.Migration):
    dependencies=[('erp','0006_operational_stock_workflows')]
    operations=[migrations.RunPython(snapshot_existing,migrations.RunPython.noop)]
