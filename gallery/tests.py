from io import BytesIO
from decimal import Decimal
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import PincodeLocation
from gallery.models import (
    GalleryImage,
    Order,
    OrderMessage,
    Product,
    ProductInteraction,
    QuoteRequest,
    SellerBanner,
)
from gallery.recommendations import hash_search_query


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
            role=User.Role.SHOPKEEPER,
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
        self.client.force_login(self.buyer)
        catalog_response = self.client.get(reverse('catalog'))
        self.assertContains(catalog_response, 'Linen Fabric')
        self.client.force_login(self.seller)
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
        self.client.force_login(self.buyer)
        self.assertContains(self.client.get(reverse('catalog')), product.image.url)
        self.assertContains(
            self.client.get(reverse('product_detail', args=[product.slug])),
            product.image.url,
        )

    def test_seller_catalog_shows_product_history_and_ordered_demand(self):
        second_buyer = User.objects.create_user(
            username='second-buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        past_product = Product.objects.create(
            owner=self.seller,
            name='Past Packaging',
            category=Product.Category.PACKAGING,
            is_active=False,
        )
        Order.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=5,
            status=Order.Status.ACCEPTED,
        )
        Order.objects.create(
            product=self.product,
            buyer=second_buyer,
            seller=self.seller,
            quantity=3,
            status=Order.Status.DELIVERED,
        )
        Order.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=100,
            status=Order.Status.PENDING,
        )
        Order.objects.create(
            product=past_product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=4,
            status=Order.Status.ACCEPTED,
        )

        self.client.force_login(self.seller)
        response = self.client.get(reverse('my_products'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Past Packaging')
        self.assertContains(response, 'Hidden')
        self.assertEqual(response.context['buyer_count'], 2)
        self.assertEqual(response.context['units_sold'], 12)
        products = {
            product.pk: product
            for product in response.context['products']
        }
        self.assertEqual(products[self.product.pk].buyer_count, 2)
        self.assertEqual(products[self.product.pk].order_count, 2)
        self.assertEqual(products[self.product.pk].units_sold, 8)
        self.assertEqual(products[past_product.pk].units_sold, 4)
        self.assertEqual(
            {
                item['category']: item['units_sold']
                for item in response.context['demand_categories']
            },
            {
                Product.Category.FABRICS: 8,
                Product.Category.PACKAGING: 4,
            },
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
            {
                'status': QuoteRequest.Status.QUOTED,
                'quoted_unit_price': '225.00',
                'seller_response': '₹225 per unit.',
            },
        )
        self.assertRedirects(response, reverse('seller_quotes'))
        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteRequest.Status.QUOTED)
        self.assertEqual(quote.seller_response, '₹225 per unit.')
        self.assertEqual(quote.quoted_unit_price, Decimal('225.00'))

        self.client.force_login(self.buyer)
        self.assertContains(self.client.get(reverse('buyer_quotes')), '₹225 per unit.')

    def test_seller_cannot_send_a_quote_without_a_unit_price(self):
        quote = QuoteRequest.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=8,
        )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse('respond_to_quote', args=[quote.pk]),
            {'status': QuoteRequest.Status.QUOTED, 'seller_response': 'Bulk price'},
        )

        self.assertRedirects(response, reverse('seller_quotes'))
        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteRequest.Status.PENDING)
        self.assertIsNone(quote.quoted_unit_price)

    def test_buyer_accepting_quote_creates_a_price_locked_order_once(self):
        quote = QuoteRequest.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=8,
            status=QuoteRequest.Status.QUOTED,
            quoted_unit_price=Decimal('225.00'),
        )
        self.client.force_login(self.buyer)

        response = self.client.post(reverse('accept_quote', args=[quote.pk]))

        order = Order.objects.get(source_quote=quote)
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.unit_price, Decimal('225.00'))
        self.assertEqual(order.total_amount, Decimal('1800.00'))
        self.assertEqual(order.product_name, 'Cotton Fabric')
        self.assertTrue(order.product_snapshot['captured_at'])
        self.assertEqual(quote.converted_order, order)
        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteRequest.Status.ACCEPTED)
        self.assertContains(
            self.client.get(reverse('buyer_quotes')),
            reverse('order_detail', args=[order.pk]),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 100)

        buyer_order_page = self.client.get(reverse('order_detail', args=[order.pk]))
        self.assertIsNone(buyer_order_page.context['status_form'])
        self.assertContains(buyer_order_page, 'The supplier manages this order’s status.')

        self.product.name = 'Renamed listing'
        self.product.price = Decimal('300.00')
        self.product.save(update_fields=['name', 'price', 'updated_at'])
        order.refresh_from_db()
        self.assertEqual(order.product_name, 'Cotton Fabric')
        self.assertEqual(order.unit_price, Decimal('225.00'))
        self.assertEqual(order.total_amount, Decimal('1800.00'))

        response = self.client.post(reverse('accept_quote', args=[quote.pk]))

        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        self.assertEqual(Order.objects.filter(source_quote=quote).count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 100)

    def test_quote_acceptance_rechecks_stock_and_keeps_quote_open_if_unavailable(self):
        quote = QuoteRequest.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=8,
            status=QuoteRequest.Status.QUOTED,
            quoted_unit_price=Decimal('225.00'),
        )
        self.product.inventory = 5
        self.product.save(update_fields=['inventory', 'updated_at'])
        self.client.force_login(self.buyer)

        response = self.client.post(reverse('accept_quote', args=[quote.pk]))

        self.assertRedirects(response, reverse('buyer_quotes'))
        self.assertFalse(Order.objects.filter(source_quote=quote).exists())
        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteRequest.Status.QUOTED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 5)

    def test_quote_quantity_must_meet_product_minimum(self):
        self.client.force_login(self.buyer)
        response = self.client.post(
            reverse('request_quote', args=[self.product.slug]),
            {'quantity': '4', 'message': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(QuoteRequest.objects.exists())
        self.assertContains(response, 'minimum order for this product is 5')

    def test_buyer_can_create_an_order_and_supplier_can_message_them(self):
        self.client.force_login(self.buyer)
        response = self.client.post(
            reverse('create_order', args=[self.product.slug]),
            {'quantity': '8', 'notes': 'Please use custom blue packaging.'},
        )

        order = Order.objects.get()
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        self.assertEqual(order.buyer, self.buyer)
        self.assertEqual(order.seller, self.seller)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.unit_price, Decimal('250.00'))
        self.assertEqual(order.total_amount, Decimal('2000.00'))
        self.assertEqual(order.product_snapshot['name'], 'Cotton Fabric')
        self.assertTrue(order.product_snapshot['captured_at'])
        buyer_orders = self.client.get(reverse('my_orders'))
        self.assertContains(buyer_orders, 'Cotton Fabric')
        self.assertContains(buyer_orders, 'Orders &amp; live chat')
        self.assertContains(
            self.client.get(reverse('order_detail', args=[order.pk])),
            'custom blue packaging',
        )

        self.client.force_login(self.seller)
        self.assertContains(self.client.get(reverse('my_orders')), 'Cotton Fabric')
        response = self.client.post(
            reverse('send_order_message', args=[order.pk]),
            {'body': 'We can customize the packaging.'},
        )
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        message = OrderMessage.objects.get()
        self.assertEqual(message.sender, self.seller)
        self.assertEqual(message.order, order)

        self.client.force_login(self.buyer)
        self.assertContains(
            self.client.get(reverse('order_detail', args=[order.pk])),
            'We can customize the packaging.',
        )

    def test_live_order_chat_feed_is_private_and_returns_only_new_messages(self):
        order = Order.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=8,
        )
        first_message = OrderMessage.objects.create(
            order=order,
            sender=self.seller,
            body='The fabric is ready for dispatch.',
        )
        feed_url = reverse('order_messages_feed', args=[order.pk])
        self.client.force_login(self.buyer)

        response = self.client.get(feed_url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload['messages']), 1)
        self.assertEqual(payload['messages'][0]['id'], first_message.pk)
        self.assertEqual(payload['messages'][0]['sender_name'], 'Seller Works')
        self.assertFalse(payload['messages'][0]['is_mine'])
        self.assertEqual(self.client.get(feed_url, {'after': first_message.pk}).json()['messages'], [])

        send_response = self.client.post(
            reverse('send_order_message', args=[order.pk]),
            {'body': 'Thanks, please share tracking when shipped.'},
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(send_response.status_code, 201)
        sent_payload = send_response.json()['message']
        self.assertEqual(sent_payload['sender_id'], self.buyer.pk)
        self.assertTrue(sent_payload['is_mine'])
        follow_up = self.client.get(feed_url, {'after': first_message.pk}).json()
        self.assertEqual(len(follow_up['messages']), 1)
        self.assertEqual(follow_up['messages'][0]['body'], 'Thanks, please share tracking when shipped.')

        outsider = User.objects.create_user(
            username='outsider',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(feed_url).status_code, 404)

    def test_live_order_chat_rejects_invalid_json_message(self):
        order = Order.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=8,
        )
        self.client.force_login(self.buyer)

        response = self.client.post(
            reverse('send_order_message', args=[order.pk]),
            {'body': ''},
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(OrderMessage.objects.filter(order=order).exists())

    def test_order_status_updates_follow_the_supplier_workflow(self):
        order = Order.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=8,
        )
        status_url = reverse('update_order_status', args=[order.pk])
        self.client.force_login(self.seller)

        response = self.client.post(status_url, {'status': Order.Status.SHIPPED})
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)

        for status in [
            Order.Status.ACCEPTED,
            Order.Status.PROCESSING,
            Order.Status.SHIPPED,
            Order.Status.DELIVERED,
        ]:
            response = self.client.post(status_url, {'status': status})
            self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
            order.refresh_from_db()
            self.assertEqual(order.status, status)
            if status == Order.Status.ACCEPTED:
                self.product.refresh_from_db()
                self.assertEqual(self.product.inventory, 92)

        response = self.client.post(status_url, {'status': Order.Status.CANCELLED})
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DELIVERED)

    def test_only_supplier_can_change_order_status_and_other_users_cannot_access_it(self):
        order = Order.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=8,
        )
        self.client.force_login(self.buyer)
        response = self.client.post(
            reverse('update_order_status', args=[order.pk]),
            {'status': Order.Status.CANCELLED},
        )
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)

        accepted_order = Order.objects.create(
            product=self.product,
            buyer=self.buyer,
            seller=self.seller,
            quantity=8,
            status=Order.Status.ACCEPTED,
        )
        self.product.inventory -= accepted_order.quantity
        self.product.save(update_fields=['inventory'])
        response = self.client.post(
            reverse('update_order_status', args=[accepted_order.pk]),
            {'status': Order.Status.CANCELLED},
        )
        self.assertRedirects(response, reverse('order_detail', args=[accepted_order.pk]))
        accepted_order.refresh_from_db()
        self.assertEqual(accepted_order.status, Order.Status.ACCEPTED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 92)

        self.client.force_login(self.seller)
        response = self.client.post(
            reverse('update_order_status', args=[accepted_order.pk]),
            {'status': Order.Status.CANCELLED},
        )
        self.assertRedirects(response, reverse('order_detail', args=[accepted_order.pk]))
        accepted_order.refresh_from_db()
        self.assertEqual(accepted_order.status, Order.Status.CANCELLED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 100)

        other_buyer = User.objects.create_user(
            username='other-buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        self.client.force_login(other_buyer)
        self.assertEqual(
            self.client.get(reverse('order_detail', args=[order.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse('send_order_message', args=[order.pk]),
                {'body': 'Unauthorized message'},
            ).status_code,
            404,
        )
        self.assertFalse(OrderMessage.objects.exists())

    def test_order_quantity_must_meet_minimum_and_available_inventory(self):
        self.client.force_login(self.buyer)
        order_url = reverse('create_order', args=[self.product.slug])
        response = self.client.post(order_url, {'quantity': '4', 'notes': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'minimum order for this product is 5')
        self.assertFalse(Order.objects.exists())

        response = self.client.post(order_url, {'quantity': '101', 'notes': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only 100 units are currently available')
        self.assertFalse(Order.objects.exists())

    def test_supplier_cannot_place_a_buyer_order(self):
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse('create_order', args=[self.product.slug]),
            {'quantity': '8', 'notes': ''},
        )

        self.assertRedirects(response, reverse('shopkeeper_dashboard'))
        self.assertFalse(Order.objects.exists())

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

    def test_gallery_and_uploads_are_private_to_shopkeepers_and_wholesalers(self):
        own_image = GalleryImage.objects.create(
            title='Shop display',
            image='gallery/shop-display.jpg',
            uploaded_by=self.seller,
        )
        other_seller = User.objects.create_user(
            username='gallery-wholesaler',
            password='test-password-123',
            role=User.Role.WHOLESALER,
        )
        other_image = GalleryImage.objects.create(
            title='Wholesale stock',
            image='gallery/wholesale-stock.jpg',
            uploaded_by=other_seller,
        )

        self.client.force_login(self.seller)
        self.assertRedirects(
            self.client.get(reverse('images_show')),
            reverse('my_images'),
        )
        response = self.client.get(reverse('my_images'))
        self.assertContains(response, own_image.title)
        self.assertNotContains(response, other_image.title)

        self.client.force_login(self.buyer)
        self.assertRedirects(
            self.client.get(reverse('images_show')),
            reverse('buyer_dashboard'),
        )
        self.assertRedirects(
            self.client.get(reverse('my_images')),
            reverse('buyer_dashboard'),
        )
        self.assertRedirects(
            self.client.get(reverse('upload_image')),
            reverse('buyer_dashboard'),
        )
        dashboard = self.client.get(reverse('buyer_dashboard'))
        self.assertNotContains(dashboard, 'My Images')
        self.assertNotContains(dashboard, '>Gallery<')

    def test_shopkeeper_can_edit_only_their_own_gallery_image(self):
        image = GalleryImage.objects.create(
            title='Shop front',
            image='gallery/shop-front.jpg',
            uploaded_by=self.seller,
        )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse('edit_image', args=[image.pk]),
            {'title': 'Updated shop front', 'description': 'New storefront photo'},
        )

        self.assertRedirects(response, reverse('my_images'))
        image.refresh_from_db()
        self.assertEqual(image.title, 'Updated shop front')
        self.assertEqual(image.description, 'New storefront photo')

        other_seller = User.objects.create_user(
            username='image-wholesaler',
            password='test-password-123',
            role=User.Role.WHOLESALER,
        )
        self.client.force_login(other_seller)
        self.assertEqual(
            self.client.get(reverse('edit_image', args=[image.pk])).status_code,
            404,
        )

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


class SupplyChainMarketplaceTests(TestCase):
    def setUp(self):
        self.industry = User.objects.create_user(
            username='industry',
            password='test-password-123',
            role=User.Role.INDUSTRY,
            business_name='Factory One',
        )
        self.wholesaler = User.objects.create_user(
            username='wholesaler',
            password='test-password-123',
            role=User.Role.WHOLESALER,
            business_name='Wholesale One',
        )
        self.shopkeeper = User.objects.create_user(
            username='shopkeeper',
            password='test-password-123',
            role=User.Role.SHOPKEEPER,
            business_name='Local Shop',
            city='Surat',
            state='Gujarat',
        )
        self.customer = User.objects.create_user(
            username='customer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        self.industry_product = Product.objects.create(
            name='Factory fabric',
            owner=self.industry,
            inventory=100,
        )
        self.wholesale_product = Product.objects.create(
            name='Wholesale fabric',
            owner=self.wholesaler,
            inventory=50,
        )
        self.shop_product = Product.objects.create(
            name='Shop fabric',
            owner=self.shopkeeper,
            inventory=20,
        )

    def test_catalog_and_product_details_follow_the_supply_chain(self):
        for owner, title in [
            (self.industry, 'Factory advertisement'),
            (self.wholesaler, 'Wholesale advertisement'),
            (self.shopkeeper, 'Shop advertisement'),
        ]:
            SellerBanner.objects.create(
                owner=owner,
                title=title,
                image='seller-banners/test.png',
                target_url='https://seller.example/',
            )

        self.client.force_login(self.customer)
        response = self.client.get(reverse('catalog'))
        self.assertContains(response, 'Shop fabric')
        self.assertNotContains(response, 'Wholesale fabric')
        self.assertNotContains(response, 'Factory fabric')
        self.assertEqual(
            list(response.context['banners'].values_list('title', flat=True)),
            ['Shop advertisement'],
        )
        self.assertEqual(
            self.client.get(reverse('product_detail', args=[self.wholesale_product.slug])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse('product_detail', args=[self.industry_product.slug])).status_code,
            404,
        )

        self.client.force_login(self.shopkeeper)
        response = self.client.get(reverse('catalog'))
        self.assertContains(response, 'Wholesale fabric')
        self.assertNotContains(response, 'Shop fabric')
        self.assertNotContains(response, 'Factory fabric')

        self.client.force_login(self.wholesaler)
        response = self.client.get(reverse('catalog'))
        self.assertContains(response, 'Factory fabric')
        self.assertNotContains(response, 'Wholesale fabric')
        self.assertNotContains(response, 'Shop fabric')

        self.client.force_login(self.industry)
        self.assertEqual(
            self.client.get(reverse('catalog')).context['total_products'],
            0,
        )

    def test_product_seller_role_does_not_change_when_owner_switches_active_role(self):
        self.assertEqual(self.shop_product.seller_role, Product.SellerRole.SHOPKEEPER)
        self.shopkeeper.role = User.Role.BUYER
        self.shopkeeper.save(update_fields=['role', 'updated_at'])

        self.shop_product.refresh_from_db()

        self.assertEqual(self.shop_product.seller_role, Product.SellerRole.SHOPKEEPER)

    def test_orders_and_quotes_reject_buyers_at_the_wrong_supply_level(self):
        self.client.force_login(self.customer)
        response = self.client.post(
            reverse('create_order', args=[self.wholesale_product.slug]),
            {'quantity': '1', 'notes': ''},
        )
        self.assertRedirects(response, reverse('buyer_dashboard'))
        self.assertFalse(Order.objects.exists())

        response = self.client.post(
            reverse('request_quote', args=[self.industry_product.slug]),
            {'quantity': '1', 'message': ''},
        )
        self.assertRedirects(response, reverse('catalog'))
        self.assertFalse(QuoteRequest.objects.exists())

        self.client.force_login(self.shopkeeper)
        response = self.client.post(
            reverse('create_order', args=[self.wholesale_product.slug]),
            {'quantity': '2', 'notes': ''},
        )
        order = Order.objects.get()
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        self.assertEqual(order.buyer, self.shopkeeper)
        self.assertEqual(order.seller, self.wholesaler)

        self.client.force_login(self.wholesaler)
        response = self.client.post(
            reverse('create_order', args=[self.industry_product.slug]),
            {'quantity': '3', 'notes': ''},
        )
        order = Order.objects.order_by('-pk').first()
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        self.assertEqual(order.buyer, self.wholesaler)
        self.assertEqual(order.seller, self.industry)

    def test_customers_can_explore_shops_and_order_while_closed(self):
        self.assertFalse(self.shopkeeper.shop_is_open)
        self.assertContains(self.client.get(reverse('shops')), 'Local Shop')
        detail = self.client.get(reverse('shop_detail', args=[self.shopkeeper.pk]))
        self.assertContains(detail, 'Closed now')
        self.assertContains(detail, 'Shop fabric')
        self.assertContains(detail, 'Order requests can still be sent')

        self.client.force_login(self.customer)
        response = self.client.post(
            reverse('create_order', args=[self.shop_product.slug]),
            {'quantity': '2', 'notes': 'Please prepare this order.'},
        )
        order = Order.objects.get()
        self.assertRedirects(response, reverse('order_detail', args=[order.pk]))
        self.assertEqual(order.status, Order.Status.PENDING)

        self.client.force_login(self.shopkeeper)
        status_url = reverse('set_shop_status')
        self.assertEqual(self.client.get(status_url).status_code, 405)
        self.client.post(status_url, {'status': 'open'})
        self.shopkeeper.refresh_from_db()
        self.assertTrue(self.shopkeeper.shop_is_open)
        self.assertIsNotNone(self.shopkeeper.shop_status_changed_at)
        self.assertContains(self.client.get(reverse('shops')), 'Open')

    def test_buyer_can_sort_available_shop_products_by_pincode_distance(self):
        self.customer.pincode = '395001'
        self.customer.save(update_fields=['pincode'])
        near_shop = User.objects.create_user(
            username='near-shop',
            password='test-password-123',
            role=User.Role.SHOPKEEPER,
            business_name='Nearby Store',
            pincode='395002',
        )
        far_shop = User.objects.create_user(
            username='far-shop',
            password='test-password-123',
            role=User.Role.SHOPKEEPER,
            business_name='Far Store',
            pincode='400001',
        )
        for pincode, latitude, longitude in [
            ('395001', '21.170200', '72.831100'),
            ('395002', '21.180000', '72.840000'),
            ('400001', '23.000000', '72.600000'),
        ]:
            PincodeLocation.objects.create(
                pincode=pincode,
                latitude=latitude,
                longitude=longitude,
            )
        nearby_product = Product.objects.create(
            name='Nearby cotton',
            owner=near_shop,
            inventory=5,
        )
        distant_product = Product.objects.create(
            name='Distant cotton',
            owner=far_shop,
            inventory=10,
        )
        Product.objects.create(
            name='Unavailable cotton',
            owner=near_shop,
            inventory=0,
        )
        Product.objects.create(
            name='Below-minimum cotton',
            owner=near_shop,
            inventory=2,
            minimum_order=5,
        )
        self.client.force_login(self.customer)

        response = self.client.get(
            reverse('catalog'),
            {'q': 'cotton', 'sort': 'nearest'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [product.pk for product in response.context['products']],
            [nearby_product.pk, distant_product.pk],
        )
        self.assertLess(
            response.context['products'][0].distance_km,
            response.context['products'][1].distance_km,
        )
        self.assertContains(response, 'About')
        self.assertNotContains(response, 'Unavailable cotton')
        self.assertNotContains(response, 'Below-minimum cotton')

    def test_nearest_sort_explains_when_buyer_has_no_pincode(self):
        self.client.force_login(self.customer)

        response = self.client.get(reverse('catalog'), {'sort': 'nearest'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Add your six-digit PIN code in Settings')
        self.assertEqual(response.context['total_products'], 0)

    def test_shopkeeper_order_center_includes_orders_from_suppliers_and_customers(self):
        Order.objects.create(
            product=self.wholesale_product,
            buyer=self.shopkeeper,
            seller=self.wholesaler,
            quantity=2,
        )
        Order.objects.create(
            product=self.shop_product,
            buyer=self.customer,
            seller=self.shopkeeper,
            quantity=1,
        )
        self.client.force_login(self.shopkeeper)

        response = self.client.get(reverse('my_orders'))

        self.assertContains(response, 'Wholesale fabric')
        self.assertContains(response, 'Shop fabric')


class ProductDiscoveryTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            username='discovery-buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        self.seller = User.objects.create_user(
            username='discovery-shop',
            password='test-password-123',
            role=User.Role.SHOPKEEPER,
            business_name='Discovery Shop',
        )
        self.fabric = Product.objects.create(
            owner=self.seller,
            name='Preferred cotton',
            category=Product.Category.FABRICS,
            price='260.00',
            minimum_order=5,
            inventory=30,
        )
        self.other_category = Product.objects.create(
            owner=self.seller,
            name='Packaging rolls',
            category=Product.Category.PACKAGING,
            price='20.00',
            minimum_order=1,
            inventory=100,
        )

    def test_default_catalog_ranks_past_order_category_and_explains_it(self):
        Order.objects.create(
            product=self.fabric,
            buyer=self.buyer,
            seller=self.seller,
            quantity=8,
            status=Order.Status.DELIVERED,
        )
        self.client.force_login(self.buyer)

        response = self.client.get(reverse('catalog'))

        products = list(response.context['products'])
        self.assertEqual(products[0], self.fabric)
        self.assertIn(
            'Matches categories you have explored or sourced',
            products[0].recommendation_reasons,
        )
        self.assertIn('Fits your usual price range', products[0].recommendation_reasons)
        self.assertIn(
            'Available in your usual quantity range',
            products[0].recommendation_reasons,
        )

    def test_new_buyer_fallback_uses_popularity_and_stock(self):
        other_buyer = User.objects.create_user(
            username='another-buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        Order.objects.create(
            product=self.other_category,
            buyer=other_buyer,
            seller=self.seller,
            quantity=4,
            status=Order.Status.ACCEPTED,
        )

        self.client.force_login(self.buyer)
        response = self.client.get(reverse('catalog'))

        products = list(response.context['products'])
        self.assertEqual(products[0], self.other_category)
        self.assertIn('Receiving orders or enquiries on GAB', products[0].recommendation_reasons)
        self.assertIn('Available now', products[0].recommendation_reasons)

    def test_recommendations_explain_nearby_suppliers(self):
        self.buyer.pincode = '395001'
        self.buyer.save(update_fields=['pincode'])
        self.seller.pincode = '395002'
        self.seller.save(update_fields=['pincode'])
        distant_seller = User.objects.create_user(
            username='distant-shop',
            password='test-password-123',
            role=User.Role.SHOPKEEPER,
            pincode='400001',
        )
        distant_product = Product.objects.create(
            owner=distant_seller,
            name='Distant fabric',
            category=Product.Category.FABRICS,
            inventory=30,
            minimum_order=5,
        )
        for pincode, latitude, longitude in [
            ('395001', '21.170200', '72.831100'),
            ('395002', '21.180000', '72.840000'),
            ('400001', '23.000000', '72.600000'),
        ]:
            PincodeLocation.objects.create(
                pincode=pincode,
                latitude=latitude,
                longitude=longitude,
            )
        self.client.force_login(self.buyer)

        products = list(self.client.get(reverse('catalog')).context['products'])
        recommended_fabric = next(
            product for product in products if product.pk == self.fabric.pk
        )

        self.assertLess(products.index(self.fabric), products.index(distant_product))
        self.assertIn(
            'Supplier is in a nearby PIN-code area',
            recommended_fabric.recommendation_reasons,
        )

    def test_authenticated_search_and_recommendation_click_store_minimized_events(self):
        self.client.force_login(self.buyer)

        self.client.get(reverse('catalog'), {'q': '  cotton private phrase  '})
        self.client.get(
            reverse('product_detail', args=[self.fabric.slug]),
            {'source': 'recommendation'},
        )

        search_event = ProductInteraction.objects.get(
            event_type=ProductInteraction.EventType.SEARCH,
        )
        self.assertEqual(search_event.user, self.buyer)
        self.assertEqual(
            search_event.search_hash,
            hash_search_query('  cotton private phrase  '),
        )
        self.assertNotIn('private phrase', search_event.search_hash)
        self.assertEqual(
            set(ProductInteraction.objects.exclude(
                event_type=ProductInteraction.EventType.SEARCH,
            ).values_list('event_type', flat=True)),
            {
                ProductInteraction.EventType.CLICK,
                ProductInteraction.EventType.PRODUCT_VIEW,
            },
        )

    def test_successful_quote_request_is_recorded_as_interest(self):
        self.client.force_login(self.buyer)

        response = self.client.post(
            reverse('request_quote', args=[self.fabric.slug]),
            {'quantity': '8', 'message': ''},
        )

        self.assertRedirects(response, reverse('buyer_quotes'))
        self.assertTrue(
            ProductInteraction.objects.filter(
                user=self.buyer,
                product=self.fabric,
                event_type=ProductInteraction.EventType.QUOTE_REQUEST,
            ).exists()
        )
