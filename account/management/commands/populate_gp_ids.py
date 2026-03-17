# wallet/management/commands/populate_gp_ids.py
#
# Run this ONCE after the first migration (when gp_id is added as non-unique).
# It assigns a unique GP ID to every existing user that doesn't have one yet.
#
# Usage:
#   python manage.py populate_gp_ids
#
# After running this command, change gp_id's unique=False → unique=True in
# account/models.py and run makemigrations + migrate one more time.

import random
import string
from django.core.management.base import BaseCommand
from account.models import CustomUser


def _generate_unique_gp_id(existing_ids):
    while True:
        code = 'GP-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in existing_ids:
            return code


class Command(BaseCommand):
    help = 'Populate gp_id for all existing users that do not have one'

    def handle(self, *args, **options):
        # Load all existing IDs to avoid DB hit per user
        existing_ids = set(
            CustomUser.objects.exclude(gp_id='').values_list('gp_id', flat=True)
        )

        users_needing_id = CustomUser.objects.filter(gp_id='')
        total = users_needing_id.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS('All users already have a GP ID. Nothing to do.'))
            return

        self.stdout.write(f'Assigning GP IDs to {total} users...')
        updated = 0

        for user in users_needing_id:
            new_id = _generate_unique_gp_id(existing_ids)
            existing_ids.add(new_id)
            user.gp_id = new_id
            user.save(update_fields=['gp_id'])
            updated += 1
            if updated % 50 == 0:
                self.stdout.write(f'  {updated}/{total} done...')

        self.stdout.write(
            self.style.SUCCESS(
                f'\nDone. {updated} users assigned a GP ID.\n'
                f'Next step: change gp_id unique=False → unique=True in account/models.py\n'
                f'then run: python manage.py makemigrations account && python manage.py migrate'
            )
        )