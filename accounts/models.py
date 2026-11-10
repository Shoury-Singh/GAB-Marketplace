import uuid

from django.db import models
from django.db import transaction
from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

indian_pincode_validator = RegexValidator(
    regex=r'^[1-9][0-9]{5}$',
    message=_('Enter a valid six-digit Indian PIN code.'),
)


class User(AbstractUser):
    class Role(models.TextChoices):
        INDUSTRY = 'INDUSTRY', _('Industry / Factory / Company')
        WHOLESALER = 'WHOLESALER', _('Wholesaler')
        SHOPKEEPER = 'SHOPKEEPER', _('Shopkeeper')
        BUYER = 'BUYER', _('Buyer / Customer')

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.BUYER,
        help_text=_("Select your role in the business network")
    )
    phone = models.CharField(max_length=15, blank=True, null=True)
    business_name = models.CharField(max_length=255, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    pincode = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        validators=[indian_pincode_validator],
        help_text=_('Six-digit Indian PIN code used for nearby shop search.'),
    )
    is_verified = models.BooleanField(default=False)
    shop_is_open = models.BooleanField(default=False)
    shop_status_changed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        UserRole.objects.get_or_create(user_id=self.pk, role=self.role)

    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')
        ordering = ['-date_joined']

    def __str__(self):
        if self.business_name:
            return f"{self.business_name} ({self.get_role_display()})"
        return f"{self.username} ({self.get_role_display()})"

    @property
    def is_industry(self):
        return self.role == self.Role.INDUSTRY

    @property
    def is_wholesaler(self):
        return self.role == self.Role.WHOLESALER

    @property
    def is_shopkeeper(self):
        return self.role == self.Role.SHOPKEEPER

    @property
    def is_buyer(self):
        return self.role == self.Role.BUYER

    def get_dashboard_url(self):
        role_urls = {
            self.Role.INDUSTRY: 'industry_dashboard',
            self.Role.WHOLESALER: 'wholesaler_dashboard',
            self.Role.SHOPKEEPER: 'shopkeeper_dashboard',
            self.Role.BUYER: 'buyer_dashboard',
        }
        return role_urls.get(self.role, 'home')

    def get_available_roles(self):
        assigned_roles = set(self.role_assignments.values_list('role', flat=True))
        assigned_roles.add(self.role)
        return [
            role
            for role, _label in self.Role.choices
            if role in assigned_roles
        ]


class UserRole(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='role_assignments',
    )
    role = models.CharField(max_length=20, choices=User.Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['user', 'role'], name='unique_user_role'),
        ]

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            from .data_saver import synchronize_role_account_data
            synchronize_role_account_data(self.user_id)

    def delete(self, *args, **kwargs):
        user_id = self.user_id
        with transaction.atomic():
            result = super().delete(*args, **kwargs)
            from .data_saver import synchronize_role_account_data
            synchronize_role_account_data(user_id)
        return result

    def __str__(self):
        return f'{self.user.username}: {self.get_role_display()}'


class RoleAccountData(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='%(app_label)s_%(class)s',
    )
    account_created_at = models.DateTimeField()
    role_added_at = models.DateTimeField()

    class Meta:
        abstract = True


class BuyerAccountData(RoleAccountData):
    class Meta:
        db_table = 'accounts_buyer_account_data'
        verbose_name = _('Buyer account data')
        verbose_name_plural = _('Buyer account data')


class WholesalerAccountData(RoleAccountData):
    class Meta:
        db_table = 'accounts_wholesaler_account_data'
        verbose_name = _('Wholesaler account data')
        verbose_name_plural = _('Wholesaler account data')


class ShopkeeperAccountData(RoleAccountData):
    class Meta:
        db_table = 'accounts_shopkeeper_account_data'
        verbose_name = _('Shopkeeper account data')
        verbose_name_plural = _('Shopkeeper account data')


class IndustryAccountData(RoleAccountData):
    class Meta:
        db_table = 'accounts_industry_account_data'
        verbose_name = _('Industry account data')
        verbose_name_plural = _('Industry account data')


class PincodeLocation(models.Model):
    pincode = models.CharField(
        max_length=6,
        unique=True,
        validators=[indian_pincode_validator],
    )
    place_name = models.CharField(max_length=180, blank=True)
    state = models.CharField(max_length=100, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pincode']
        verbose_name = _('PIN code location')
        verbose_name_plural = _('PIN code locations')

    def __str__(self):
        location = ', '.join(part for part in (self.place_name, self.state) if part)
        return f'{self.pincode} - {location}' if location else self.pincode


class PasswordResetChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='password_reset_challenges',
        null=True,
        blank=True,
    )
    email_digest = models.CharField(max_length=64, db_index=True)
    ip_digest = models.CharField(max_length=64, db_index=True)
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email_digest', 'created_at']),
            models.Index(fields=['ip_digest', 'created_at']),
        ]