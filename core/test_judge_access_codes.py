from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.views import _allocate_access_code
from events.models import AssignmentAccountProfile


class JudgeAccessCodeTests(TestCase):
    def test_judge_code_has_three_random_letters_and_two_digits(self):
        with patch('secrets.choice', side_effect=list('KRM47')):
            self.assertEqual(_allocate_access_code('Judge'), 'KRM47')

    def test_existing_code_is_not_reused_even_when_regenerating(self):
        user = get_user_model().objects.create_user(username='code-owner')
        AssignmentAccountProfile.objects.create(user=user, access_code='krm47')
        with patch('secrets.choice', side_effect=list('KRM47XTA09')):
            self.assertEqual(_allocate_access_code('Judge', exclude_user_id=user.pk), 'XTA09')

    def test_repeated_collisions_fail_without_returning_a_duplicate(self):
        user = get_user_model().objects.create_user(username='collision-owner')
        AssignmentAccountProfile.objects.create(user=user, access_code='AAA00')
        with patch('secrets.choice', side_effect=list('AAA00') * 100):
            self.assertEqual(_allocate_access_code('Judge'), '')

    def test_scorer_generation_keeps_existing_format(self):
        self.assertEqual(_allocate_access_code('Scorer'), 'SCR01')
