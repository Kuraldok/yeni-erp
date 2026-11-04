from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse

from .audit import audit_actor
from .models import (AuditEvent, Order, OrderLine, Partner, ProductionFlow, ProductionJob,
    StockDocument, StockDocumentLine, StockMovement, Warehouse, stock_balance)
from .production import advance_job
from .production_ops import cancel_job, reverse_production
from .report_data import order_rows
from .stock import available_stock, post_document, reserve_order_line, reserved_stock, reverse_document
from .test_seven_fixes import SevenFixesFixture


class FourFixesFixture(SevenFixesFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.customer = Partner.objects.create(name='Sentetik müşteri', kind='customer')
        cls.supplier = Partner.objects.create(name='Sentetik tedarikçi', kind='supplier')
        cls.other = Warehouse.objects.create(name='İkinci ürün deposu')

    def order_line(self, kind='sale', quantity=5):
        order = Order.objects.create(number=f'FOUR-ORDER-{Order.objects.count()+1}', kind=kind,
            partner=self.customer if kind == 'sale' else self.supplier, date=date(2026, 11, 4))
        line = OrderLine.objects.create(order=order, product=self.main if kind == 'sale' else self.raw,
            quantity=quantity, unit_price=10)
        order.status = 'approved'; order.full_clean(); order.save()
        line.refresh_from_db()
        return order, line

    def put_stock(self, warehouse, quantity=10):
        return StockMovement.objects.create(product=self.main, warehouse=warehouse, kind='in',
            quantity=quantity, unit_cost=2, note='Sentetik açılış', created_by=self.admin)

    def order_document(self, line, quantity, warehouse=None):
        sale = line.order.kind == 'sale'
        document = StockDocument.objects.create(number=f'FOUR-DOC-{StockDocument.objects.count()+1}',
            kind='delivery' if sale else 'receipt', order=line.order,
            source_warehouse=(warehouse or self.output) if sale else None,
            target_warehouse=None if sale else warehouse or self.source, created_by=self.admin)
        StockDocumentLine.objects.create(document=document, order_line=line, product=line.product, quantity=quantity)
        return document

    def shared_job(self, *predecessors):
        job = ProductionJob.objects.create(number=f'FOUR-SHARED-{ProductionJob.objects.count()+1}',
            product=self.main, quantity=1, unit='adet', flow=ProductionFlow.objects.get(code='finishing'),
            assembly_required=False, stock_mode='tracking', created_by=self.admin)
        if predecessors:
            job.preceding_jobs.add(*predecessors)
        return job

    def finish_tracking(self, job):
        advance_job(job.pk, self.admin, 'start')
        for step in job.steps.order_by('sequence'):
            advance_job(job.pk, self.admin, str(step.pk))

    def dependency_events(self, job):
        return AuditEvent.objects.filter(model_label='erp.productionjob', object_id=str(job.pk),
            changes__has_key='preceding_jobs')


class ActiveFulfillmentTests(FourFixesFixture):
    def remove_and_replace(self, document):
        old = document.lines.get()
        response = self.client.post(reverse('edit_stock_document', args=[document.pk]), self.draft_payload(document, **{
            'items-TOTAL_FORMS': '2', 'items-0-remove': 'on',
            'items-1-product': str(old.product_id), 'items-1-order_line': str(old.order_line_id),
            'items-1-quantity': '2'}))
        self.assertEqual(response.status_code, 302)
        old.refresh_from_db(); document.refresh_from_db()
        self.assertFalse(old.active)
        return old

    def test_removed_delivery_line_is_not_fulfilled_in_tracking_or_reports(self):
        self.put_stock(self.output)
        order, line = self.order_line()
        document = self.order_document(line, 3)
        old = self.remove_and_replace(document)
        post_document(document.pk, self.admin, expected_revision=document.revision)
        order.refresh_from_db(); line.refresh_from_db()
        self.assertEqual((line.fulfilled_quantity, line.remaining_quantity, order.status), (2, 3, 'approved'))
        self.assertEqual(stock_balance(self.main, self.output), 8)
        self.assertEqual(document.lines.count(), 2)
        self.assertFalse(old.movements.exists())
        response = self.client.get(reverse('order_tracking', args=[order.pk]))
        self.assertEqual((response.context['rows'][0]['fulfilled'], response.context['rows'][0]['remaining']), (2, 3))
        _, rows = order_rows(self.admin, '', None, None, lines=[line])
        self.assertEqual(rows[0][5:8], [5, 2, 3])
        response = self.client.get(reverse('reports'), {'kind': 'orders', 'format': 'csv'})
        self.assertContains(response, ';5;2;3;')

    def test_removed_receipt_line_is_not_received_and_original_is_preserved(self):
        order, line = self.order_line(kind='purchase')
        document = self.order_document(line, 3)
        old = self.remove_and_replace(document)
        post_document(document.pk, self.admin, expected_revision=document.revision)
        order.refresh_from_db()
        self.assertEqual((line.fulfilled_quantity, line.remaining_quantity, order.status), (2, 3, 'approved'))
        self.assertEqual(stock_balance(self.raw, self.source), 102)
        old.refresh_from_db()
        self.assertEqual((old.quantity, old.active), (3, False))
        _, rows = order_rows(self.admin, '', None, None, lines=[line])
        self.assertEqual(rows[0][5:8], [5, 2, 3])

    def test_completion_and_reversal_follow_only_actual_delivery(self):
        self.put_stock(self.output)
        order, line = self.order_line()
        first = self.order_document(line, 3)
        self.remove_and_replace(first)
        post_document(first.pk, self.admin)
        last = self.order_document(line, 3)
        post_document(last.pk, self.admin)
        order.refresh_from_db()
        self.assertEqual((order.status, line.fulfilled_quantity), ('completed', 5))
        reverse_document(first.pk, self.admin, 'İki adet teslimatı geri al')
        order.refresh_from_db(); line.refresh_from_db()
        self.assertEqual((order.status, line.fulfilled_quantity, line.remaining_quantity), ('approved', 3, 2))
        self.assertEqual(stock_balance(self.main, self.output), 7)


class DeliveryReservationTests(FourFixesFixture):
    def test_other_warehouse_reservation_is_released_at_completion_and_audited(self):
        for warehouse in (self.output, self.other): self.put_stock(warehouse)
        order, line = self.order_line()
        reservation = reserve_order_line(line.pk, self.output.pk, 5, self.admin)
        document = self.order_document(line, 5, self.other)
        post_document(document.pk, self.admin)
        reservation.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((order.status, reservation.state, reservation.quantity), ('completed', 'released', 5))
        self.assertEqual((reserved_stock(self.main, self.output), available_stock(self.main, self.output)), (0, 10))
        event = AuditEvent.objects.filter(model_label='erp.stockreservation', object_id=str(reservation.pk), action='update').latest('pk')
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertIn(document.number, event.reason)
        self.assertIsNotNone(event.created_at)
        count = AuditEvent.objects.count()
        post_document(document.pk, self.admin)
        self.assertEqual(AuditEvent.objects.count(), count)

    def test_partial_delivery_keeps_only_remaining_demand_reserved(self):
        for warehouse in (self.output, self.other): self.put_stock(warehouse)
        order, line = self.order_line()
        reservation = reserve_order_line(line.pk, self.output.pk, 5, self.admin)
        post_document(self.order_document(line, 2, self.other).pk, self.admin)
        reservation.refresh_from_db()
        self.assertEqual((reservation.quantity, reservation.state, line.remaining_quantity), (3, 'active', 3))
        event = AuditEvent.objects.filter(model_label='erp.stockreservation', object_id=str(reservation.pk), action='update').latest('pk')
        self.assertEqual(Decimal(event.changes['quantity']['before']), 5)
        self.assertEqual(Decimal(event.changes['quantity']['after']), 3)
        post_document(self.order_document(line, 3, self.output).pk, self.admin)
        reservation.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((reservation.state, order.status, reserved_stock(self.main)), ('consumed', 'completed', 0))

    def test_reservations_across_depots_are_trimmed_without_affecting_another_order(self):
        third = Warehouse.objects.create(name='Üçüncü ürün deposu')
        for warehouse in (self.output, self.other, third): self.put_stock(warehouse)
        _, line = self.order_line()
        _, other_line = self.order_line()
        first = reserve_order_line(line.pk, self.output.pk, 3, self.admin)
        last = reserve_order_line(line.pk, third.pk, 2, self.admin)
        unrelated = reserve_order_line(other_line.pk, third.pk, 4, self.admin)
        post_document(self.order_document(line, 1, self.other).pk, self.admin)
        first.refresh_from_db(); last.refresh_from_db(); unrelated.refresh_from_db()
        self.assertEqual((first.quantity, last.quantity, unrelated.quantity), (3, 1, 4))
        self.assertEqual(unrelated.state, 'active')
        self.assertEqual(line.remaining_quantity, 4)

    def test_split_document_lines_are_reconciled_after_the_whole_delivery(self):
        for warehouse in (self.output, self.other): self.put_stock(warehouse)
        _, line = self.order_line()
        reservation = reserve_order_line(line.pk, self.output.pk, 5, self.admin)
        document = self.order_document(line, 2, self.other)
        StockDocumentLine.objects.create(document=document, product=self.main, order_line=line, quantity=1)
        post_document(document.pk, self.admin)
        reservation.refresh_from_db()
        self.assertEqual((line.fulfilled_quantity, reservation.quantity), (3, 2))

    def test_final_delivery_also_releases_stale_reservations_on_other_order_lines(self):
        for warehouse in (self.output, self.other): self.put_stock(warehouse)
        order, first = self.order_line(quantity=2)
        order.status = 'draft'; order.save()
        second = OrderLine.objects.create(order=order, product=self.main, quantity=1, unit_price=10)
        order.status = 'approved'; order.full_clean(); order.save()
        post_document(self.order_document(first, 2, self.other).pk, self.admin)
        # Simulate a reservation left behind by the previous version.
        from .models import StockReservation
        stale = StockReservation.objects.create(product=self.main, warehouse=self.output, order_line=first,
            quantity=2, unit='adet', created_by=self.admin)
        post_document(self.order_document(second, 1, self.other).pk, self.admin)
        order.refresh_from_db(); stale.refresh_from_db()
        self.assertEqual((order.status, stale.state, reserved_stock(self.main)), ('completed', 'released', 0))

    def test_failed_reconciliation_rolls_back_movements_reservations_and_audit(self):
        for warehouse in (self.output, self.other): self.put_stock(warehouse)
        _, line = self.order_line()
        reservation = reserve_order_line(line.pk, self.output.pk, 5, self.admin)
        document = self.order_document(line, 2, self.other)
        from .stock import trim_order_reservations
        def fail_after_trim(*args):
            trim_order_reservations(*args)
            raise ValidationError('Sentetik kayıt hatası')
        before = AuditEvent.objects.count()
        with patch('erp.stock.trim_order_reservations', side_effect=fail_after_trim):
            with self.assertRaises(ValidationError): post_document(document.pk, self.admin)
        document.refresh_from_db(); reservation.refresh_from_db()
        self.assertEqual((document.status, reservation.quantity, reservation.state), ('draft', 5, 'active'))
        self.assertFalse(StockMovement.objects.filter(document_line__document=document).exists())
        self.assertEqual(AuditEvent.objects.count(), before)


class ProductionDependencyTests(FourFixesFixture):
    def test_direct_and_self_cycles_are_rejected(self):
        first = self.shared_job(); second = self.shared_job(first)
        for predecessor in (first, second):
            with self.assertRaisesMessage(ValidationError, 'döngü'), transaction.atomic():
                first.preceding_jobs.add(predecessor)
        self.assertFalse(first.preceding_jobs.exists())

    def test_transitive_cycle_and_mixed_batch_are_rejected_atomically(self):
        first = self.shared_job(); second = self.shared_job(first); third = self.shared_job(second)
        unrelated = self.shared_job()
        before = AuditEvent.objects.count()
        with self.assertRaisesMessage(ValidationError, 'döngü'), transaction.atomic():
            first.preceding_jobs.add(unrelated, third)
        self.assertFalse(first.preceding_jobs.exists())
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_failed_set_restores_removed_edges_and_audit(self):
        original = self.shared_job(); first = self.shared_job(original); second = self.shared_job(first)
        before = AuditEvent.objects.count()
        with self.assertRaisesMessage(ValidationError, 'döngü'), transaction.atomic():
            first.preceding_jobs.set([second])
        self.assertEqual(list(first.preceding_jobs.all()), [original])
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_reverse_relation_checks_cycles_and_records_the_actual_dependent(self):
        first = self.shared_job(); second = self.shared_job()
        with audit_actor(self.admin): first.following_jobs.add(second)
        event = self.dependency_events(second).get()
        self.assertEqual((event.actor_id, event.changes['preceding_jobs']['after']), (self.admin.pk, [first.pk]))
        with self.assertRaisesMessage(ValidationError, 'döngü'), transaction.atomic():
            second.following_jobs.add(first)
        with audit_actor(self.admin): first.following_jobs.remove(second)
        self.assertFalse(second.preceding_jobs.exists())
        self.assertEqual(self.dependency_events(second).count(), 2)

    def test_valid_shared_dependencies_and_repeated_add_keep_history_stable(self):
        first = self.shared_job(); second = self.shared_job(first); third = self.shared_job(first, second)
        count = self.dependency_events(third).count()
        third.preceding_jobs.add(first, second)
        self.assertEqual(self.dependency_events(third).count(), count)
        third.preceding_jobs.clear()
        self.assertFalse(third.preceding_jobs.exists())
        self.assertEqual(self.dependency_events(third).count(), count + 1)

    def test_started_job_dependencies_cannot_be_changed_in_either_direction(self):
        parent = self.shared_job(); self.finish_tracking(parent)
        child = self.shared_job(parent); advance_job(child.pk, self.admin, 'start')
        other = self.shared_job()
        for operation in (lambda: child.preceding_jobs.add(other), lambda: child.preceding_jobs.remove(parent),
                lambda: child.preceding_jobs.clear(), lambda: parent.following_jobs.clear(),
                lambda: other.following_jobs.add(child)):
            with self.assertRaisesMessage(ValidationError, 'bağlantıları değiştirilemez'), transaction.atomic(): operation()
        self.assertEqual(list(child.preceding_jobs.all()), [parent])

    def test_form_rejects_cycle_and_rolls_back_other_job_field_changes(self):
        first = self.shared_job(); second = self.shared_job(first)
        before = AuditEvent.objects.count()
        response = self.client.post(reverse('edit', args=['uretim-isleri', first.pk]), {
            'number': first.number, 'product': self.main.pk, 'quantity': '1', 'unit': 'adet',
            'flow': first.flow_id, 'assembly_required': 'False', 'stock_mode': 'tracking',
            'preceding_jobs': [second.pk], 'note': 'Kaydedilmemesi gereken değişiklik'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'döngü')
        first.refresh_from_db()
        self.assertEqual(first.note, '')
        self.assertFalse(first.preceding_jobs.exists())
        self.assertEqual(AuditEvent.objects.count(), before)


class DependentReversalTests(FourFixesFixture):
    def test_running_dependent_blocks_reversal_without_changing_history(self):
        parent = self.job(); posting = self.complete(parent)
        child = self.shared_job(parent); advance_job(child.pk, self.admin, 'start')
        before = AuditEvent.objects.count()
        moves = list(StockMovement.objects.values())
        with self.assertRaisesMessage(ValidationError, 'Bağlı üretim işleri'):
            reverse_production(posting.pk, self.admin, 'Önce bağlı işi düzelt')
        parent.refresh_from_db()
        self.assertEqual((parent.state, parent.actual_quantity), ('completed', 2))
        self.assertFalse(StockDocument.objects.filter(production_posting=posting).exists())
        self.assertEqual(list(StockMovement.objects.values()), moves)
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_completed_dependent_blocks_reversal(self):
        parent = self.job(); posting = self.complete(parent)
        child = self.shared_job(parent); self.finish_tracking(child)
        with self.assertRaisesMessage(ValidationError, child.number):
            reverse_production(posting.pk, self.admin, 'Bağlı iş tamamlandı')

    def test_draft_dependent_allows_reversal_but_cannot_start_until_parent_completes(self):
        parent = self.job(); posting = self.complete(parent)
        child = self.shared_job(parent)
        original = posting.__class__.objects.filter(pk=posting.pk).values().get()
        reversal = reverse_production(posting.pk, self.admin, 'Üretim düzeltmesi')
        self.assertEqual(reverse_production(posting.pk, self.admin, 'Tekrar').pk, reversal.pk)
        self.assertEqual(posting.__class__.objects.filter(pk=posting.pk).values().get(), original)
        self.assertEqual((stock_balance(self.raw), stock_balance(self.main)), (100, 0))
        with self.assertRaisesMessage(ValidationError, 'tamamlanmadan'):
            advance_job(child.pk, self.admin, 'start')

    def test_cancelled_dependent_allows_reversal(self):
        parent = self.job(); posting = self.complete(parent)
        child = self.shared_job(parent); advance_job(child.pk, self.admin, 'start')
        cancel_job(child.pk, self.admin, 'Bağlı işi iptal et')
        reversal = reverse_production(posting.pk, self.admin, 'Önceki üretimi düzelt')
        self.assertEqual(reversal.status, 'posted')

    def test_legacy_incomplete_predecessor_blocks_further_progress(self):
        parent = self.job(); self.complete(parent)
        child = self.shared_job(parent); advance_job(child.pk, self.admin, 'start')
        # Reproduce a state left by the old version, without exposing a bypass in application code.
        ProductionJob._base_manager.filter(pk=parent.pk).update(state='running')
        before = AuditEvent.objects.count()
        step = child.steps.first()
        with self.assertRaisesMessage(ValidationError, 'ilerletilemez'):
            advance_job(child.pk, self.admin, str(step.pk))
        with self.assertRaisesMessage(ValidationError, 'Bağlı üretim işleri'):
            cancel_job(parent.pk, self.admin, 'Önceki sürümden kalan tutarsızlık')
        step.refresh_from_db()
        self.assertIsNone(step.completed_at)
        self.assertEqual(AuditEvent.objects.count(), before)
