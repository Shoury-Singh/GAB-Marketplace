from io import BytesIO
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from gallery.models import GalleryImage, Product, QuoteRequest


User = get_user_model()


class MarketplaceFlowTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

        self.seller = User.objects.create_user(
            username='seller',
            password='test-password-123',
            role=User.Role.INDUSTRY,
            business_name='Seller Works',
            city='Surat',
            state='Gujarat',
        )
        self.buyer = User.objects.create_user(
            username='buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        self.product = Product.objects.create(
            owner=self.seller,
            name='Cotton Fabric',
            category=Product.Category.FABRICS,
            supplier='Seller Works',
            location='Surat, Gujarat',
            price='250.00',
            minimum_order=5,
            inventory=100,
        )

    def test_seller_can_publish_product_and_catalog_displays_it(self):
        self.client.force_login(self.seller)
        response = self.client.post(reverse('create_product'), {
            'name': 'Linen Fabric',
            'category': Product.Category.FABRICS,
            'description': 'Natural linen',
            'price': '300.00',
            'minimum_order': '10',
            'inventory': '80',
        })

        self.assertRedirects(response, reverse('my_products'))
        product = Product.objects.get(name='Linen Fabric')
        self.assertEqual(product.owner, self.seller)
        self.assertEqual(product.supplier, 'Seller Works')
        self.assertEqual(product.location, 'Surat, Gujarat')
        catalog_response = self.client.get(reverse('catalog'))
        self.assertContains(catalog_response, 'Linen Fabric')
        self.assertContains(
            self.client.get(reverse('my_products')),
            'Linen Fabric',
        )
        self.assertContains(
            self.client.get(reverse('product_detail', args=[product.slug])),
            '10 units',
        )

    def test_seller_can_upload_product_photo_and_listing_displays_it(self):
        image_buffer = BytesIO()
        Image.new('RGB', (20, 20), color='blue').save(image_buffer, format='PNG')
        self.client.force_login(self.seller)

        response = self.client.post(reverse('create_product'), {
            'name': 'Linen Fabric',
            'category': Product.Category.FABRICS,
            'description': 'Natural linen',
            'price': '300.00',
            'minimum_order': '10',
            'inventory': '80',
            'image': SimpleUploadedFile(
                'linen.png',
                image_buffer.getvalue(),
                content_type='image/png',
            ),
        })

        self.assertRedirects(response, reverse('my_products'))
        product = Product.objects.get(name='Linen Fabric')
        self.assertTrue(product.image.name.startswith('products/'))
        self.assertContains(self.client.get(reverse('catalog')), product.image.url)
        self.assertContains(
            self.client.get(reverse('product_detail', args=[product.slug])),
            product.image.url,
        )

    def test_uploaded_media_is_served_as_an_image_and_rejects_other_paths(self):
        image_buffer = BytesIO()
        Image.new('RGB', (12, 12), color='red').save(image_buffer, format='PNG')
        self.client.force_login(self.seller)
        self.client.post(reverse('create_product'), {
            'name': 'Photo test fabric',
            'category': Product.Category.FABRICS,
            'description': '',
            'price': '25.00',
            'minimum_order': '1',
            'inventory': '5',
            'image': SimpleUploadedFile(
                'fabric.png',
                image_buffer.getvalue(),
                content_type='image/png',
            ),
        })
        product = Product.objects.get(name='Photo test fabric')

        response = self.client.get(product.image.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        response.close()
        self.assertEqual(
            self.client.get('/media/../../manage.py').status_code,
            404,
        )
        self.assertEqual(
            self.client.get('/media/not-an-image.txt').status_code,
            404,
        )

    def test_buyer_can_request_quote_and_seller_can_respond(self):
        self.client.force_login(self.buyer)
        response = self.client.post(
            reverse('request_quote', args=[self.product.slug]),
            {'quantity': '8', 'message': 'Please share bulk pricing.'},
        )

        self.assertRedirects(response, reverse('buyer_quotes'))
        quote = QuoteRequest.objects.get()
        self.assertEqual(quote.buyer, self.buyer)
        self.assertEqual(quote.seller, self.seller)
        self.assertEqual(quote.status, QuoteRequest.Status.PENDING)

        self.client.force_login(self.seller)
        self.assertContains(
            self.client.get(reverse('seller_quotes')),
            'Please share bulk pricing.',
        )
        response = self.client.post(
            reverse('respond_to_quote', args=[quote.pk]),
            {'status': QuoteRequest.Status.QUOTED, 'seller_response': '₹225 per unit.'},
        )
        self.assertRedirects(response, reverse('seller_quotes'))
        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteRequest.Status.QUOTED)
        self.assertEqual(quote.seller_response, '₹225 per unit.')

        self.client.force_login(self.buyer)
        self.assertContains(self.client.get(reverse('buyer_quotes')), '₹225 per unit.')

    def test_quote_quantity_must_meet_product_minimum(self):
        self.client.force_login(self.buyer)
        response = self.client.post(
            reverse('request_quote', args=[self.product.slug]),
            {'quantity': '4', 'message': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(QuoteRequest.objects.exists())
        self.assertContains(response, 'minimum order for this product is 5')

    def test_seller_cannot_edit_another_sellers_product(self):
        other_seller = User.objects.create_user(
            username='other-seller',
            password='test-password-123',
            role=User.Role.WHOLESALER,
        )
        self.client.force_login(other_seller)

        response = self.client.get(reverse('edit_product', args=[self.product.pk]))

        self.assertEqual(response.status_code, 404)

    def test_buyer_cannot_create_products(self):
        self.client.force_login(self.buyer)

        response = self.client.post(reverse('create_product'), {
            'name': 'Unauthorized listing',
            'category': Product.Category.FABRICS,
            'price': '10.00',
            'old_price': '0.00',
            'minimum_order': '1',
            'inventory': '1',
        })

        self.assertRedirects(response, reverse('buyer_dashboard'))
        self.assertFalse(Product.objects.filter(name='Unauthorized listing').exists())

    def test_gallery_image_deletion_requires_post_and_owner(self):
        image = GalleryImage.objects.create(
            title='Factory floor',
            image='gallery/factory.jpg',
            uploaded_by=self.seller,
        )
        self.client.force_login(self.seller)

        response = self.client.get(reverse('delete_image', args=[image.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(GalleryImage.objects.filter(pk=image.pk).exists())

        response = self.client.post(reverse('delete_image', args=[image.pk]))
        self.assertRedirects(response, reverse('my_images'))
        self.assertFalse(GalleryImage.objects.filter(pk=image.pk).exists())

    def test_user_cannot_delete_another_users_gallery_image(self):
        image = GalleryImage.objects.create(
            title='Factory floor',
            image='gallery/factory.jpg',
            uploaded_by=self.seller,
        )
        self.client.force_login(self.buyer)

        response = self.client.post(reverse('delete_image', args=[image.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(GalleryImage.objects.filter(pk=image.pk).exists())

    def test_seller_can_create_and_publish_banner(self):
        image_buffer = BytesIO()
        Image.new('RGB', (40, 20), color='green').save(image_buffer, format='PNG')
        self.client.force_login(self.seller)

        response = self.client.post(reverse('create_banner'), {
            'title': 'New cotton collection',
            'target_url': 'https://seller.example/collection',
            'image': SimpleUploadedFile(
                'banner.png',
                image_buffer.getvalue(),
                content_type='image/png',
            ),
        })

        self.assertRedirects(response, reverse('my_banners'))
        banner = self.seller.seller_banners.get()
        self.assertTrue(banner.is_active)
        self.assertContains(self.client.get(reverse('my_banners')), banner.title)
        self.client.logout()
        self.assertContains(self.client.get(reverse('home')), banner.title)
        self.assertContains(self.client.get(reverse('catalog')), banner.image.url)

    def test_buyer_cannot_create_or_manage_seller_banners(self):
        self.client.force_login(self.buyer)

        response = self.client.get(reverse('create_banner'))

        self.assertRedirects(response, reverse('buyer_dashboard'))
        self.assertFalse(self.buyer.seller_banners.exists())

    def test_seller_can_pause_banner_and_cannot_change_another_sellers_banner(self):
        banner = self.seller.seller_banners.create(
            title='Factory offer',
            image='seller-banners/factory.png',
            target_url='https://seller.example/offer',
        )
        self.client.force_login(self.seller)

        response = self.client.get(reverse('toggle_banner', args=[banner.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(banner.is_active)

        response = self.client.post(reverse('toggle_banner', args=[banner.pk]))
        self.assertRedirects(response, reverse('my_banners'))
        banner.refresh_from_db()
        self.assertFalse(banner.is_active)

        other_seller = User.objects.create_user(
            username='other-seller',
            password='test-password-123',
            role=User.Role.WHOLESALER,
        )
        self.client.force_login(other_seller)
        response = self.client.get(reverse('edit_banner', args=[banner.pk]))
        self.assertEqual(response.status_code, 404)
