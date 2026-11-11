from decimal import Decimal
import uuid

from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.text import slugify


class GalleryImage(models.Model):
    """
    Images uploaded by business users (Industry, Wholesaler, Shopkeeper)
    to showcase products, factory, shop, or business.
    Displayed on the public Images Show page.
    """
    title = models.CharField(
        max_length=200,
        help_text=_("Short title for the image")
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text=_("Optional description")
    )
    image = models.ImageField(
        upload_to='gallery/%Y/%m/',
        help_text=_("Upload image (JPG, PNG, WEBP recommended)")
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='gallery_images'
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Only active images are shown on the public page")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('Gallery Image')
        verbose_name_plural = _('Gallery Images')

    def __str__(self):
        return f"{self.title} by {self.uploaded_by.business_name or self.uploaded_by.username}"


class Product(models.Model):
    class SellerRole(models.TextChoices):
        INDUSTRY = 'INDUSTRY', _('Industry / Factory / Company')
        WHOLESALER = 'WHOLESALER', _('Wholesaler')
        SHOPKEEPER = 'SHOPKEEPER', _('Shopkeeper')

    class Category(models.TextChoices):
        FABRICS = 'Fabrics', _('Fabrics')
        MACHINERY = 'Machinery', _('Machinery')
        PACKAGING = 'Packaging', _('Packaging')
        CHEMICALS = 'Chemicals', _('Chemicals')
        FOOD = 'Food', _('Food & Beverages')
        ELECTRONICS = 'Electronics', _('Electronics')

    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='products',
        null=True,
        blank=True,
    )
    seller_role = models.CharField(
        max_length=20,
        choices=SellerRole.choices,
        blank=True,
        null=True,
        editable=False,
    )
    image = models.ImageField(upload_to='products/%Y/%m/', blank=True)
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.FABRICS)
    supplier = models.CharField(max_length=120, default='GAB Verified Supplier')
    location = models.CharField(max_length=120, default='India')
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    old_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    minimum_order = models.PositiveIntegerField(default=1)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=4.5)
    review_count = models.PositiveIntegerField(default=0)
    inventory = models.PositiveIntegerField(default=0)
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    accent = models.CharField(max_length=30, default='#0d6efd')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_featured', '-created_at']
        verbose_name = _('Product')
        verbose_name_plural = _('Products')

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.seller_role and self.owner_id:
            self.seller_role = self.owner.role
        if not self.slug:
            self.slug = slugify(self.name)
            base_slug = self.slug
            counter = 1
            while Product.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f'{base_slug}-{counter}'
                counter += 1
        super().save(*args, **kwargs)

    @property
    def stock_status(self):
        if self.inventory <= 0:
            return 'Out of stock'
        if self.inventory < 25:
            return 'Low stock'
        return 'In stock'

    @property
    def discount_percent(self):
        if not self.old_price or self.old_price <= self.price:
            return 0
        return int(((self.old_price - self.price) / self.old_price) * 100)


class ProductInteraction(models.Model):
    class EventType(models.TextChoices):
        PRODUCT_VIEW = 'PRODUCT_VIEW', _('Product view')
        SEARCH = 'SEARCH', _('Search')
        CLICK = 'CLICK', _('Recommendation click')
        QUOTE_REQUEST = 'QUOTE_REQUEST', _('Quote request')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='product_interactions',
        null=True,
        blank=True,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        related_name='interactions',
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    category = models.CharField(max_length=30, blank=True)
    search_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'event_type', 'created_at']),
        ]

    def __str__(self):
        return f'{self.get_event_type_display()} for {self.product or self.category}'


class QuoteRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        QUOTED = 'QUOTED', _('Quoted')
        ACCEPTED = 'ACCEPTED', _('Accepted')
        DECLINED = 'DECLINED', _('Declined')

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='quote_requests',
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quote_requests',
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='received_quote_requests',
    )
    quantity = models.PositiveIntegerField()
    message = models.TextField(max_length=1000, blank=True)
    seller_response = models.TextField(max_length=1000, blank=True)
    quoted_unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_('Seller price per unit; required when sending a quote.'),
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Quote request for {self.product} from {self.buyer}'


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        ACCEPTED = 'ACCEPTED', _('Accepted')
        PROCESSING = 'PROCESSING', _('Processing')
        SHIPPED = 'SHIPPED', _('Shipped')
        DELIVERED = 'DELIVERED', _('Delivered')
        DECLINED = 'DECLINED', _('Declined')
        CANCELLED = 'CANCELLED', _('Cancelled')

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='orders',
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='buyer_orders',
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='seller_orders',
    )
    source_quote = models.OneToOneField(
        QuoteRequest,
        on_delete=models.PROTECT,
        related_name='converted_order',
        null=True,
        blank=True,
    )
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    total_amount = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        null=True,
        blank=True,
    )
    product_snapshot = models.JSONField(default=dict, editable=False)
    notes = models.TextField(max_length=1000, blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    payment_status = models.CharField(
        max_length=24,
        choices=[
            ('UNPAID', _('Unpaid')),
            ('PENDING', _('Payment pending')),
            ('PAID', _('Paid')),
            ('FAILED', _('Payment failed')),
            ('REFUND_PENDING', _('Refund pending')),
            ('REFUNDED', _('Refunded')),
            ('OFFLINE_REPORTED', _('Seller reported paid offline')),
        ],
        default='UNPAID',
        db_index=True,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-created_at']

    def save(self, *args, **kwargs):
        if self._state.adding:
            if not self.product_id:
                raise ValueError('An order must reference a product.')
            product = self.product
            if not self.product_snapshot:
                self.product_snapshot = {
                    'captured_at': timezone.now().isoformat(),
                    'name': product.name,
                    'category': product.category,
                    'supplier': product.supplier,
                    'location': product.location,
                    'description': product.description,
                    'image': product.image.name if product.image else '',
                }
            product_price = Decimal(str(product.price))
            if self.unit_price is None:
                self.unit_price = product_price
            else:
                self.unit_price = Decimal(str(self.unit_price))
            self.total_amount = self.unit_price * self.quantity
        super().save(*args, **kwargs)

    @property
    def product_name(self):
        return self.product_snapshot.get('name') or self.product.name

    @property
    def product_supplier(self):
        return self.product_snapshot.get('supplier') or self.product.supplier

    def __str__(self):
        return f'Order #{self.pk} for {self.product} from {self.buyer}'


class OrderMessage(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='order_messages',
    )
    body = models.CharField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']

    def __str__(self):
        return f'Message on order #{self.order_id} from {self.sender}'


class SellerBanner(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='seller_banners',
    )
    seller_role = models.CharField(
        max_length=20,
        choices=Product.SellerRole.choices,
        blank=True,
        null=True,
        editable=False,
    )
    title = models.CharField(max_length=120)
    image = models.ImageField(upload_to='seller-banners/%Y/%m/')
    target_url = models.URLField(max_length=500)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('Seller banner')
        verbose_name_plural = _('Seller banners')

    def __str__(self):
        return f'{self.title} by {self.owner}'

    def save(self, *args, **kwargs):
        if not self.seller_role and self.owner_id:
            self.seller_role = self.owner.role
        super().save(*args, **kwargs)


def get_random_image_upload_path(instance, filename):
    """
    Generate a random upload path for images to avoid name collisions.
    """
    import uuid
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return f'gallery/{filename}'


class CommissionPlan(models.Model):
    """Commission settings are snapshotted on each ledger entry; 100 bps is 1%."""

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    commission_rate_bps = models.PositiveSmallIntegerField(default=100)
    # Unset until GAB's CA confirms the applicable treatment.
    gst_rate_bps = models.PositiveSmallIntegerField(null=True, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['is_default'],
                condition=models.Q(is_default=True),
                name='one_default_commission_plan',
            ),
        ]

    @property
    def commission_rate_percent(self):
        return self.commission_rate_bps / 100

    def __str__(self):
        return f'{self.name} ({self.commission_rate_bps / 100:.2f}%)'


class SellerPayoutProfile(models.Model):
    class Provider(models.TextChoices):
        NOT_CONFIGURED = 'NOT_CONFIGURED', _('Provider not configured')
        RAZORPAY_ROUTE = 'RAZORPAY_ROUTE', _('Razorpay Route')
        CASHFREE_EASY_SPLIT = 'CASHFREE_EASY_SPLIT', _('Cashfree Easy Split')

    class OnboardingStatus(models.TextChoices):
        NOT_STARTED = 'NOT_STARTED', _('Not started')
        IN_PROGRESS = 'IN_PROGRESS', _('In progress')
        REQUIRES_INFORMATION = 'REQUIRES_INFORMATION', _('More information required')
        ACTIVE = 'ACTIVE', _('Verified and active')
        REJECTED = 'REJECTED', _('Needs provider support')

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='payout_profile',
    )
    provider = models.CharField(
        max_length=24,
        choices=Provider.choices,
        default=Provider.NOT_CONFIGURED,
    )
    provider_account_id = models.CharField(max_length=120, blank=True)
    onboarding_status = models.CharField(
        max_length=24,
        choices=OnboardingStatus.choices,
        default=OnboardingStatus.NOT_STARTED,
    )
    provider_onboarding_url = models.URLField(max_length=500, blank=True)
    commission_plan = models.ForeignKey(
        CommissionPlan,
        on_delete=models.PROTECT,
        related_name='seller_profiles',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Payout profile for {self.user}'


class PaymentAttempt(models.Model):
    class Provider(models.TextChoices):
        RAZORPAY_ROUTE = 'RAZORPAY_ROUTE', _('Razorpay Route')
        CASHFREE_EASY_SPLIT = 'CASHFREE_EASY_SPLIT', _('Cashfree Easy Split')
        OFFLINE = 'OFFLINE', _('Offline / seller reported')

    class Status(models.TextChoices):
        CREATED = 'CREATED', _('Created')
        PENDING = 'PENDING', _('Pending')
        SUCCEEDED = 'SUCCEEDED', _('Succeeded')
        FAILED = 'FAILED', _('Failed')
        CANCELLED = 'CANCELLED', _('Cancelled')
        REFUNDED = 'REFUNDED', _('Refunded')

    class OfflineMethod(models.TextChoices):
        BANK_TRANSFER = 'BANK_TRANSFER', _('Bank transfer')
        CREDIT = 'CREDIT', _('Business credit')
        CASH = 'CASH', _('Cash')
        UPI = 'UPI', _('UPI paid outside GAB')
        OTHER = 'OTHER', _('Other')

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='payment_attempts')
    provider = models.CharField(max_length=24, choices=Provider.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.CREATED, db_index=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    amount_paise = models.PositiveBigIntegerField()
    commission_plan = models.ForeignKey(
        CommissionPlan,
        on_delete=models.PROTECT,
        related_name='payment_attempts',
        null=True,
        blank=True,
    )
    commission_rate_bps = models.PositiveSmallIntegerField(null=True, blank=True)
    commission_amount_paise = models.PositiveBigIntegerField(null=True, blank=True)
    gst_rate_bps = models.PositiveSmallIntegerField(null=True, blank=True)
    gst_amount_paise = models.PositiveBigIntegerField(null=True, blank=True)
    seller_amount_paise = models.PositiveBigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default='INR')
    provider_order_id = models.CharField(max_length=120, blank=True)
    provider_payment_id = models.CharField(max_length=120, blank=True)
    offline_method = models.CharField(max_length=20, choices=OfflineMethod.choices, blank=True)
    offline_reference = models.CharField(max_length=120, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='recorded_payment_attempts',
        null=True,
        blank=True,
    )
    failure_code = models.CharField(max_length=80, blank=True)
    failure_message = models.CharField(max_length=240, blank=True)
    reconciliation_note = models.CharField(
        max_length=500,
        blank=True,
        help_text='Required when staff resolves an uncertain provider attempt.',
    )
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='reconciled_payment_attempts',
        null=True,
        blank=True,
    )
    reconciled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['order', 'status'])]
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'provider_order_id'],
                condition=~models.Q(provider_order_id=''),
                name='unique_provider_order_ref',
            ),
            models.UniqueConstraint(
                fields=['provider', 'provider_payment_id'],
                condition=~models.Q(provider_payment_id=''),
                name='unique_provider_payment_ref',
            ),
        ]

    def __str__(self):
        return f'Payment attempt {self.pk} for order #{self.order_id}'


class PaymentWebhookEvent(models.Model):
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', _('Received')
        PROCESSED = 'PROCESSED', _('Processed')
        FAILED = 'FAILED', _('Failed')
        IGNORED = 'IGNORED', _('Ignored')

    provider = models.CharField(max_length=24, choices=PaymentAttempt.Provider.choices)
    event_id = models.CharField(max_length=160)
    event_type = models.CharField(max_length=100)
    payload_hash = models.CharField(max_length=64, blank=True)
    safe_payload = models.JSONField(default=dict, blank=True)
    signature_verified = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    payment_attempt = models.ForeignKey(
        PaymentAttempt,
        on_delete=models.SET_NULL,
        related_name='webhook_events',
        null=True,
        blank=True,
    )
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_summary = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ['-received_at']
        constraints = [
            models.UniqueConstraint(fields=['provider', 'event_id'], name='unique_provider_webhook_event'),
        ]

    def __str__(self):
        return f'{self.provider} {self.event_type} ({self.event_id})'


class CommissionStatement(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        ISSUED = 'ISSUED', _('Issued')
        PAID = 'PAID', _('Paid')
        VOID = 'VOID', _('Void')

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='commission_statements',
    )
    statement_number = models.CharField(max_length=80, unique=True)
    period_start = models.DateField()
    commission_total_paise = models.PositiveBigIntegerField(default=0)
    gst_total_paise = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-period_start', 'seller_id']
        constraints = [
            models.UniqueConstraint(fields=['seller', 'period_start'], name='unique_seller_commission_period'),
        ]

    @property
    def commission_total_rupees(self):
        return Decimal(self.commission_total_paise) / Decimal(100)

    def __str__(self):
        return f'{self.statement_number} for {self.seller}'


class CommissionLedger(models.Model):
    class Status(models.TextChoices):
        ACCRUED = 'ACCRUED', _('Accrued')
        INVOICED = 'INVOICED', _('Included in statement')
        PAID = 'PAID', _('Paid')
        REVERSED = 'REVERSED', _('Reversed')

    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name='commission_ledger')
    payment_attempt = models.OneToOneField(
        PaymentAttempt,
        on_delete=models.PROTECT,
        related_name='commission_ledger',
        null=True,
        blank=True,
    )
    commission_plan = models.ForeignKey(CommissionPlan, on_delete=models.PROTECT, related_name='ledger_entries')
    statement = models.ForeignKey(
        CommissionStatement,
        on_delete=models.PROTECT,
        related_name='ledger_entries',
        null=True,
        blank=True,
    )
    basis_amount_paise = models.PositiveBigIntegerField()
    commission_rate_bps = models.PositiveSmallIntegerField()
    commission_amount_paise = models.PositiveBigIntegerField()
    gst_rate_bps = models.PositiveSmallIntegerField(null=True, blank=True)
    gst_amount_paise = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACCRUED, db_index=True)
    reversed_commission_paise = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Commission for order #{self.order_id}'
