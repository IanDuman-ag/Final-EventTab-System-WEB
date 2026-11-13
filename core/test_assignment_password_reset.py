import json
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token


class AssignmentPasswordResetTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('reset-admin', is_staff=True)
        self.target = get_user_model().objects.create_user('faculty-reset', password='Original!4729')
        self.target.groups.add(Group.objects.get_or_create(name='Faculty')[0])
        self.url = reverse('reset_assignment_account_password', args=[self.target.pk])
        self.client.force_login(self.admin)
        self.password = 'New-Cedar!57294'

    def post(self, payload):
        return self.client.post(self.url, json.dumps(payload), content_type='application/json')

    def test_resets_only_password_and_revokes_token_without_logging_secret(self):
        Token.objects.create(user=self.target)
        response = self.post({'password': self.password, 'password_confirm': self.password})
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(self.password))
        self.assertEqual(self.target.username, 'faculty-reset')
        self.assertTrue(self.target.groups.filter(name='Faculty').exists())
        self.assertFalse(Token.objects.filter(user=self.target).exists())
        self.assertNotIn(self.password, response.content.decode())
        self.assertNotIn(self.password, LogEntry.objects.latest('id').change_message)

    def test_accepts_tabulator_accounts(self):
        self.target.groups.clear()
        self.target.groups.add(Group.objects.get_or_create(name='Tabulator')[0])
        self.assertEqual(self.post({'password': self.password, 'password_confirm': self.password}).status_code, 200)

    def test_rejects_invalid_passwords_and_payloads_without_changing_password(self):
        for payload in ([], {}, {'password': 123}, {'password': self.password, 'password_confirm': 'different'},
                        {'password': '123', 'password_confirm': '123'}):
            self.assertEqual(self.post(payload).status_code, 400)
            self.target.refresh_from_db()
            self.assertTrue(self.target.check_password('Original!4729'))
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_protects_other_roles_and_administrators(self):
        self.target.groups.clear()
        self.target.groups.add(Group.objects.get_or_create(name='Judge')[0])
        self.assertEqual(self.post({'password': self.password, 'password_confirm': self.password}).status_code, 404)
        self.target.is_staff = True
        self.target.save()
        self.assertEqual(self.post({'password': self.password, 'password_confirm': self.password}).status_code, 404)

    def test_requires_admin_and_csrf(self):
        self.client.logout()
        self.assertEqual(self.post({}).status_code, 302)
        self.client.force_login(self.target)
        self.assertEqual(self.post({}).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url, '{}', content_type='application/json').status_code, 403)

    def test_audit_failure_rolls_back_password_and_token(self):
        token = Token.objects.create(user=self.target)
        with patch('core.views.LogEntry.objects.create', side_effect=RuntimeError('Audit unavailable')):
            response = self.post({'password': self.password, 'password_confirm': self.password})
        self.assertEqual(response.status_code, 500)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password('Original!4729'))
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())
