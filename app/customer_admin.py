"""Site-scoped merchant customer records, segments and portable CSV data."""

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.catalog_admin import _atomic as atomic
from app.customer_services import customer_for, normalized_email, orders_for, unsubscribe
from app.models import (
    Channel,
    CustomerAddress,
    CustomerComment,
    CustomerSegment,
    MarketingConsent,
    Order,
    PaymentTransaction,
    ShopCustomer,
    SiteOrder,
    User,
)
from app.services import CommerceError

FILTER_KEYS = {"q", "orders_min", "orders_max", "spent_min", "spent_max", "consent", "tag", "location", "sort"}
ADDRESS_FIELDS = ("name", "phone", "line1", "line2", "city", "state", "postal_code", "country")
ADDRESS_LIMITS = {"name": 160, "phone": 60, "line1": 240, "line2": 240,
                  "city": 120, "state": 120, "postal_code": 40, "country": 2}
CSV_COLUMNS = (
    "First Name", "Last Name", "Email", "Accepts Email Marketing", "Accepts SMS Marketing",
    "Default Address Address1", "Default Address Address2", "Default Address City",
    "Default Address Province", "Default Address Country Code", "Default Address Zip",
    "Default Address Phone", "Phone", "Tags", "Note", "Tax Exempt",
)


@dataclass
class CustomerRow:
    customer: ShopCustomer
    orders: int
    spent: int
    currency: str
    location: str
    email_consent: bool


def owned_customer(db, site, customer_id, *, lock=False):
    query = select(ShopCustomer).where(ShopCustomer.id == customer_id,
        ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.site_id == site.id)
    if lock:
        query = query.with_for_update()
    customer = db.scalar(query)
    if not customer:
        raise CommerceError("Customer not found.")
    return customer


def tags(value):
    values = value if isinstance(value, list) else str(value or "").split(",")
    result = list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))
    if len(result) > 50 or any(len(v) > 80 for v in result):
        raise CommerceError("Use up to 50 tags, each no longer than 80 characters.")
    return result


def text(value, limit, label):
    value = str(value or "").strip()
    if len(value) > limit or "\x00" in value:
        raise CommerceError(f"{label} must be no longer than {limit} characters.")
    return value


def addresses_for(db, site, customer):
    owned_customer(db, site, customer.id)
    return list(db.scalars(select(CustomerAddress).where(
        CustomerAddress.tenant_id == site.tenant_id, CustomerAddress.site_id == site.id,
        CustomerAddress.customer_id == customer.id).order_by(
            CustomerAddress.is_default.desc(), CustomerAddress.created_at, CustomerAddress.id)))


def email_consent_for(db, site):
    return {r.customer_id: r.status == "subscribed" for r in db.scalars(select(MarketingConsent)
        .where(MarketingConsent.tenant_id == site.tenant_id, MarketingConsent.site_id == site.id))}


def customer_rows(db, site, filters=None):
    filters = validate_filters(filters or {})
    currency = db.get(Channel, site.channel_id).currency
    customers = list(db.scalars(select(ShopCustomer).where(
        ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.site_id == site.id)))
    orders = list(db.execute(select(SiteOrder, Order).join(Order, SiteOrder.order_id == Order.id)
        .where(SiteOrder.tenant_id == site.tenant_id, SiteOrder.site_id == site.id,
               Order.tenant_id == site.tenant_id)))
    order_ids = [o.id for _, o in orders]
    refunded = {}
    if order_ids:
        for payment in db.scalars(select(PaymentTransaction).where(
                PaymentTransaction.order_id.in_(order_ids), PaymentTransaction.kind == "refund",
                PaymentTransaction.status == "succeeded", PaymentTransaction.currency == currency)):
            refunded[payment.order_id] = refunded.get(payment.order_id, 0) + payment.amount_minor
    stats = {}
    for link, order in orders:
        count, spent = stats.get(link.customer_id, (0, 0))
        paid = order.payment_status in {"paid", "partially_refunded", "refunded"}
        amount = max(0, order.total_minor - refunded.get(order.id, 0)) if paid and order.currency == currency else 0
        if order.payment_status == "refunded":
            amount = 0
        stats[link.customer_id] = (count + 1, spent + amount)
    locations = {}
    for address in db.scalars(select(CustomerAddress).where(
            CustomerAddress.tenant_id == site.tenant_id, CustomerAddress.site_id == site.id)
            .order_by(CustomerAddress.is_default, CustomerAddress.created_at)):
        locations[address.customer_id] = ", ".join(v for v in (address.city, address.country) if v)
    consent = email_consent_for(db, site)
    rows = []
    for customer in customers:
        count, spent = stats.get(customer.id, (0, 0))
        row = CustomerRow(customer, count, spent, currency, locations.get(customer.id, ""),
                          consent.get(customer.id, customer.email_marketing_consent))
        q = filters.get("q", "").casefold()
        if q and q not in " ".join((customer.name, customer.email, customer.phone)).casefold():
            continue
        if filters.get("tag") and filters["tag"] not in (customer.tags_json or []):
            continue
        if filters.get("location", "").casefold() not in row.location.casefold():
            continue
        if filters.get("consent") == "subscribed" and not row.email_consent:
            continue
        if filters.get("consent") == "not_subscribed" and row.email_consent:
            continue
        if any(filters.get(key) and not check(value, int(filters[key])) for key, value, check in (
            ("orders_min", count, lambda a, b: a >= b), ("orders_max", count, lambda a, b: a <= b),
            ("spent_min", spent, lambda a, b: a >= b), ("spent_max", spent, lambda a, b: a <= b))):
            continue
        rows.append(row)
    sort = filters.get("sort", "created")
    key = {"name": lambda r: (r.customer.name.casefold(), r.customer.id),
           "spent": lambda r: (-r.spent, r.customer.id),
           "orders": lambda r: (-r.orders, r.customer.id)}.get(sort,
           lambda r: (-r.customer.created_at.replace(tzinfo=UTC).timestamp(), r.customer.id))
    return sorted(rows, key=key)


def validate_filters(filters):
    result = {key: str(value)[:160] for key, value in filters.items() if key in FILTER_KEYS and value != ""}
    for key in ("orders_min", "orders_max", "spent_min", "spent_max"):
        if key in result:
            try:
                value = int(result[key])
            except ValueError as exc:
                raise CommerceError("Order and spend filters must be whole non-negative numbers.") from exc
            if value < 0 or value > 10**12:
                raise CommerceError("Order and spend filters must be whole non-negative numbers.")
    return result


@atomic
def save_customer(db, site, data, customer_id=None):
    email = normalized_email(data.get("email", ""))
    customer = owned_customer(db, site, customer_id, lock=True) if customer_id else customer_for(db, site, email, create=True)
    duplicate = customer_for(db, site, email)
    if duplicate and duplicate.id != customer.id:
        raise CommerceError("Another customer already uses this email address.")
    customer.email = email
    for field, limit in (("name", 160), ("phone", 60), ("note", 5000)):
        if field in data:
            setattr(customer, field, text(data[field], limit, field.title()))
    if "tags" in data:
        customer.tags_json = tags(data["tags"])
    if "account_state" in data:
        state = data["account_state"]
        if state not in {"active", "disabled", "invited"}:
            raise CommerceError("Choose a valid account state.")
        customer.account_state = state
        customer.is_active = state != "disabled"
    now = datetime.now(UTC)
    canonical = email_consent_for(db, site)
    for kind in ("email", "sms"):
        field = kind + "_marketing_consent"
        if field not in data:
            continue
        enabled = bool(data[field])
        current = canonical.get(customer.id, customer.email_marketing_consent) if kind == "email" else customer.sms_marketing_consent
        if enabled != current:
            setattr(customer, kind + "_consent_at", now)
            setattr(customer, kind + "_consent_source", "admin")
        setattr(customer, field, enabled)
        if kind == "email" and enabled != current:
            if not enabled:
                unsubscribe(db, site, customer)
            else:
                consent = db.scalar(select(MarketingConsent).where(
                    MarketingConsent.tenant_id == site.tenant_id, MarketingConsent.site_id == site.id,
                    MarketingConsent.customer_id == customer.id))
                if not consent:
                    consent = MarketingConsent(tenant_id=site.tenant_id, site_id=site.id, customer_id=customer.id)
                    db.add(consent)
                consent.status, consent.confirmed_at, consent.unsubscribed_at = "subscribed", now, None
                consent.consent_json = {"source": "admin", "confirmed_at": now.isoformat()}
    db.flush()
    return customer


@atomic
def save_address(db, site, customer_id, data, address_id=None, *, delete=False):
    customer = owned_customer(db, site, customer_id, lock=True)
    rows = addresses_for(db, site, customer)
    address = next((a for a in rows if a.id == address_id), None) if address_id else None
    if address_id and not address:
        raise CommerceError("Address not found.")
    if delete:
        if not address:
            raise CommerceError("Address not found.")
        was_default = address.is_default
        db.delete(address)
        remaining = [a for a in rows if a.id != address.id]
        if was_default and remaining:
            remaining[0].is_default = True
        db.flush()
        return None
    values = {key: text(data.get(key, ""), ADDRESS_LIMITS[key], key.replace("_", " ").title()) for key in ADDRESS_FIELDS}
    values["country"] = values["country"].upper()
    if not values["line1"] or not values["city"] or len(values["country"]) != 2 or not values["country"].isascii() or not values["country"].isalpha():
        raise CommerceError("Enter an address, city and two-letter country code.")
    if not address:
        address = CustomerAddress(tenant_id=site.tenant_id, site_id=site.id, customer_id=customer.id)
        db.add(address)
    for key, value in values.items():
        setattr(address, key, value)
    default = bool(data.get("is_default")) or not rows or bool(address.is_default)
    if default:
        for row in rows:
            row.is_default = False
    address.is_default = default
    db.flush()
    return address


@atomic
def add_comment(db, site, customer_id, actor_id, body):
    owned_customer(db, site, customer_id, lock=True)
    body = text(body, 5000, "Comment")
    if not body:
        raise CommerceError("Enter a comment.")
    comment = CustomerComment(tenant_id=site.tenant_id, site_id=site.id, customer_id=customer_id,
                              author_id=actor_id, body=body, internal=True)
    db.add(comment)
    db.flush()
    return comment


def timeline_for(db, site, customer):
    items = [(customer.created_at, "Customer created", customer.email)]
    items += [(order.created_at, "Order " + order.number, order.status.replace("_", " "))
              for _, order in orders_for(db, site, customer)]
    for comment, author in db.execute(select(CustomerComment, User).outerjoin(User, User.id == CustomerComment.author_id)
            .where(CustomerComment.tenant_id == site.tenant_id, CustomerComment.site_id == site.id,
                   CustomerComment.customer_id == customer.id)):
        items.append((comment.created_at, "Internal note" + (" · " + (author.name or author.email) if author else ""), comment.body))
    return sorted(items, key=lambda item: item[0].replace(tzinfo=UTC), reverse=True)


def segments_for(db, site):
    return list(db.scalars(select(CustomerSegment).where(CustomerSegment.tenant_id == site.tenant_id,
        CustomerSegment.site_id == site.id).order_by(CustomerSegment.name)))


@atomic
def save_segment(db, site, name, filters):
    name = text(name, 100, "Segment name")
    if not name:
        raise CommerceError("Enter a segment name.")
    row = db.scalar(select(CustomerSegment).where(CustomerSegment.tenant_id == site.tenant_id,
        CustomerSegment.site_id == site.id, CustomerSegment.name == name))
    if not row:
        row = CustomerSegment(tenant_id=site.tenant_id, site_id=site.id, name=name)
        db.add(row)
    row.filters_json = validate_filters(filters)
    db.flush()
    return row


@atomic
def bulk_tag(db, site, ids, value):
    additions = tags(value)
    if not additions or not ids or len(ids) > 500:
        raise CommerceError("Select customers and enter at least one tag.")
    customers = [owned_customer(db, site, key, lock=True) for key in sorted(set(ids))]
    for customer in customers:
        customer.tags_json = tags((customer.tags_json or []) + additions)
    db.flush()


def csv_cell(value):
    value = str(value or "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def export_csv(db, site, filters=None):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    for row in customer_rows(db, site, filters):
        customer = row.customer
        parts = customer.name.rsplit(" ", 1)
        first, last = (parts[0], parts[1]) if len(parts) == 2 else (customer.name, "")
        addresses = addresses_for(db, site, customer)
        address = addresses[0] if addresses else None
        values = [first, last, customer.email, "yes" if row.email_consent else "no",
                  "yes" if customer.sms_marketing_consent else "no"]
        values += [getattr(address, key, "") for key in ("line1", "line2", "city", "state", "country", "postal_code", "phone")]
        values += [customer.phone, ", ".join(customer.tags_json or []), customer.note, "no"]
        writer.writerow([csv_cell(value) for value in values])
    return output.getvalue()


def csv_bool(value):
    value = str(value).strip().lower()
    if value not in {"yes", "no", "true", "false", "1", "0", ""}:
        raise CommerceError("Consent must be yes or no.")
    return value in {"yes", "true", "1"}


@atomic
def import_csv(db, site, payload, *, apply=False):
    if len(payload) > 2 * 1024 * 1024:
        raise CommerceError("Choose a CSV file smaller than 2 MB.")
    try:
        source = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CommerceError("Choose a UTF-8 CSV file.") from exc
    reader = csv.DictReader(io.StringIO(source))
    if not reader.fieldnames or "Email" not in reader.fieldnames:
        raise CommerceError("The CSV needs an Email column.")
    results, seen = [], set()
    try:
        for number, row in enumerate(reader, 2):
            if number > 5001:
                raise CommerceError("Import up to 5,000 customers at a time.")
            result = {"row": number, "email": row.get("Email", ""), "error": "", "action": ""}
            try:
                email = normalized_email(row.get("Email", ""))
                if email in seen:
                    raise CommerceError("Duplicate email in this CSV.")
                seen.add(email)
                data = {"email": email}
                if "First Name" in row or "Last Name" in row:
                    data["name"] = text(" ".join((row.get("First Name", ""), row.get("Last Name", ""))).strip(), 160, "Name")
                for header, key, limit in (("Phone", "phone", 60), ("Note", "note", 5000)):
                    if header in row:
                        data[key] = text(row[header], limit, header)
                if "Tags" in row:
                    data["tags"] = tags(row["Tags"])
                for header, key in (("Accepts Email Marketing", "email_marketing_consent"),
                                    ("Accepts Marketing", "email_marketing_consent"),
                                    ("Accepts SMS Marketing", "sms_marketing_consent")):
                    if header in row:
                        data[key] = csv_bool(row[header])
                address = {}
                for key, header in (("line1", "Address1"), ("line2", "Address2"), ("city", "City"),
                                    ("state", "Province"), ("country", "Country Code"), ("postal_code", "Zip"), ("phone", "Phone")):
                    address[key] = row.get("Default Address " + header, row.get(header, "") if key != "phone" else "") or ""
                has_address = any(address[k] for k in ("line1", "line2", "city", "country", "postal_code"))
                if has_address:
                    for key, value in address.items():
                        address[key] = text(value, ADDRESS_LIMITS[key], "Address " + key)
                    if not address["line1"] or not address["city"] or len(address["country"]) != 2 or not address["country"].isascii() or not address["country"].isalpha():
                        raise CommerceError("Address needs Address1, City and a two-letter Country Code.")
                existing = customer_for(db, site, email)
                result["action"] = "Update" if existing else "Create"
                if apply:
                    with db.begin_nested():
                        customer = save_customer(db, site, data, existing.id if existing else None)
                        if has_address:
                            addresses = addresses_for(db, site, customer)
                            save_address(db, site, customer.id, {**address, "name": customer.name, "is_default": True}, addresses[0].id if addresses else None)
            except (CommerceError, TypeError) as exc:
                result["error"] = str(exc)
            results.append(result)
    except csv.Error as exc:
        raise CommerceError("The CSV could not be read. Check its quotation marks.") from exc
    return results
