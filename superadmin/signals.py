import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from discount_store.models import StoreOrder
from bookings.models import Booking, BookingStatus
from .models import SalesRecord
from .utils import create_sales_record_for_booking

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Booking)
def create_sales_record_on_check_in(sender, instance, **kwargs):
    """
    Auto-create a SalesRecord when a booking is checked in.

    Idempotent:
    - if a sales record already exists, do nothing
    - if creation fails, never break the booking save itself
    """
    if instance.status != BookingStatus.CHECKED_IN or not instance.checked_in_at:
        return

    try:
        _ = instance.sales_record
        return
    except SalesRecord.DoesNotExist:
        pass

    try:
        create_sales_record_for_booking(instance)
    except Exception as exc:
        logger.error(
            "SalesRecord creation failed for booking %s: %s",
            instance.booking_reference,
            exc,
            exc_info=True,
        )

@receiver(post_save, sender=StoreOrder)
def create_sales_record_on_store_order_paid(sender, instance, **kwargs):
    """
    Auto-create a SalesRecord when a StoreOrder transitions to PAID.
    """
    if instance.status != StoreOrder.OrderStatus.PAID or not instance.booking_id:
        return

    try:
        from .models import SalesRecord
        _ = instance.booking.sales_record
        return  # already exists
    except SalesRecord.DoesNotExist:
        pass
    except Exception:
        return

    try:
        from .utils import create_sales_record_for_booking
        create_sales_record_for_booking(instance.booking)
    except Exception as exc:
        logger.error(
            "SalesRecord creation failed for store order %s: %s",
            instance.reference,
            exc,
            exc_info=True,
        )
