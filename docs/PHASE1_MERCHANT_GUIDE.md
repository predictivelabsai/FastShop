# Phase 1 merchant editing guide

Phase 1 is a design/content preview, not a transactional store. H2 4 You is
available at `/sites/h24you/` when deployed. Checkout, subscription billing,
customer accounts, tracking, discount issuance and marketing email capture are
not enabled by this work. Review the preview before starting Phase 2.

## Create and edit a site

1. Sign in as an authorized merchant and open `/admin/sites`.
2. Create a site with a name and unique URL slug. Its initial pages are private.
3. Open a page to edit its title, SEO description and content sections. Add a
   section using the type selector; use the ordering and remove controls to
   adjust the layout.
4. Save a draft and inspect the authorized preview. Saving a draft does not
   replace the published page.
5. Publish the page when ready. Publishing the first page makes the site's
   preview URL publicly accessible with noindex; this is not a private share link.
6. Use revision history to restore earlier content, then review and publish it.

If another editor saved changes first, reload before editing again. The version
check prevents silently overwriting their work.

## Shared content and products

Use site settings for brand details, navigation, contact details, the announcement
banner, social links, research figures and approved benefit statements. These
are shared values rather than text that needs replacing across many pages.

Use the product editor for product names, images, variant names and prices.
Prices are entered in minor currency units: USD 29.95 is **2995 cents**. A blank
price is not a zero-dollar selling price. Product page content is edited separately
from the underlying catalog record.

Upload JPEG, PNG or WebP images in the media library, supplying meaningful alt
text. Copy the resulting media URL into the appropriate section image field.
Uploads are optimized as WebP. Do not upload confidential material for use on a
public preview.

## Preview and launch checklist

- Replace the generated hero, product, lifestyle and video placeholders, and
  supply approved founder/partner photos. The video placeholders are animated
  still-image motion studies, not genuine dissolving or drinking footage.
- Supply the licensed Lovera font if required; the current heading fallback is
  Georgia. Quicksand is supplied locally with its font licence.
- Confirm the company address, product prices, pack size, Supplement Facts,
  ingredients, bottle specifications and shipping language.
- Review all three Learn drafts and their source links. Confirm the research
  counter values and source date before launch.
- Keep unapproved benefit statements disabled. Supply approved legal text and
  obtain the regulatory review requested by the brief.
- Replace social-link placeholders and do not publish sample reviews as genuine
  customer endorsements. The preview currently contains no customer testimonials.
- Configure and verify contact email delivery. Submissions are stored before
  delivery; inspect the contact inbox for failed or awaiting-configuration entries.
- Review desktop, tablet and mobile layouts, keyboard navigation, cookie
  preferences and video pause controls.

The Phase 1 preview is marked noindex. It is not proof of launch readiness or
completion of the payment, tax, shipping and subscription requirements. See
[the Phase 2 roadmap](PHASE2_COMMERCE_ROADMAP.md) for the remaining commerce work.
