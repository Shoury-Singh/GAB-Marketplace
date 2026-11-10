from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import PincodeLocation


class Command(BaseCommand):
    help = 'Load approximate Indian PIN-code centers from the bundled GeoNames data.'

    def handle(self, *args, **options):
        data_path = Path(settings.BASE_DIR) / 'accounts' / 'data' / 'IN.zip'
        if not data_path.is_file():
            raise CommandError(f'PIN-code dataset not found: {data_path}')

        totals = defaultdict(lambda: [Decimal('0'), Decimal('0'), 0])
        place_names = defaultdict(Counter)
        states = defaultdict(Counter)

        try:
            with ZipFile(data_path) as archive:
                with TextIOWrapper(archive.open('IN.txt'), encoding='utf-8') as rows:
                    for line in rows:
                        fields = line.rstrip('\r\n').split('\t')
                        if len(fields) < 12 or fields[0] != 'IN':
                            continue

                        pincode = fields[1]
                        if not re.fullmatch(r'[1-9][0-9]{5}', pincode):
                            continue

                        try:
                            latitude = Decimal(fields[9])
                            longitude = Decimal(fields[10])
                        except (InvalidOperation, ValueError) as error:
                            raise CommandError(
                                f'Invalid coordinates for PIN code {pincode}.'
                            ) from error

                        totals[pincode][0] += latitude
                        totals[pincode][1] += longitude
                        totals[pincode][2] += 1
                        if fields[2]:
                            place_names[pincode][fields[2]] += 1
                        if fields[3]:
                            states[pincode][fields[3]] += 1
        except (BadZipFile, KeyError, OSError) as error:
            raise CommandError(f'Could not read PIN-code dataset: {error}') from error

        locations = []
        for pincode, (latitude_total, longitude_total, count) in totals.items():
            locations.append(PincodeLocation(
                pincode=pincode,
                place_name=place_names[pincode].most_common(1)[0][0] if place_names[pincode] else '',
                state=states[pincode].most_common(1)[0][0] if states[pincode] else '',
                latitude=(latitude_total / count).quantize(Decimal('0.000001')),
                longitude=(longitude_total / count).quantize(Decimal('0.000001')),
            ))

        existing_count = PincodeLocation.objects.count()
        PincodeLocation.objects.bulk_create(
            locations,
            batch_size=1000,
            ignore_conflicts=True,
        )
        imported_count = PincodeLocation.objects.count() - existing_count
        self.stdout.write(self.style.SUCCESS(
            f'Imported {imported_count} new PIN-code locations from GeoNames; '
            f'{PincodeLocation.objects.count()} are available.'
        ))
