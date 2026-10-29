from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from events.models import AssignmentAccountProfile


class AccessCodeLoginTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.user = get_user_model().objects.create_user(
            username='reconciliation-judge', password='test-password', email='judge@example.test')
        self.user.groups.add(Group.objects.get_or_create(name='Judge')[0])
        AssignmentAccountProfile.objects.create(user=self.user, access_code='JDG01')
        self.url = reverse('auth_access_code')

    def test_normalized_code_returns_existing_token_and_role(self):
        token = Token.objects.create(user=self.user)
        response = self.api.post(self.url, {'access_code': ' jdg- 01 '}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'token': token.key, 'role': 'judge',
            'user': {'id': self.user.pk, 'username': self.user.username, 'email': self.user.email}})
        self.assertEqual(Token.objects.filter(user=self.user).count(), 1)
        self.api.credentials(HTTP_AUTHORIZATION='Token ' + response.json()['token'])
        self.assertEqual(self.api.get(reverse('auth_me')).json()['id'], self.user.pk)

    def test_scorer_code_creates_token(self):
        self.user.groups.clear()
        self.user.groups.add(Group.objects.get_or_create(name='Scorer')[0])
        response = self.api.post(self.url, {'access_code': 'jdg01'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['role'], 'scorer')
        self.assertTrue(Token.objects.filter(user=self.user).exists())

    def test_missing_invalid_and_oversized_codes_create_no_tokens(self):
        for payload in ({}, {'access_code': None}, {'access_code': 123}, {'access_code': []},
                        {'access_code': ''}, {'access_code': ' - '}, {'access_code': 'unknown'},
                        {'access_code': 'X' * 33}):
            with self.subTest(payload=payload):
                self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 400)
        self.assertFalse(Token.objects.exists())

    def test_ambiguous_codes_create_no_tokens(self):
        other = get_user_model().objects.create_user(username='ambiguous-judge')
        other.groups.add(Group.objects.get(name='Judge'))
        AssignmentAccountProfile.objects.create(user=other, access_code='jdg01')
        self.assertEqual(self.api.post(self.url, {'access_code': 'JDG01'}, format='json').status_code, 400)
        self.assertFalse(Token.objects.exists())

    def test_disabled_and_wrong_role_accounts_create_no_tokens(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.api.post(self.url, {'access_code': 'JDG01'}, format='json').status_code, 400)
        self.user.is_active = True
        self.user.save(update_fields=['is_active'])
        self.user.groups.clear()
        self.user.groups.add(Group.objects.get_or_create(name='Faculty')[0])
        self.assertEqual(self.api.post(self.url, {'access_code': 'JDG01'}, format='json').status_code, 400)
        self.assertFalse(Token.objects.exists())

    def test_existing_password_login_and_post_only_code_route(self):
        response = self.api.post(reverse('auth_login'),
            {'identifier': self.user.username, 'password': 'test-password', 'require_judge': True}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['id'], self.user.pk)
        self.assertEqual(self.api.get(self.url).status_code, 405)
