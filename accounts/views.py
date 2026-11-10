import hashlib
import hmac
import logging
import secrets
import smtplib
from datetime import timedelta

from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .forms import (
    AccountLocationForm,
    AddAccountRoleForm,
    PasswordResetRequestForm,
    PasswordResetVerifyForm,
    SwitchAccountRoleForm,
    UserRegistrationForm,
    UserLoginForm,
)
from .models import PasswordResetChallenge, User, UserRole
from gallery.models import Order, Product, QuoteRequest, SellerBanner


logger = logging.getLogger(__name__)
PASSWORD_RESET_TTL = timedelta(minutes=10)
PASSWORD_RESET_RATE_WINDOW = timedelta(minutes=15)
PASSWORD_RESET_MAX_EMAIL_REQUESTS = 3
PASSWORD_RESET_MAX_IP_REQUESTS = 10
PASSWORD_RESET_MAX_ATTEMPTS = 5


def _reset_digest(value, purpose):
    key = settings.SECRET_KEY.encode()
    message = f'{purpose}:{value}'.encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _password_reset_email(code):
    return (
        'Use this code to reset your GAB password: '
        f'{code}\n\n'
        'The code expires in 10 minutes and can only be used once. '
        'If you did not request a password reset, you can ignore this email.'
    )


def password_reset_view(request):
    if request.user.is_authenticated:
        return redirect(request.user.get_dashboard_url())

    request_form = PasswordResetRequestForm()
    verify_form = PasswordResetVerifyForm()
    challenge_id = request.session.get('password_reset_challenge')
    challenge = PasswordResetChallenge.objects.filter(
        pk=challenge_id,
        is_used=False,
    ).first() if challenge_id else None
    now = timezone.now()

    if challenge and (challenge.expires_at <= now or challenge.attempts >= PASSWORD_RESET_MAX_ATTEMPTS):
        challenge.is_used = True
        challenge.save(update_fields=['is_used'])
        request.session.pop('password_reset_challenge', None)
        challenge = None

    if request.method == 'POST' and request.POST.get('action') == 'request':
        request_form = PasswordResetRequestForm(request.POST)
        if request_form.is_valid():
            email = request_form.cleaned_data['email'].strip().lower()
            email_digest = _reset_digest(email, 'password-reset-email')
            remote_ip = request.META.get('REMOTE_ADDR', '')
            ip_digest = _reset_digest(remote_ip, 'password-reset-ip')
            window_start = now - PASSWORD_RESET_RATE_WINDOW

            PasswordResetChallenge.objects.filter(
                expires_at__lt=now - timedelta(days=1),
            ).delete()
            recent = PasswordResetChallenge.objects.filter(created_at__gte=window_start)
            rate_limited = (
                recent.filter(email_digest=email_digest).count()
                >= PASSWORD_RESET_MAX_EMAIL_REQUESTS
                or recent.filter(ip_digest=ip_digest).count()
                >= PASSWORD_RESET_MAX_IP_REQUESTS
            )

            if not rate_limited:
                user = User.objects.filter(
                    email__iexact=email,
                    is_active=True,
                ).first()
                code = f'{secrets.randbelow(1_000_000):06d}'
                PasswordResetChallenge.objects.filter(
                    email_digest=email_digest,
                    is_used=False,
                ).update(is_used=True)
                challenge = PasswordResetChallenge.objects.create(
                    user=user,
                    email_digest=email_digest,
                    ip_digest=ip_digest,
                    code_hash=make_password(code),
                    expires_at=now + PASSWORD_RESET_TTL,
                )
                request.session['password_reset_challenge'] = str(challenge.pk)

                if user:
                    try:
                        sent = send_mail(
                            subject='Your GAB password reset code',
                            message=_password_reset_email(code),
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[user.email],
                            fail_silently=False,
                        )
                    except (OSError, smtplib.SMTPException):
                        logger.exception('Could not send a password reset email.')
                        challenge.is_used = True
                        challenge.save(update_fields=['is_used'])
                        request.session.pop('password_reset_challenge', None)
                    else:
                        if sent != 1:
                            logger.error('Password reset email backend did not send the message.')
                            challenge.is_used = True
                            challenge.save(update_fields=['is_used'])
                            request.session.pop('password_reset_challenge', None)
            else:
                request.session.pop('password_reset_challenge', None)
                challenge = None

            messages.info(
                request,
                'If an active account uses that email, a password reset code will be sent.',
            )
            return redirect('password_reset')

    elif request.method == 'POST' and request.POST.get('action') == 'verify':
        verify_form = PasswordResetVerifyForm(request.POST)
        if verify_form.is_valid():
            challenge_id = request.session.get('password_reset_challenge')
            error = None
            reset_complete = False

            with transaction.atomic():
                locked_challenge = (
                    PasswordResetChallenge.objects.select_for_update()
                    .filter(pk=challenge_id, is_used=False)
                    .first()
                    if challenge_id else None
                )
                if (
                    locked_challenge is None
                    or locked_challenge.expires_at <= now
                    or locked_challenge.attempts >= PASSWORD_RESET_MAX_ATTEMPTS
                ):
                    if locked_challenge:
                        locked_challenge.is_used = True
                        locked_challenge.save(update_fields=['is_used'])
                    error = 'That code is invalid or expired. Request a new code and try again.'
                else:
                    locked_challenge.attempts += 1
                    code_valid = check_password(
                        verify_form.cleaned_data['code'],
                        locked_challenge.code_hash,
                    )
                    if not code_valid:
                        if locked_challenge.attempts >= PASSWORD_RESET_MAX_ATTEMPTS:
                            locked_challenge.is_used = True
                        locked_challenge.save(update_fields=['attempts', 'is_used'])
                        error = (
                            'That code is incorrect. Try again.'
                            if locked_challenge.attempts < PASSWORD_RESET_MAX_ATTEMPTS
                            else 'That code is invalid or expired. Request a new code and try again.'
                        )
                    elif locked_challenge.user is None or not locked_challenge.user.is_active:
                        locked_challenge.is_used = True
                        locked_challenge.save(update_fields=['attempts', 'is_used'])
                        error = 'That code is invalid or expired. Request a new code and try again.'
                    else:
                        try:
                            validate_password(
                                verify_form.cleaned_data['new_password'],
                                user=locked_challenge.user,
                            )
                        except ValidationError as validation_error:
                            for message in validation_error.messages:
                                verify_form.add_error('new_password', message)
                            locked_challenge.attempts -= 1
                            locked_challenge.save(update_fields=['attempts'])
                        else:
                            locked_challenge.user.set_password(
                                verify_form.cleaned_data['new_password'],
                            )
                            locked_challenge.user.save(update_fields=['password'])
                            locked_challenge.is_used = True
                            locked_challenge.save(update_fields=['attempts', 'is_used'])
                            reset_complete = True

            if reset_complete:
                request.session.pop('password_reset_challenge', None)
                messages.success(request, 'Your password has been changed. You can now log in.')
                return redirect('login')
            if error:
                verify_form.add_error(None, error)
                if 'invalid or expired' in error:
                    request.session.pop('password_reset_challenge', None)
                    challenge = None

    return render(request, 'accounts/password_reset.html', {
        'request_form': request_form,
        'verify_form': verify_form,
        'show_verify_form': bool(challenge) or bool(verify_form.errors),
        'page_title': 'Reset Password - GAB',
    })


def register_view(request):
    if request.user.is_authenticated:
        return redirect(request.user.get_dashboard_url())

    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(
                request,
                f"Welcome to GAB Platform, {user.get_full_name() or user.username}!"
            )
            return redirect(user.get_dashboard_url())
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = UserRegistrationForm()

    return render(request, 'accounts/register.html', {
        'form': form,
        'page_title': 'Create Account - GAB Platform'
    })


def login_view(request):
    if request.user.is_authenticated:
        return redirect(request.user.get_dashboard_url())

    if request.method == 'POST':
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(
                request,
                f"Welcome back, {user.get_full_name() or user.username}!"
            )
            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            return redirect(user.get_dashboard_url())
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = UserLoginForm()

    return render(request, 'accounts/login.html', {
        'form': form,
        'page_title': 'Login - GAB Platform'
    })


@require_http_methods(["POST", "GET"])
def logout_view(request):
    if request.user.is_authenticated:
        logout(request)
        messages.info(request, "You have been logged out successfully.")
    return redirect('login')


@login_required
def profile_view(request):
    return render(request, 'accounts/profile.html', {
        'user_obj': request.user,
        'page_title': 'My Profile - GAB Platform'
    })


@login_required
def account_settings_view(request):
    add_role_form = AddAccountRoleForm(user=request.user)
    switch_role_form = SwitchAccountRoleForm(user=request.user)
    location_form = AccountLocationForm(instance=request.user)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update-location':
            location_form = AccountLocationForm(request.POST, instance=request.user)
            if location_form.is_valid():
                location_form.save()
                messages.success(request, 'Your PIN code was updated.')
                return redirect('account_settings')
        elif action == 'add-role':
            add_role_form = AddAccountRoleForm(request.POST, user=request.user)
            if add_role_form.is_valid():
                role = add_role_form.cleaned_data['role']
                _assignment, created = UserRole.objects.get_or_create(
                    user=request.user,
                    role=role,
                )
                if created:
                    business_name = add_role_form.cleaned_data['business_name'].strip()
                    if business_name and not request.user.business_name:
                        request.user.business_name = business_name
                        request.user.save(update_fields=['business_name', 'updated_at'])
                    messages.success(
                        request,
                        f'{dict(User.Role.choices)[role]} was added to your account.',
                    )
                else:
                    messages.info(request, 'That role is already on your account.')
                return redirect('account_settings')
        elif action == 'switch-role':
            switch_role_form = SwitchAccountRoleForm(request.POST, user=request.user)
            if switch_role_form.is_valid():
                request.user.role = switch_role_form.cleaned_data['role']
                request.user.save(update_fields=['role', 'updated_at'])
                messages.success(
                    request,
                    f'Switched to {request.user.get_role_display()}.',
                )
                return redirect(request.user.get_dashboard_url())
        else:
            messages.error(request, 'Choose a valid account settings action.')

    available_roles = request.user.get_available_roles()
    role_labels = dict(User.Role.choices)
    return render(request, 'accounts/account_settings.html', {
        'add_role_form': add_role_form,
        'switch_role_form': switch_role_form,
        'location_form': location_form,
        'available_roles': [
            (role, role_labels[role])
            for role in available_roles
        ],
        'has_roles_to_add': len(available_roles) < len(User.Role.choices),
        'page_title': 'Account Settings - GAB',
    })


@login_required
def industry_dashboard(request):
    if not request.user.is_industry:
        messages.warning(request, "Access denied. This dashboard is only for Industry users.")
        return redirect(request.user.get_dashboard_url())
    return render(request, 'accounts/dashboards/industry.html', {
        'page_title': 'Industry Dashboard - GAB',
        'active_products': Product.objects.filter(owner=request.user, is_active=True).count(),
        'incoming_quotes': QuoteRequest.objects.filter(
            seller=request.user,
            status=QuoteRequest.Status.PENDING,
        ).count(),
        'active_banners': SellerBanner.objects.filter(
            owner=request.user,
            is_active=True,
        ).count(),
    })


@login_required
def wholesaler_dashboard(request):
    if not request.user.is_wholesaler:
        messages.warning(request, "Access denied. This dashboard is only for Wholesaler users.")
        return redirect(request.user.get_dashboard_url())
    return render(request, 'accounts/dashboards/wholesaler.html', {
        'page_title': 'Wholesaler Dashboard - GAB',
        'active_products': Product.objects.filter(owner=request.user, is_active=True).count(),
        'incoming_orders': Order.objects.filter(
            seller=request.user,
            status=Order.Status.PENDING,
        ).count(),
        'incoming_quotes': QuoteRequest.objects.filter(
            seller=request.user,
            status=QuoteRequest.Status.PENDING,
        ).count(),
        'active_banners': SellerBanner.objects.filter(
            owner=request.user,
            is_active=True,
        ).count(),
    })


@login_required
def shopkeeper_dashboard(request):
    if not request.user.is_shopkeeper:
        messages.warning(request, "Access denied. This dashboard is only for Shopkeeper users.")
        return redirect(request.user.get_dashboard_url())
    return render(request, 'accounts/dashboards/shopkeeper.html', {
        'page_title': 'Shopkeeper Dashboard - GAB',
        'active_products': Product.objects.filter(owner=request.user, is_active=True).count(),
        'incoming_orders': Order.objects.filter(
            seller=request.user,
            status=Order.Status.PENDING,
        ).count(),
        'incoming_quotes': QuoteRequest.objects.filter(
            seller=request.user,
            status=QuoteRequest.Status.PENDING,
        ).count(),
        'active_banners': SellerBanner.objects.filter(
            owner=request.user,
            is_active=True,
        ).count(),
    })


@login_required
def buyer_dashboard(request):
    if not request.user.is_buyer:
        messages.warning(request, "Access denied. This dashboard is only for Buyer users.")
        return redirect(request.user.get_dashboard_url())
    return render(request, 'accounts/dashboards/buyer.html', {
        'page_title': 'Buyer Dashboard - GAB',
        'quote_requests': QuoteRequest.objects.filter(
            buyer=request.user,
        ).select_related('product', 'seller')[:5],
        'quote_request_count': QuoteRequest.objects.filter(buyer=request.user).count(),
    })