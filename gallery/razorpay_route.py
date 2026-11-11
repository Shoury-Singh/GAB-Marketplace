import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.db import transaction

from .models import Order, PaymentAttempt, SellerPayoutProfile
from .payment_services import (
    PaymentRecordingError,
    _commission_snapshot,
    order_amount_paise,
)


class RazorpayRouteError(PaymentRecordingError):
    pass


def razorpay_route_enabled():
    return (
        os.environ.get('GAB_PAYMENT_PROVIDER', '').strip().lower() == 'razorpay_route'
        and os.environ.get('GAB_RAZORPAY_ROUTE_APPROVED', '').strip().lower() in {'true', '1', 'yes'}
        and bool(os.environ.get('GAB_RAZORPAY_KEY_ID'))
        and bool(os.environ.get('GAB_RAZORPAY_KEY_SECRET'))
        and bool(os.environ.get('GAB_RAZORPAY_WEBHOOK_SECRET'))
    )


def _seller_profile_and_plan(order):
    profile = (
        SellerPayoutProfile.objects.select_related('commission_plan')
        .filter(user_id=order.seller_id)
        .first()
    )
    if (
        not profile
        or profile.provider != SellerPayoutProfile.Provider.RAZORPAY_ROUTE
        or profile.onboarding_status != SellerPayoutProfile.OnboardingStatus.ACTIVE
        or not profile.provider_account_id
    ):
        raise RazorpayRouteError('The seller has not completed provider payout onboarding.')
    plan = profile.commission_plan
    if plan is None or not plan.is_active:
        from .models import CommissionPlan
        plan = CommissionPlan.objects.filter(is_default=True, is_active=True).first()
    if not plan:
        raise RazorpayRouteError('GAB has no active commission plan.')
    if plan.gst_rate_bps is None:
        raise RazorpayRouteError('Commission GST setup must be confirmed before online checkout.')
    return profile, plan


def checkout_is_available(order):
    if not razorpay_route_enabled():
        return False
    if order.status not in {Order.Status.ACCEPTED, Order.Status.PROCESSING}:
        return False
    if order.payment_status not in {'UNPAID', 'FAILED'}:
        return False
    if order.payment_attempts.filter(
        status__in=[PaymentAttempt.Status.SUCCEEDED, PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING],
    ).exists():
        return False
    try:
        _seller_profile_and_plan(order)
        order_amount_paise(order)
    except PaymentRecordingError:
        return False
    return True


def _create_provider_order(attempt, profile):
    key_id = os.environ['GAB_RAZORPAY_KEY_ID']
    key_secret = os.environ['GAB_RAZORPAY_KEY_SECRET']
    basic_token = base64.b64encode(f'{key_id}:{key_secret}'.encode()).decode()
    payload = {
        'amount': attempt.amount_paise,
        'currency': attempt.currency,
        'receipt': f'gab-{attempt.order_id}-{attempt.pk}',
        'notes': {
            'gab_order_id': str(attempt.order_id),
            'gab_payment_attempt_id': str(attempt.pk),
        },
        'transfers': [{
            'account': profile.provider_account_id,
            'amount': attempt.seller_amount_paise,
            'currency': attempt.currency,
            'notes': {
                'gab_order_id': str(attempt.order_id),
                'gab_payment_attempt_id': str(attempt.pk),
            },
        }],
    }
    request = Request(
        'https://api.razorpay.com/v1/orders',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Basic {basic_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        # A definite client rejection can be retried after the cause is fixed.
        # Server and gateway failures are uncertain, so keep the attempt open.
        definitive = 400 <= error.code < 500
        attempt.refresh_from_db()
        if definitive:
            attempt.status = PaymentAttempt.Status.FAILED
            attempt.failure_code = f'HTTP_{error.code}'
            attempt.failure_message = 'Provider rejected the checkout request.'
            attempt.save(update_fields=[
                'status', 'failure_code', 'failure_message', 'updated_at',
            ])
            Order.objects.filter(
                pk=attempt.order_id,
                payment_status='PENDING',
            ).update(payment_status='FAILED')
        else:
            attempt.status = PaymentAttempt.Status.PENDING
            attempt.failure_code = f'HTTP_{error.code}'
            attempt.failure_message = 'Provider response is uncertain; reconcile before retrying.'
            attempt.save(update_fields=[
                'status', 'failure_code', 'failure_message', 'updated_at',
            ])
        raise RazorpayRouteError(
            'Checkout could not be confirmed. Do not pay again until the transaction is reconciled.'
        )
    except (URLError, TimeoutError, OSError, ValueError):
        attempt.refresh_from_db()
        attempt.status = PaymentAttempt.Status.PENDING
        attempt.failure_code = 'PROVIDER_TIMEOUT'
        attempt.failure_message = 'Provider response is uncertain; reconcile before retrying.'
        attempt.save(update_fields=[
            'status', 'failure_code', 'failure_message', 'updated_at',
        ])
        raise RazorpayRouteError(
            'Checkout could not be confirmed. Do not pay again until the transaction is reconciled.'
        )

    if not isinstance(result, dict):
        attempt.refresh_from_db()
        attempt.status = PaymentAttempt.Status.PENDING
        attempt.failure_code = 'INVALID_PROVIDER_RESPONSE'
        attempt.failure_message = 'Provider response needs manual reconciliation.'
        attempt.save(update_fields=[
            'status', 'failure_code', 'failure_message', 'updated_at',
        ])
        raise RazorpayRouteError(
            'Provider order needs reconciliation. Do not start another payment.'
        )
    provider_order_id = result.get('id')
    if (
        not provider_order_id
        or result.get('amount') != attempt.amount_paise
        or result.get('currency') != attempt.currency
    ):
        attempt.refresh_from_db()
        attempt.status = PaymentAttempt.Status.PENDING
        attempt.failure_code = 'PROVIDER_RESPONSE_MISMATCH'
        attempt.failure_message = 'Provider order needs manual reconciliation.'
        attempt.save(update_fields=[
            'status', 'failure_code', 'failure_message', 'updated_at',
        ])
        raise RazorpayRouteError(
            'Provider order needs reconciliation. Do not start another payment.'
        )

    attempt.provider_order_id = provider_order_id
    attempt.status = PaymentAttempt.Status.PENDING
    attempt.failure_code = ''
    attempt.failure_message = ''
    attempt.save(update_fields=[
        'provider_order_id', 'status', 'failure_code', 'failure_message', 'updated_at',
    ])
    return attempt


def start_checkout(*, order_id, buyer_id):
    if not razorpay_route_enabled():
        raise RazorpayRouteError('Online checkout is not enabled for GAB yet.')

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.buyer_id != buyer_id:
            raise RazorpayRouteError('Only the buyer can start checkout.')
        if order.status not in {Order.Status.ACCEPTED, Order.Status.PROCESSING}:
            raise RazorpayRouteError('The seller must accept the order before payment.')
        if order.payment_status not in {'UNPAID', 'FAILED'}:
            raise RazorpayRouteError('This order has a pending or completed payment that must be reconciled first.')
        if order.payment_attempts.filter(
            status__in=[PaymentAttempt.Status.SUCCEEDED, PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING],
        ).exists():
            raise RazorpayRouteError('A previous attempt is still open or succeeded. Reconcile it before retrying.')
        if order.payment_status == 'PENDING':
            raise RazorpayRouteError(
                'A payment is already pending. Do not pay again; reconcile this attempt first.'
            )
        profile, plan = _seller_profile_and_plan(order)
        amount = order_amount_paise(order)
        plan, rate, commission, gst_rate, gst, seller_amount = _commission_snapshot(
            order.seller,
            amount,
        )
        open_attempt = order.payment_attempts.filter(
            provider=PaymentAttempt.Provider.RAZORPAY_ROUTE,
            status__in=[PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING],
        ).first()
        if open_attempt:
            raise RazorpayRouteError(
                'A previous checkout attempt is still open. Reconcile it before retrying.'
            )
        else:
            attempt = PaymentAttempt.objects.create(
                order=order,
                provider=PaymentAttempt.Provider.RAZORPAY_ROUTE,
                status=PaymentAttempt.Status.CREATED,
                amount_paise=amount,
                commission_plan=plan,
                commission_rate_bps=rate,
                commission_amount_paise=commission,
                gst_rate_bps=gst_rate,
                gst_amount_paise=gst,
                seller_amount_paise=seller_amount,
            )
            order.payment_status = 'PENDING'
            order.save(update_fields=['payment_status', 'updated_at'])
            create_provider_order = True

    if create_provider_order:
        attempt = _create_provider_order(attempt, profile)
    return {
        'key_id': os.environ['GAB_RAZORPAY_KEY_ID'],
        'provider_order_id': attempt.provider_order_id,
        'amount_paise': attempt.amount_paise,
        'currency': attempt.currency,
        'attempt_id': attempt.pk,
    }
