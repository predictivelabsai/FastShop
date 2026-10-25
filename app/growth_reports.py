"""Site-owned merchant reporting from committed commerce records only."""

import csv
import io
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Channel, CommerceQuote, Order, Product, ProductVariant, SiteOrder
from app.order_management import REPORT_PAYMENT_STATES, report_dates, revenue_report
from app.services import CommerceError


def date_range(preset="30", start="", end="", *, today=None):
    today = today or datetime.now(UTC).date()
    if start or end or preset == "custom":
        return report_dates(start, end, today=today)
    if preset not in {"1", "7", "30", "90"}:
        raise CommerceError("Choose a valid date range.")
    return today - timedelta(days=int(preset) - 1), today


def aggregate(db, site, start: date, end: date):
    """Sales include shipping and tax; product revenue is the stored line total.

    Refunds are reported separately, in their own transaction period. Distinct
    buying customers with an earlier paid order (including earlier in this
    period) are returning customers. Foreign currencies are never added together.
    """
    summary = revenue_report(db, site, start, end)
    stop = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
    rows = list(db.execute(select(SiteOrder, Order, Channel.name, CommerceQuote)
        .join(Order, Order.id == SiteOrder.order_id)
        .join(Channel, Channel.id == Order.channel_id)
        .outerjoin(CommerceQuote, (CommerceQuote.id == SiteOrder.quote_id)
                   & (CommerceQuote.site_id == site.id)
                   & (CommerceQuote.tenant_id == site.tenant_id))
        .where(SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
               Order.tenant_id == site.tenant_id, Channel.tenant_id == site.tenant_id,
               Order.payment_status.in_(REPORT_PAYMENT_STATES), Order.created_at < stop)
        .options(selectinload(Order.lines)).order_by(Order.created_at, Order.id)))
    daily = {start + timedelta(days=n): 0 for n in range((end - start).days + 1)}
    variant_ids = {line.variant_id for _, order, _, _ in rows for line in order.lines
                   if line.variant_id}
    product_for_variant = {variant_id: (product_id, name) for variant_id, product_id, name
        in db.execute(select(ProductVariant.id, Product.id, Product.name).join(
            Product, Product.id == ProductVariant.product_id).where(
                ProductVariant.tenant_id == site.tenant_id, Product.tenant_id == site.tenant_id,
                ProductVariant.id.in_(variant_ids)))}
    products, channels, discounts = {}, defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0, 0])
    seen, buyers, returning = set(), set(), set()
    for link, order, channel, quote in rows:
        if order.currency.upper() != summary.currency:
            continue
        customer = link.customer_id
        day = order.created_at.date()
        if start <= day <= end:
            buyers.add(customer)
            if customer in seen:
                returning.add(customer)
            daily[day] += order.total_minor
            channels[channel][0] += order.total_minor
            channels[channel][1] += 1
            snapshot = quote.snapshot_json if quote else {}
            applied = snapshot.get("discounts") or snapshot.get("applied_discounts") or []
            if isinstance(applied, list) and applied:
                # Sales are attributed once to the complete combination used.
                label = " + ".join(str(item.get("code") or item.get("title") or
                                        item.get("name") or "Automatic discount")
                                   for item in applied if isinstance(item, dict))
            else:
                label = str(snapshot.get("discount_code") or snapshot.get("code") or
                            ("Discounted orders" if order.discount_minor else "No discount"))
            label = label or "Discounted orders"
            discounts[label][0] += order.total_minor
            shipping_saving = sum(item.get("amount_minor", 0) for item in applied
                if isinstance(item, dict) and item.get("type") == "free_shipping") if isinstance(applied, list) else 0
            discounts[label][1] += order.discount_minor + shipping_saving
            discounts[label][2] += 1
            for line in order.lines:
                # Variants roll up to their product. Manual/historical lines without
                # a catalogue reference retain their stored label as a fallback.
                identity = product_for_variant.get(line.variant_id)
                key = ("product", identity[0]) if identity else ("historical", line.product_name)
                name = identity[1] if identity else line.product_name
                product = products.setdefault(key, {"name": name, "revenue_minor": 0, "units": 0})
                product["revenue_minor"] += line.total_minor
                product["units"] += line.quantity
        seen.add(customer)
    return {"summary": summary, "daily": list(daily.items()),
            "top_products": sorted(products.values(), key=lambda p: (-p["revenue_minor"], p["name"])),
            "top_units": sorted(products.values(), key=lambda p: (-p["units"], p["name"])),
            "channels": sorted(channels.items()), "discounts": sorted(discounts.items()),
            "customers": len(buyers), "returning_customers": len(returning),
            "returning_rate": (len(returning) * 10000 // len(buyers)) if buyers else 0}


def report(db, site, start, end):
    current = aggregate(db, site, start, end)
    length = (end - start).days + 1
    current["previous"] = aggregate(db, site, start - timedelta(days=length),
                                     start - timedelta(days=1))
    return current


def report_csv(data):
    """A single portable CSV with named sections and explicit minor currency units."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)

    def safe(value):
        # Prevent spreadsheet formula execution for merchant-controlled names.
        return "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value

    def row(*values):
        writer.writerow([safe(value) for value in values])

    summary = data["summary"]
    row("report", "name", "value_minor_or_count", "currency", "start", "end")
    for key in ("gross_sales_minor", "refunds_minor", "net_revenue_minor", "order_count",
                "average_order_value_minor"):
        row("summary", key, getattr(summary, key), summary.currency,
            summary.start_date, summary.end_date)
    row("summary", "returning_customer_rate_basis_points", data["returning_rate"])
    for day, amount in data["daily"]:
        row("daily_sales", day, amount, summary.currency)
    for item in data["top_products"]:
        row("product_revenue", item["name"], item["revenue_minor"], summary.currency)
        row("product_units", item["name"], item["units"])
    for label, (amount, count) in data["channels"]:
        row("channel_sales", label, amount, summary.currency)
        row("channel_orders", label, count)
    for label, (amount, saving, count) in data["discounts"]:
        row("discount_sales", label, amount, summary.currency)
        row("discount_savings", label, saving, summary.currency)
        row("discount_orders", label, count)
    previous = data.get("previous")
    if previous:
        old = previous["summary"]
        for key in ("gross_sales_minor", "refunds_minor", "net_revenue_minor", "order_count",
                    "average_order_value_minor"):
            row("previous_period", key, getattr(old, key), old.currency, old.start_date, old.end_date)
    return buffer.getvalue()
