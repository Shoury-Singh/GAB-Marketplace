import uuid

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _


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
    pincode = models.CharField(max_length=10, blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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