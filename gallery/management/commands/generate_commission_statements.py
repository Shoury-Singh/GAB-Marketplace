from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from gallery.payment_services import generate_monthly_commission_statements


class Command(BaseCommand):
    help = 'Prepare draft monthly commission statements from offline-paid orders.'

    def add_arguments(self, parser):
        parser.add_argument('--month', help='Statement month as YYYY-MM.')

    def handle(self, *args, **options):
        month = options.get('month')
        if month:
            try:
                year, month_number = (int(part) for part in month.split('-', 1))
                period_start = date(year, month_number, 1)
            except (ValueError, TypeError):
                raise CommandError('Use --month YYYY-MM, for example --month 2026-10.')
        else:
            first_of_this_month = timezone.localdate().replace(day=1)
            period_start = (first_of_this_month - timedelta(days=1)).replace(day=1)

        statements = generate_monthly_commission_statements(period_start)
        if not statements:
            self.stdout.write(self.style.WARNING(
                f'No unbilled offline commission entries found for {period_start:%Y-%m}.'
            ))
            return
        for statement in statements:
            gst = 'unconfigured' if statement.gst_total_paise is None else f'{statement.gst_total_paise} paise'
            self.stdout.write(
                f'{statement.statement_number}: seller #{statement.seller_id}, '
                f'commission {statement.commission_total_paise} paise, GST {gst}'
            )
        self.stdout.write(self.style.SUCCESS(
            f'Prepared {len(statements)} draft commission statement(s).'
        ))
