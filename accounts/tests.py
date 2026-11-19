from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from .models import LoginAudit, Profile

User = get_user_model()


class RegistrationTests(TestCase):
    def test_register_page_renders(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your account")

    def test_participant_can_register_and_is_logged_in(self):
        response = self.client.post(reverse("accounts:register"), {
            "first_name": "Neha", "last_name": "Kapoor", "username": "neha",
            "email": "neha@example.com", "role": "participant",
            "password1": "s3cret-Event-9", "password2": "s3cret-Event-9",
            "terms": "on",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="neha")
        self.assertEqual(user.role, User.Role.PARTICIPANT)
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertContains(response, "Welcome aboard")

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user("existing", "dup@example.com", "pw12345678")
        response = self.client.post(reverse("accounts:register"), {
            "first_name": "A", "last_name": "B", "username": "another",
            "email": "dup@example.com", "role": "participant",
            "password1": "s3cret-Event-9", "password2": "s3cret-Event-9", "terms": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertFalse(User.objects.filter(username="another").exists())

    def test_password_mismatch_is_rejected(self):
        response = self.client.post(reverse("accounts:register"), {
            "first_name": "A", "last_name": "B", "username": "mismatch",
            "email": "mismatch@example.com", "role": "participant",
            "password1": "s3cret-Event-9", "password2": "different-pass-1", "terms": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="mismatch").exists())


class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mina", "mina@example.com", "pw12345678",
                                             first_name="Mina", last_name="Roy")

    def test_login_with_username(self):
        response = self.client.post(reverse("accounts:login"),
                                    {"username": "mina", "password": "pw12345678"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["user"].is_authenticated)
        self.assertTrue(LoginAudit.objects.filter(user=self.user).exists())

    def test_login_with_email(self):
        self.client.post(reverse("accounts:login"),
                         {"username": "mina@example.com", "password": "pw12345678"})
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_invalid_login_shows_error(self):
        response = self.client.post(reverse("accounts:login"),
                                    {"username": "mina", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_clears_session(self):
        self.client.force_login(self.user)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(reverse("accounts:login"),
                                    {"username": "mina", "password": "pw12345678"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "did not match")

    def test_password_reset_sends_email(self):
        response = self.client.post(reverse("accounts:password_reset"), {"email": "mina@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("password", mail.outbox[0].subject.lower())


class ProfileAndPasswordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("omar", "omar@example.com", "pw12345678")
        self.client.force_login(self.user)

    def test_profile_update(self):
        response = self.client.post(reverse("accounts:profile"), {
            "user-first_name": "Omar", "user-last_name": "Farooq", "user-email": "omar@example.com",
            "user-email_notifications": "on",
            "profile-phone": "+91 90000 00000", "profile-city": "Pune",
            "profile-bio": "Runs community events.", "save_details": "1",
        }, follow=True)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Omar")
        self.assertContains(response, "updated")

    def test_password_change(self):
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": "pw12345678",
            "new_password1": "brand-New-Pass-77",
            "new_password2": "brand-New-Pass-77",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brand-New-Pass-77"))

    def test_profile_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)


class RoleRoutingTests(TestCase):
    def test_role_dashboard_router(self):
        admin = User.objects.create_superuser("root", "root@example.com", "pw12345678")
        organizer = User.objects.create_user("org", "org@example.com", "pw12345678", role=User.Role.ORGANIZER)
        participant = User.objects.create_user("part", "part@example.com", "pw12345678")

        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("dashboard:router")).url, reverse("dashboard:admin"))
        self.client.force_login(organizer)
        self.assertEqual(self.client.get(reverse("dashboard:router")).url, reverse("dashboard:organizer"))
        self.client.force_login(participant)
        self.assertEqual(self.client.get(reverse("dashboard:router")).url, reverse("dashboard:participant"))
