from django.db import models
from django.conf import settings
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


class QuoteRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        QUOTED = 'QUOTED', _('Quoted')
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
    quantity = models.PositiveIntegerField()
    notes = models.TextField(max_length=1000, blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-created_at']

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
