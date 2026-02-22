from __future__ import annotations

from django.core.management.base import BaseCommand

from accounts.models import User


class Command(BaseCommand):
    help = "Create default admin user for MVP if not present."

    def handle(self, *args, **options):
        username = "admin"
        password = "admin123"

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": "System",
                "last_name": "Admin",
                "role": User.Role.ADMIN,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )

        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS("Created default admin user: admin / admin123"))
            return

        self.stdout.write(self.style.WARNING("Admin user already exists. Skipped."))
