from datetime import timedelta
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import PasswordResetChallenge


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
