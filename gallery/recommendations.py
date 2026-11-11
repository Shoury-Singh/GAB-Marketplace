"""Privacy-conscious, explainable marketplace ranking.

This is an implicit-feedback baseline: it learns affinity from product views,
recommendation clicks, enquiries, and completed orders. The ranking interface
is deliberately isolated so a trained model can replace this baseline later.
"""

import hashlib
import hmac
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from math import atan2, cos, radians, sin, sqrt
from statistics import median

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from accounts.models import PincodeLocation

from .models import Order, Product, ProductInteraction


EVENT_WEIGHTS = {
    ProductInteraction.EventType.PRODUCT_VIEW: 1,
    ProductInteraction.EventType.SEARCH: 1.5,
    ProductInteraction.EventType.CLICK: 3,
    ProductInteraction.EventType.QUOTE_REQUEST: 6,
}
POSITIVE_ORDER_STATUSES = (
    Order.Status.ACCEPTED,
    Order.Status.PROCESSING,
    Order.Status.SHIPPED,
    Order.Status.DELIVERED,
)


def hash_search_query(query):
    """Return a keyed digest; raw search phrases are never persisted."""
    secret = str(settings.SECRET_KEY).encode('utf-8')
    normalized = ' '.join(query.casefold().split()).encode('utf-8')
    return hmac.new(secret, normalized, hashlib.sha256).hexdigest()


def distance_km(origin, destination):
    """Approximate distance between PIN-code centroids using haversine."""
    latitude_delta = radians(float(destination.latitude) - float(origin.latitude))
    longitude_delta = radians(float(destination.longitude) - float(origin.longitude))
    origin_latitude = radians(float(origin.latitude))
    destination_latitude = radians(float(destination.latitude))
    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(origin_latitude)
        * cos(destination_latitude)
        * sin(longitude_delta / 2) ** 2
    )
    return 6371 * 2 * atan2(sqrt(haversine), sqrt(1 - haversine))


def _buyer_signals(user):
    """Build bounded, decayed affinity maps from recent first-party signals."""
    category_affinity = Counter()
    product_affinity = Counter()
    seller_affinity = Counter()
    price_history = []
    quantity_history = []
    if not user.is_authenticated:
        return (
            category_affinity,
            product_affinity,
            seller_affinity,
            price_history,
            quantity_history,
        )

    cutoff = timezone.now() - timedelta(days=180)
    interactions = ProductInteraction.objects.filter(
        user=user,
        created_at__gte=cutoff,
    ).select_related('product').only(
        'event_type', 'created_at', 'category', 'product_id',
        'product__category', 'product__owner_id',
    ).order_by('-created_at')[:2000]
    now = timezone.now()
    for event in interactions:
        weight = EVENT_WEIGHTS.get(event.event_type, 0)
        if not weight:
            continue
        age_days = max((now - event.created_at).days, 0)
        weight *= 0.5 ** (age_days / 60)
        category = event.category or (event.product.category if event.product_id else '')
        if category:
            category_affinity[category] += weight
        if event.product_id:
            product_affinity[event.product_id] += weight
            if event.product.owner_id:
                seller_affinity[event.product.owner_id] += weight

    # Completed commercial outcomes are stronger signals than browsing.
    order_rows = Order.objects.filter(
        buyer=user,
        status__in=POSITIVE_ORDER_STATUSES,
    ).values('product_id', 'product__category', 'seller_id').annotate(total=Count('id'))
    for row in order_rows:
        weight = min(row['total'], 5) * 8
        category_affinity[row['product__category']] += weight
        product_affinity[row['product_id']] += weight
        seller_affinity[row['seller_id']] += weight

    purchase_history = Order.objects.filter(
        buyer=user,
        status__in=POSITIVE_ORDER_STATUSES,
    ).values_list('product__price', 'quantity')
    for price, quantity in purchase_history:
        price_history.append(price)
        quantity_history.append(quantity)

    return (
        category_affinity,
        product_affinity,
        seller_affinity,
        price_history,
        quantity_history,
    )


def rank_products(products, user):
    """Rank a role-filtered product queryset and add human-readable reasons."""
    candidates = list(products.select_related('owner'))
    if not candidates:
        return candidates

    (
        category_affinity,
        product_affinity,
        seller_affinity,
        price_history,
        quantity_history,
    ) = _buyer_signals(user)
    candidate_ids = [item.pk for item in candidates]
    demand = dict(
        Order.objects.filter(
            product_id__in=candidate_ids,
            status__in=POSITIVE_ORDER_STATUSES,
        ).values('product_id').annotate(total=Count('id')).values_list('product_id', 'total')
    )
    views = dict(
        ProductInteraction.objects.filter(
            product_id__in=candidate_ids,
            event_type__in=[ProductInteraction.EventType.CLICK,
                            ProductInteraction.EventType.QUOTE_REQUEST],
            created_at__gte=timezone.now() - timedelta(days=90),
        ).values('product_id').annotate(total=Count('id')).values_list('product_id', 'total')
    )

    location_by_pincode = {}
    buyer_pincode = getattr(user, 'pincode', None) if user.is_authenticated else None
    if buyer_pincode:
        pincodes = {owner.pincode for owner in (item.owner for item in candidates)
                    if owner and owner.pincode}
        location_by_pincode = PincodeLocation.objects.in_bulk(pincodes, field_name='pincode')
        buyer_location = PincodeLocation.objects.filter(pincode=buyer_pincode).first()
    else:
        buyer_location = None

    usual_min_price = min(price_history) if price_history else None
    usual_max_price = max(price_history) if price_history else None
    usual_quantity = median(quantity_history) if quantity_history else None
    ranked = []
    for product in candidates:
        reasons = []
        category_score = min(category_affinity[product.category], 24) * 2.0
        product_score = min(product_affinity[product.pk], 24) * 1.5
        seller_score = min(seller_affinity[product.owner_id], 20) * 0.6
        demand_score = min(demand.get(product.pk, 0), 30) * 1.2
        engagement_score = min(views.get(product.pk, 0), 30) * 0.45
        rating_score = min(max(float(product.rating or 0), 0), 5) * 0.8
        target_quantity = max(product.minimum_order, usual_quantity or 0)
        if product.inventory >= target_quantity:
            stock_score = 5
            stock_reason = (
                'Available in your usual quantity range'
                if usual_quantity is not None
                else 'Available now'
            )
        elif product.inventory >= product.minimum_order:
            stock_score = 2
            stock_reason = 'Available now'
        else:
            stock_score = -100
            stock_reason = None
        featured_score = 2 if product.is_featured else 0
        location_score = 0

        if category_score:
            reasons.append('Matches categories you have explored or sourced')
        if product_score:
            reasons.append('Related to a product you viewed or enquired about')
        if seller_score:
            reasons.append('From a supplier you have worked with')
        if demand_score or engagement_score:
            reasons.append('Receiving orders or enquiries on GAB')
        price_score = 0
        if usual_min_price is not None:
            if (
                usual_min_price * Decimal('0.8')
                <= product.price
                <= usual_max_price * Decimal('1.2')
            ):
                price_score = 4
                reasons.append('Fits your usual price range')
        if stock_reason:
            reasons.append(stock_reason)
        if buyer_location and product.owner and product.owner.pincode:
            seller_location = location_by_pincode.get(product.owner.pincode)
            if seller_location:
                km = distance_km(buyer_location, seller_location)
                location_score = max(0, 8 - km / 100)
                product.recommendation_distance_km = round(km, 1)
                if km <= 100:
                    reasons.append('Supplier is in a nearby PIN-code area')
        if not reasons:
            reasons.append('A strong listing for your supply-chain level')

        score = (category_score + product_score + seller_score + demand_score
                 + engagement_score + rating_score + stock_score + featured_score
                 + location_score + price_score)
        reason_priority = {
            'Matches categories you have explored or sourced': 0,
            'Related to a product you viewed or enquired about': 1,
            'Fits your usual price range': 2,
            'Available in your usual quantity range': 3,
            'Available now': 3,
            'Supplier is in a nearby PIN-code area': 4,
            'From a supplier you have worked with': 5,
            'Receiving orders or enquiries on GAB': 6,
        }
        product.recommendation_reasons = sorted(
            reasons,
            key=lambda reason: reason_priority.get(reason, 10),
        )[:5]
        ranked.append((score, product.created_at, product))

    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [product for _, _, product in ranked]
