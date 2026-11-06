import json

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse


class ProgressiveWebAppTests(TestCase):
    def test_requested_public_domain_is_allowed_for_site_and_csrf(self):
        self.assertIn('gabpt.com', settings.ALLOWED_HOSTS)
        self.assertIn('www.gabpt.com', settings.ALLOWED_HOSTS)
        self.assertIn(
            'https://www.gabpt.com',
            settings.CSRF_TRUSTED_ORIGINS,
        )

    def test_production_media_storage_can_use_the_render_persistent_disk(self):
        self.assertEqual(
            settings.STORAGES['default']['BACKEND'],
            'django.core.files.storage.FileSystemStorage',
        )

    def test_web_app_manifest_has_installable_icons(self):
        manifest_path = finders.find('manifest.webmanifest')
        self.assertIsNotNone(manifest_path)
        with open(manifest_path, encoding='utf-8') as manifest_file:
            manifest = json.load(manifest_file)

        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(
            {icon['sizes'] for icon in manifest['icons']},
            {'192x192', '512x512'},
        )

    def test_static_assets_are_referenced_by_the_base_template(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, 'gab-install-button')
        self.assertContains(response, 'theme-toggle')
        self.assertContains(response, "localStorage.setItem('gab-theme'")
        self.assertContains(response, "navigator.serviceWorker.register")

    def test_service_worker_is_available_at_root_with_site_wide_scope(self):
        response = self.client.get(reverse('service_worker'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript')
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertContains(response, "request.mode === 'navigate'")


class DeploymentHealthCheckTests(TestCase):
    def test_health_check_confirms_database_connection(self):
        response = self.client.get(reverse('health_check'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')
