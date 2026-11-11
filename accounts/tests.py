from datetime import timedelta
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import (
    BuyerAccountData,
    IndustryAccountData,
    PincodeLocation,
    PasswordResetChallenge,
    ShopkeeperAccountData,
    UserRole,
    WholesalerAccountData,
)
from accounts.forms import UserRegistrationForm
from gallery.models import Order, Product


User = get_user_model()


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='GAB <noreply@example.com>',
)
class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='password-user',
            email='password-user@example.com',
            password='Original-password-123!',
        )

    def request_code(self, email=None):
        return self.client.post(reverse('password_reset'), {
            'action': 'request',
            'email': email or self.user.email,
        })

    def code_from_email(self):
        match = re.search(r'\b(\d{6})\b', mail.outbox[-1].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def reset_password(self, code, password='A-new-unique-password-456!'):
        return self.client.post(reverse('password_reset'), {
            'action': 'verify',
            'code': code,
            'new_password': password,
            'confirm_password': password,
        })

    def test_user_can_reset_password_with_one_time_email_code(self):
        response = self.request_code()

        self.assertRedirects(response, reverse('password_reset'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertContains(self.client.get(reverse('password_reset')), 'Enter your code')

        response = self.reset_password(self.code_from_email())

        self.assertRedirects(response, reverse('login'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('A-new-unique-password-456!'))
        challenge = PasswordResetChallenge.objects.get(user=self.user)
        self.assertTrue(challenge.is_used)

        response = self.reset_password(
            self.code_from_email(),
            password='Another-unique-password-789!',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid or expired')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('A-new-unique-password-456!'))

    def test_wrong_code_does_not_consume_all_attempts_or_hide_form(self):
        self.request_code()
        correct_code = self.code_from_email()
        wrong_code = '000000' if correct_code != '000000' else '999999'

        response = self.reset_password(wrong_code)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'That code is incorrect')
        self.assertContains(response, 'Enter your code')
        self.assertEqual(PasswordResetChallenge.objects.get(user=self.user).attempts, 1)

        response = self.reset_password(correct_code)

        self.assertRedirects(response, reverse('login'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('A-new-unique-password-456!'))

    def test_unknown_email_gets_same_response_without_sending_email(self):
        response = self.client.post(reverse('password_reset'), {
            'action': 'request',
            'email': 'not-registered@example.com',
        }, follow=True)

        self.assertEqual(response.redirect_chain, [(reverse('password_reset'), 302)])
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'If an active account uses that email')

    def test_expired_code_cannot_reset_password(self):
        self.request_code()
        challenge = PasswordResetChallenge.objects.get(user=self.user)
        challenge.expires_at = timezone.now() - timedelta(minutes=1)
        challenge.save(update_fields=['expires_at'])

        response = self.reset_password(self.code_from_email())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid or expired')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Original-password-123!'))

    def test_password_reset_requests_are_rate_limited(self):
        for _ in range(4):
            self.request_code()

        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(PasswordResetChallenge.objects.count(), 3)

    def test_five_incorrect_codes_lock_the_reset_challenge(self):
        self.request_code()
        code = self.code_from_email()
        wrong_code = '000000' if code != '000000' else '999999'

        for _ in range(5):
            response = self.reset_password(wrong_code)

        self.assertEqual(response.status_code, 200)
        challenge = PasswordResetChallenge.objects.get(user=self.user)
        self.assertEqual(challenge.attempts, 5)
        self.assertTrue(challenge.is_used)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Original-password-123!'))


class WholesalerDashboardTests(TestCase):
    def test_dashboard_uses_separate_cards_and_counts_pending_orders(self):
        wholesaler = User.objects.create_user(
            username='wholesaler',
            password='test-password-123',
            role=User.Role.WHOLESALER,
        )
        buyer = User.objects.create_user(
            username='buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        product = Product.objects.create(
            name='Bulk cotton',
            owner=wholesaler,
            inventory=20,
        )
        Order.objects.create(
            product=product,
            buyer=buyer,
            seller=wholesaler,
            quantity=5,
        )
        self.client.force_login(wholesaler)

        response = self.client.get(reverse('wholesaler_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['incoming_orders'], 1)
        self.assertContains(response, 'wholesaler-dashboard.png')
        self.assertContains(response, 'Pending orders')
        self.assertContains(response, 'Review orders')


class BuyerDashboardTests(TestCase):
    def test_dashboard_uses_customer_illustration_and_shop_links(self):
        buyer = User.objects.create_user(
            username='buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        self.client.force_login(buyer)

        response = self.client.get(reverse('buyer_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'buyer-dashboard.png')
        self.assertContains(response, 'role-dashboard-hero')
        self.assertContains(response, 'Explore shops')


class ShopkeeperDashboardTests(TestCase):
    def test_dashboard_uses_shopkeeper_image_and_counts_pending_orders(self):
        shopkeeper = User.objects.create_user(
            username='shopkeeper',
            password='test-password-123',
            role=User.Role.SHOPKEEPER,
        )
        buyer = User.objects.create_user(
            username='buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        product = Product.objects.create(
            name='Store goods',
            owner=shopkeeper,
            inventory=10,
        )
        Order.objects.create(
            product=product,
            buyer=buyer,
            seller=shopkeeper,
            quantity=2,
        )
        self.client.force_login(shopkeeper)

        response = self.client.get(reverse('shopkeeper_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['incoming_orders'], 1)
        self.assertContains(response, 'shopkeeper-dashboard.png')
        self.assertContains(response, 'role-dashboard-hero')
        self.assertContains(response, 'Pending orders')
        self.assertContains(response, 'Review orders')


class AccountRoleManagementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='multi-role-user',
            email='multi-role@example.com',
            password='test-password-123',
            role=User.Role.BUYER,
            phone='5551234567',
        )

    def test_user_can_add_and_switch_roles_without_creating_another_account(self):
        self.client.force_login(self.user)
        settings_url = reverse('account_settings')
        response = self.client.post(settings_url, {
            'action': 'add-role',
            'role': User.Role.WHOLESALER,
            'business_name': 'Northside Wholesale',
        })

        self.assertRedirects(response, settings_url)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(self.user.role_assignments.count(), 2)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, User.Role.BUYER)
        self.assertEqual(self.user.phone, '5551234567')
        self.assertEqual(
            self.user.role_assignments.get(role=User.Role.WHOLESALER).business_name,
            'Northside Wholesale',
        )

        response = self.client.post(settings_url, {
            'action': 'switch-role',
            'role': User.Role.WHOLESALER,
        })
        self.assertRedirects(response, reverse('wholesaler_dashboard'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, User.Role.WHOLESALER)
        self.assertEqual(self.user.get_available_roles(), [
            User.Role.WHOLESALER,
            User.Role.BUYER,
        ])
        self.assertEqual(
            self.client.get(reverse('wholesaler_dashboard')).status_code,
            200,
        )

    def test_seller_role_requires_a_business_name(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('account_settings'), {
            'action': 'add-role',
            'role': User.Role.INDUSTRY,
            'business_name': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A business name is required')
        self.assertFalse(self.user.role_assignments.filter(role=User.Role.INDUSTRY).exists())
        self.assertEqual(User.objects.count(), 1)

    def test_user_cannot_switch_to_a_role_not_on_their_account(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('account_settings'), {
            'action': 'switch-role',
            'role': User.Role.SHOPKEEPER,
        })

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, User.Role.BUYER)
        self.assertFalse(self.user.role_assignments.filter(role=User.Role.SHOPKEEPER).exists())

    def test_role_management_is_linked_from_the_profile_and_signup(self):
        self.client.force_login(self.user)
        self.assertContains(
            self.client.get(reverse('profile')),
            'Manage roles',
        )
        self.client.logout()

        self.assertContains(
            self.client.get(reverse('register')),
            'add or switch roles later in Settings',
        )


class SellerBusinessProfileTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            username='profile-shop',
            password='test-password-123',
            role=User.Role.SHOPKEEPER,
            business_name='Old Shop Name',
            city='Old City',
            state='Old State',
            pincode='395001',
        )
        self.buyer = User.objects.create_user(
            username='profile-buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        self.product = Product.objects.create(
            owner=self.seller,
            name='Profile cotton',
            supplier='Old Shop Name',
            location='Old City, Old State',
            inventory=10,
        )

    def test_seller_can_update_business_profile_and_public_listing(self):
        self.client.force_login(self.seller)

        response = self.client.post(reverse('account_settings'), {
            'action': 'update-business-profile',
            'business_name': 'New Supplier Shop',
            'phone': '9876543210',
            'address': '12 Market Road',
            'city': 'New City',
            'state': 'Gujarat',
            'pincode': '395007',
        })

        self.assertRedirects(response, reverse('account_settings'))
        self.seller.refresh_from_db()
        shop_profile = self.seller.role_assignments.get(role=User.Role.SHOPKEEPER)
        self.assertEqual(self.seller.business_name, 'Old Shop Name')
        self.assertEqual(shop_profile.business_name, 'New Supplier Shop')
        self.assertEqual(shop_profile.phone, '9876543210')
        self.assertEqual(shop_profile.address, '12 Market Road')
        self.assertEqual(shop_profile.city, 'New City')
        self.assertEqual(shop_profile.state, 'Gujarat')
        self.assertEqual(shop_profile.pincode, '395007')

        self.client.force_login(self.buyer)
        catalog_response = self.client.get(
            reverse('catalog'),
            {'q': 'New Supplier Shop'},
        )
        self.assertContains(catalog_response, self.product.name)
        self.assertContains(catalog_response, 'New Supplier Shop')
        self.assertContains(
            self.client.get(reverse('product_detail', args=[self.product.slug])),
            'New City',
        )
        self.assertContains(
            self.client.get(reverse('request_quote', args=[self.product.slug])),
            'New Supplier Shop',
        )
        self.assertContains(self.client.get(reverse('shops')), 'New City')

    def test_seller_roles_keep_independent_business_profiles(self):
        shop_profile = self.seller.role_assignments.get(role=User.Role.SHOPKEEPER)
        wholesale_profile = UserRole.objects.create(
            user=self.seller,
            role=User.Role.WHOLESALER,
            business_name='Wholesale Division',
            city='Wholesale City',
            state='Maharashtra',
            pincode='400001',
        )
        self.client.force_login(self.seller)

        self.client.post(reverse('account_settings'), {
            'action': 'switch-role',
            'role': User.Role.WHOLESALER,
        })
        response = self.client.get(reverse('account_settings'))

        self.assertEqual(
            response.context['business_profile_form']['business_name'].value(),
            'Wholesale Division',
        )
        self.client.post(reverse('account_settings'), {
            'action': 'update-business-profile',
            'business_name': 'Updated Wholesale Division',
            'phone': '9876543210',
            'address': 'Wholesale Road',
            'city': 'New Wholesale City',
            'state': 'Maharashtra',
            'pincode': '400002',
        })

        shop_profile.refresh_from_db()
        wholesale_profile.refresh_from_db()
        self.assertEqual(shop_profile.business_name, 'Old Shop Name')
        self.assertEqual(shop_profile.pincode, '395001')
        self.assertEqual(wholesale_profile.business_name, 'Updated Wholesale Division')
        self.assertEqual(wholesale_profile.city, 'New Wholesale City')
        self.assertEqual(wholesale_profile.pincode, '400002')

    def test_invalid_pin_preserves_profile_and_buyers_cannot_edit_seller_profile(self):
        self.client.force_login(self.seller)
        response = self.client.post(reverse('account_settings'), {
            'action': 'update-business-profile',
            'business_name': 'Should Not Save',
            'phone': '9876543210',
            'address': 'New address',
            'city': 'New City',
            'state': 'Gujarat',
            'pincode': '1234',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'valid six-digit Indian PIN code')
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.business_name, 'Old Shop Name')
        self.assertEqual(self.seller.pincode, '395001')
        self.assertEqual(
            self.seller.role_assignments.get(role=User.Role.SHOPKEEPER).business_name,
            'Old Shop Name',
        )

        self.client.force_login(self.buyer)
        response = self.client.post(reverse('account_settings'), {
            'action': 'update-business-profile',
            'business_name': 'Unauthorized Edit',
            'pincode': '395007',
        })

        self.assertRedirects(response, reverse('account_settings'))
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.business_name, 'Old Shop Name')


class PincodeAccountTests(TestCase):
    def test_registration_accepts_valid_indian_pincode_and_rejects_invalid_values(self):
        registration_data = {
            'username': 'pin-customer',
            'email': 'pin-customer@example.com',
            'role': User.Role.BUYER,
            'pincode': '395007',
            'password1': 'A-long-unique-Password-987!',
            'password2': 'A-long-unique-Password-987!',
        }
        form = UserRegistrationForm(data=registration_data)

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.pincode, '395007')

        registration_data['username'] = 'bad-pin-customer'
        registration_data['email'] = 'bad-pin-customer@example.com'
        registration_data['pincode'] = '39507'
        invalid_form = UserRegistrationForm(data=registration_data)
        self.assertFalse(invalid_form.is_valid())
        self.assertIn('pincode', invalid_form.errors)

    def test_account_settings_can_update_pincode(self):
        buyer = User.objects.create_user(
            username='pin-settings-buyer',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        self.client.force_login(buyer)
        response = self.client.post(reverse('account_settings'), {
            'action': 'update-location',
            'pincode': '395007',
        })

        self.assertRedirects(response, reverse('account_settings'))
        buyer.refresh_from_db()
        self.assertEqual(buyer.pincode, '395007')
        self.assertEqual(
            buyer.role_assignments.get(role=User.Role.BUYER).pincode,
            '395007',
        )

        response = self.client.post(reverse('account_settings'), {
            'action': 'update-location',
            'pincode': '123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'valid six-digit Indian PIN code')
        buyer.refresh_from_db()
        self.assertEqual(buyer.pincode, '395007')

    def test_admin_lists_user_pincodes_and_postal_locations(self):
        admin_user = User.objects.create_superuser(
            username='pin-admin',
            email='pin-admin@example.com',
            password='test-password-123',
        )
        buyer = User.objects.create_user(
            username='pin-admin-buyer',
            password='test-password-123',
            pincode='395007',
        )
        PincodeLocation.objects.create(
            pincode='395007',
            place_name='Surat',
            state='Gujarat',
            latitude='21.170200',
            longitude='72.831100',
        )
        self.client.force_login(admin_user)

        users_response = self.client.get(reverse('admin:accounts_user_changelist'))
        locations_response = self.client.get(
            reverse('admin:accounts_pincodelocation_changelist'),
        )

        self.assertContains(users_response, buyer.pincode)
        self.assertContains(locations_response, '395007')
        self.assertContains(locations_response, 'Surat')


class RoleAccountDataSaverTests(TestCase):
    def test_role_tables_save_account_and_role_timestamps_without_password_copies(self):
        role_tables = [
            (User.Role.BUYER, BuyerAccountData),
            (User.Role.WHOLESALER, WholesalerAccountData),
            (User.Role.SHOPKEEPER, ShopkeeperAccountData),
            (User.Role.INDUSTRY, IndustryAccountData),
        ]

        for role, model in role_tables:
            user = User.objects.create_user(
                username=f'data-{role.lower()}',
                password='test-password-123',
                role=role,
                business_name='Test Business' if role != User.Role.BUYER else '',
            )
            account_data = model.objects.get(user=user)
            role_assignment = user.role_assignments.get(role=role)

            self.assertEqual(account_data.account_created_at, user.date_joined)
            self.assertEqual(account_data.role_added_at, role_assignment.created_at)
            self.assertTrue(user.check_password('test-password-123'))
            self.assertNotIn(
                'password',
                [field.name for field in model._meta.get_fields()],
            )

    def test_added_and_removed_roles_update_their_role_table(self):
        user = User.objects.create_user(
            username='multi-role-data-user',
            password='test-password-123',
            role=User.Role.BUYER,
        )
        assignment = UserRole.objects.create(
            user=user,
            role=User.Role.SHOPKEEPER,
        )

        self.assertTrue(BuyerAccountData.objects.filter(user=user).exists())
        self.assertTrue(ShopkeeperAccountData.objects.filter(user=user).exists())

        assignment.delete()

        self.assertFalse(ShopkeeperAccountData.objects.filter(user=user).exists())
        self.assertTrue(BuyerAccountData.objects.filter(user=user).exists())
