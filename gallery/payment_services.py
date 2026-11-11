from calendar import monthrange
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import (
    CommissionLedger,
    CommissionPlan,
    CommissionStatement,
    Order,
    PaymentAttempt,
    PaymentWebhookEvent,
    SellerPayoutProfile,
)


class PaymentRecordingError(Exception):
    """A user-facing validation error when a payment cannot be recorded."""


def order_amount_paise(order):
    if order.total_amount is None:
        raise PaymentRecordingError(
            'This older order has no agreed price recorded. Ask the seller to confirm it first.'
        )
    amount = Decimal(str(order.total_amount)).quantize(Decimal('0.01'))
    if amount <= 0:
        raise PaymentRecordingError('The order amount must be greater than zero.')
    return int(amount * 100)


def _rounded_bps_amount(amount_paise, rate_bps):
    return int(
        (Decimal(amount_paise) * Decimal(rate_bps) / Decimal(10000))
        .quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    )


def _commission_plan_for(seller):
    profile = (
        SellerPayoutProfile.objects.select_related('commission_plan')
        .filter(user=seller)
        .first()
    )
    if profile and profile.commission_plan_id and profile.commission_plan.is_active:
        return profile.commission_plan
    plan = CommissionPlan.objects.filter(is_default=True, is_active=True).first()
    if not plan:
        raise PaymentRecordingError('GAB has no active commission plan configured.')
    return plan


def _commission_snapshot(seller, amount_paise):
    plan = _commission_plan_for(seller)
    rate = plan.commission_rate_bps
    commission = _rounded_bps_amount(amount_paise, rate)
    gst = (
        _rounded_bps_amount(commission, plan.gst_rate_bps)
        if plan.gst_rate_bps is not None else None
    )
    seller_amount = amount_paise - commission - (gst or 0)
    if seller_amount <= 0:
        raise PaymentRecordingError('The commission and tax leave no payable amount for the seller.')
    return plan, rate, commission, plan.gst_rate_bps, gst, seller_amount


def _record_commission(order, attempt, amount_paise):
    plan = attempt.commission_plan or _commission_plan_for(order.seller)
    rate = attempt.commission_rate_bps if attempt.commission_rate_bps is not None else plan.commission_rate_bps
    commission = attempt.commission_amount_paise
    if commission is None:
        commission = _rounded_bps_amount(amount_paise, rate)
    gst_rate = (
        attempt.gst_rate_bps
        if attempt.commission_plan_id is not None
        else plan.gst_rate_bps
    )
    gst = attempt.gst_amount_paise
    if gst is None and gst_rate is not None:
        gst = _rounded_bps_amount(commission, gst_rate)
    if attempt.commission_plan_id is None:
        attempt.commission_plan = plan
        attempt.commission_rate_bps = rate
        attempt.commission_amount_paise = commission
        attempt.gst_rate_bps = gst_rate
        attempt.gst_amount_paise = gst
        attempt.save(update_fields=[
            'commission_plan', 'commission_rate_bps', 'commission_amount_paise',
            'gst_rate_bps', 'gst_amount_paise', 'updated_at',
        ])
    ledger, created = CommissionLedger.objects.get_or_create(
        order=order,
        defaults={
            'payment_attempt': attempt,
            'commission_plan': plan,
            'basis_amount_paise': amount_paise,
            'commission_rate_bps': rate,
            'commission_amount_paise': commission,
            'gst_rate_bps': gst_rate,
            'gst_amount_paise': gst,
        },
    )
    if not created and ledger.payment_attempt_id not in (None, attempt.pk):
        raise PaymentRecordingError('A different payment is already linked to this commission.')
    return ledger


@transaction.atomic
def report_offline_payment(*, order_id, seller_id, method, reference=''):
    """Record a seller-reported payment with method, actor, timestamp, and reference."""
    order = Order.objects.select_for_update().select_related('seller').get(pk=order_id)
    if order.seller_id != seller_id:
        raise PaymentRecordingError('Only this order’s seller can report offline payment.')
    if order.status in {Order.Status.PENDING, Order.Status.DECLINED, Order.Status.CANCELLED}:
        raise PaymentRecordingError('Only an accepted order can have a payment recorded.')
    if order.payment_status not in {'UNPAID', 'FAILED'}:
        raise PaymentRecordingError('This order has a pending or completed transaction that must be reconciled first.')
    if order.payment_attempts.filter(status=PaymentAttempt.Status.SUCCEEDED).exists():
        raise PaymentRecordingError('A successful payment attempt already exists; reconcile the order before adding another.')
    if order.payment_status == 'OFFLINE_REPORTED':
        existing = order.payment_attempts.filter(
            provider=PaymentAttempt.Provider.OFFLINE,
            status=PaymentAttempt.Status.SUCCEEDED,
        ).first()
        if existing and existing.offline_method == method and existing.offline_reference == reference.strip():
            return existing
        raise PaymentRecordingError('An offline payment report already exists for this order.')

    if order.payment_attempts.filter(
        provider__in=[
            PaymentAttempt.Provider.RAZORPAY_ROUTE,
            PaymentAttempt.Provider.CASHFREE_EASY_SPLIT,
        ],
        status__in=[PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING],
    ).exists():
        raise PaymentRecordingError(
            'An online payment is still pending. Reconcile it before reporting offline payment.'
        )

    amount_paise = order_amount_paise(order)
    plan, rate, commission, gst_rate, gst, seller_amount = _commission_snapshot(
        order.seller,
        amount_paise,
    )
    now = timezone.now()
    attempt = PaymentAttempt.objects.create(
        order=order,
        provider=PaymentAttempt.Provider.OFFLINE,
        status=PaymentAttempt.Status.SUCCEEDED,
        amount_paise=amount_paise,
        commission_plan=plan,
        commission_rate_bps=rate,
        commission_amount_paise=commission,
        gst_rate_bps=gst_rate,
        gst_amount_paise=gst,
        seller_amount_paise=seller_amount,
        offline_method=method,
        offline_reference=reference.strip(),
        recorded_by_id=seller_id,
        succeeded_at=now,
    )
    order.payment_status = 'OFFLINE_REPORTED'
    order.paid_at = now
    order.save(update_fields=['payment_status', 'paid_at', 'updated_at'])
    _record_commission(order, attempt, amount_paise)
    return attempt


@transaction.atomic
def record_verified_provider_capture(
    *, provider, event_id, event_type, attempt_id, provider_order_id,
    provider_payment_id, amount_paise, currency, payload_hash='', safe_payload=None,
):
    """
    Process a captured event only after its provider adapter has verified the
    signature against the original HTTP body. Never call from browser input.
    """
    event, created = PaymentWebhookEvent.objects.get_or_create(
        provider=provider,
        event_id=event_id,
        defaults={
            'event_type': event_type,
            'payload_hash': payload_hash,
            'safe_payload': safe_payload or {},
            'signature_verified': True,
        },
    )
    if not created:
        if event.status == PaymentWebhookEvent.Status.PROCESSED:
            return False, event
        event.event_type = event_type
        event.payload_hash = payload_hash
        event.safe_payload = safe_payload or {}
        event.signature_verified = True
        event.status = PaymentWebhookEvent.Status.RECEIVED
        event.error_summary = ''
        event.processed_at = None
        event.save(update_fields=[
            'event_type', 'payload_hash', 'safe_payload', 'signature_verified',
            'status', 'error_summary', 'processed_at',
        ])

    try:
        attempt = (
            PaymentAttempt.objects.select_for_update()
            .select_related('order')
            .get(pk=attempt_id)
        )
    except PaymentAttempt.DoesNotExist:
        event.status = PaymentWebhookEvent.Status.FAILED
        event.error_summary = 'Payment attempt was not found.'
        event.processed_at = timezone.now()
        event.save(update_fields=['status', 'error_summary', 'processed_at', 'signature_verified'])
        return False, event

    order = Order.objects.select_for_update().get(pk=attempt.order_id)
    valid = (
        attempt.provider == provider
        and (not attempt.provider_order_id or attempt.provider_order_id == provider_order_id)
        and attempt.amount_paise == int(amount_paise)
        and attempt.currency == currency.upper()
    )
    problem = None
    if not valid:
        problem = 'Payment details did not match the attempt.'
    elif order.payment_status == 'OFFLINE_REPORTED':
        problem = 'Offline payment already reported; manual reconciliation required.'
    elif order.payment_status == 'PAID' and attempt.status != PaymentAttempt.Status.SUCCEEDED:
        problem = 'Another payment already paid this order; manual reconciliation required.'
    elif order.status in {Order.Status.CANCELLED, Order.Status.DECLINED}:
        problem = 'Order was cancelled before capture; manual refund review required.'
    elif order.payment_status not in {'UNPAID', 'PENDING', 'FAILED', 'PAID'}:
        problem = 'Order is in a refund or offline state; manual reconciliation required.'
    elif not provider_payment_id or int(amount_paise) <= 0:
        problem = 'Provider payment ID or captured amount is invalid.'
    elif PaymentAttempt.objects.filter(
        provider=provider,
        provider_payment_id=provider_payment_id,
    ).exclude(pk=attempt.pk).exists():
        problem = 'Provider payment ID is already attached to another attempt.'
    if problem:
        event.status = PaymentWebhookEvent.Status.FAILED
        event.error_summary = problem
        event.payment_attempt = attempt
        event.processed_at = timezone.now()
        event.save(update_fields=[
            'status', 'error_summary', 'payment_attempt', 'processed_at', 'signature_verified',
        ])
        return False, event

    if attempt.status != PaymentAttempt.Status.SUCCEEDED:
        now = timezone.now()
        attempt.status = PaymentAttempt.Status.SUCCEEDED
        attempt.provider_order_id = provider_order_id
        attempt.provider_payment_id = provider_payment_id
        attempt.succeeded_at = now
        attempt.save(update_fields=[
            'status', 'provider_order_id', 'provider_payment_id', 'succeeded_at', 'updated_at',
        ])
        order.payment_status = 'PAID'
        order.paid_at = now
        order.save(update_fields=['payment_status', 'paid_at', 'updated_at'])
        _record_commission(order, attempt, int(amount_paise))

    event.status = PaymentWebhookEvent.Status.PROCESSED
    event.payment_attempt = attempt
    event.processed_at = timezone.now()
    event.save(update_fields=['status', 'payment_attempt', 'processed_at', 'signature_verified'])
    return True, event


@transaction.atomic
def generate_monthly_commission_statements(period_start):
    """Prepare draft monthly statements from accrued offline commission."""
    period_end = period_start.replace(day=monthrange(period_start.year, period_start.month)[1]) + timedelta(days=1)
    start = timezone.make_aware(datetime.combine(period_start, time.min))
    end = timezone.make_aware(datetime.combine(period_end, time.min))
    entries = list(
        CommissionLedger.objects.select_for_update()
        .select_related('order__seller')
        .filter(
            statement__isnull=True,
            created_at__gte=start,
            created_at__lt=end,
            payment_attempt__provider=PaymentAttempt.Provider.OFFLINE,
            status=CommissionLedger.Status.ACCRUED,
        )
        .order_by('order__seller_id', 'created_at', 'pk')
    )
    grouped = {}
    for entry in entries:
        grouped.setdefault(entry.order.seller_id, []).append(entry)

    statements = []
    for seller_id, rows in grouped.items():
        gst_total = (
            None if any(row.gst_amount_paise is None for row in rows)
            else sum(row.gst_amount_paise for row in rows)
        )
        statement, created = CommissionStatement.objects.get_or_create(
            seller_id=seller_id,
            period_start=period_start,
            defaults={
                'statement_number': f'GAB-CS-{period_start:%Y%m}-{seller_id}',
                'commission_total_paise': sum(row.commission_amount_paise for row in rows),
                'gst_total_paise': gst_total,
            },
        )
        if not created:
            if statement.status != CommissionStatement.Status.DRAFT:
                continue
            statement.commission_total_paise += sum(row.commission_amount_paise for row in rows)
            statement.gst_total_paise = (
                None if statement.gst_total_paise is None or gst_total is None
                else statement.gst_total_paise + gst_total
            )
            statement.save(update_fields=['commission_total_paise', 'gst_total_paise'])
        CommissionLedger.objects.filter(pk__in=[row.pk for row in rows]).update(
            statement=statement,
            status=CommissionLedger.Status.INVOICED,
        )
        statements.append(statement)
    return statements


@transaction.atomic
def record_verified_provider_failure(
    *, provider, event_id, event_type, attempt_id, provider_order_id,
    provider_payment_id='', failure_code='', payload_hash='', safe_payload=None,
):
    """Record a signed provider failure without allowing it to undo a capture."""
    event, created = PaymentWebhookEvent.objects.get_or_create(
        provider=provider,
        event_id=event_id,
        defaults={
            'event_type': event_type,
            'payload_hash': payload_hash,
            'safe_payload': safe_payload or {},
            'signature_verified': True,
        },
    )
    if not created:
        if event.status == PaymentWebhookEvent.Status.PROCESSED:
            return False, event
        event.event_type = event_type
        event.payload_hash = payload_hash
        event.safe_payload = safe_payload or {}
        event.signature_verified = True
        event.status = PaymentWebhookEvent.Status.RECEIVED
        event.error_summary = ''
        event.processed_at = None
        event.save(update_fields=[
            'event_type', 'payload_hash', 'safe_payload', 'signature_verified',
            'status', 'error_summary', 'processed_at',
        ])
    try:
        attempt = PaymentAttempt.objects.select_for_update().select_related('order').get(pk=attempt_id)
    except PaymentAttempt.DoesNotExist:
        event.status = PaymentWebhookEvent.Status.FAILED
        event.error_summary = 'Payment attempt was not found.'
        event.processed_at = timezone.now()
        event.save(update_fields=['status', 'error_summary', 'processed_at', 'signature_verified'])
        return False, event

    order = Order.objects.select_for_update().get(pk=attempt.order_id)
    if attempt.provider != provider or (
        attempt.provider_order_id and attempt.provider_order_id != provider_order_id
    ):
        event.status = PaymentWebhookEvent.Status.FAILED
        event.error_summary = 'Provider order did not match the payment attempt.'
    else:
        if attempt.status != PaymentAttempt.Status.SUCCEEDED:
            attempt.status = PaymentAttempt.Status.FAILED
            attempt.provider_order_id = provider_order_id or attempt.provider_order_id
            attempt.provider_payment_id = provider_payment_id
            attempt.failure_code = failure_code[:80]
            attempt.save(update_fields=[
                'status', 'provider_order_id', 'provider_payment_id',
                'failure_code', 'updated_at',
            ])
            if order.payment_status == 'PENDING':
                order.payment_status = 'FAILED'
                order.save(update_fields=['payment_status', 'updated_at'])
        event.status = PaymentWebhookEvent.Status.PROCESSED
        event.payment_attempt = attempt
    event.processed_at = timezone.now()
    event.save(update_fields=[
        'status', 'error_summary', 'payment_attempt', 'processed_at', 'signature_verified',
    ])
    return event.status == PaymentWebhookEvent.Status.PROCESSED, event
