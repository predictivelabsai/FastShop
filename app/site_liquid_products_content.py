"""Canonical, editable product block documents transcribed from Helen's prototype."""

import json
from pathlib import Path

CONTENT = json.loads(Path(__file__).with_suffix(".json").read_text("utf-8"))
ASSETS = "/static/h24you/liquid/"


def item(theme, **values):
    return {"theme": theme, **values}


def photo(filename, alt):
    return item("gallery", image=ASSETS + filename, alt=alt)


def block(kind, identity, **values):
    return {"id": identity, "type": "liquid_product_" + kind, "version": 1, **values}


def product_documents():
    c = CONTENT
    bottle, tablet, range_ = (c[k] for k in ("bottleProduct", "tabletProduct", "rangeProduct"))
    flavors = c["products"]["tablets"]["flavors"]
    documents = []
    for mode, slug, p in [
        ("bottle", "h2-4-you-one", bottle),
        ("tablet", "hydrogen-tablets", tablet),
        ("range", "taste-the-range", range_),
    ]:
        name = p.get("name", c["products"]["tablets"]["name"])
        if mode == "bottle":
            gallery = [
                photo(
                    "studio-front.jpg",
                    "Silver H2 | 4 YOU One bottle, front view with crystal-pattern chamber",
                ),
                photo(
                    "studio-glow.jpg",
                    "Silver H2 | 4 YOU One bottle with illuminated blue hydrogen chamber",
                ),
                photo(
                    "held-out-face-hidden.jpg",
                    "Woman holding the silver hydrogen water bottle towards the camera",
                ),
                photo(
                    "bottle-desk.jpg",
                    "PLACEHOLDER – silver hydrogen water bottle on a light wooden desk beside a laptop and glass of water",
                ),
                photo(
                    "bottle-bag.jpg",
                    "PLACEHOLDER – silver hydrogen water bottle in an open canvas tote with a towel",
                ),
            ]
        elif mode == "tablet":
            gallery = [
                photo(
                    "tablet-box-unflavoured.jpg",
                    "PLACEHOLDER – pearl Hydrogen Tablets box, Unflavored, with a light blue accent",
                ),
                photo(
                    "tablet-flavour-unflavoured.jpg",
                    "PLACEHOLDER – Unflavored tablet box beside a fizzing glass of water",
                ),
                photo(
                    "tablet-fizz.jpg",
                    "PLACEHOLDER – white effervescent tablets beside a glass of water with one tablet fizzing",
                ),
            ]
            gallery += [
                photo(
                    "tablet-box-" + f["id"] + ".jpg",
                    "PLACEHOLDER – Hydrogen Tablets box, " + f["label"],
                )
                for f in flavors[1:]
            ]
        else:
            gallery = [
                photo(
                    "range-row.jpg",
                    "PLACEHOLDER – the four Hydrogen Tablets boxes, Unflavored, Raspberry, Pineapple and Peach, standing in a row",
                ),
                photo(
                    "range-glasses.jpg",
                    "PLACEHOLDER – the four flavor boxes behind four glasses of sparkling water",
                ),
                photo(
                    "range-flatlay.jpg",
                    "PLACEHOLDER – flat-lay of the four flavor boxes with tablets, raspberries, pineapple and peach slices",
                ),
            ]
            gallery += [
                photo(
                    (
                        "tablet-box-raspberry.jpg"
                        if f["id"] == "raspberry"
                        else "range-box-" + f["id"].replace("unflavored", "unflavoured") + ".jpg"
                    ),
                    "PLACEHOLDER – Hydrogen Tablets box, " + f["label"],
                )
                for f in flavors
            ]
        items = gallery + [item("trust", label=s) for s in p["trust"]]
        items += [
            item("box", body=s) for s in (p["box"] if isinstance(p["box"], list) else [p["box"]])
        ]
        reviews = c[mode + "Reviews"]
        items += [
            item(
                "rating",
                value=str(sum(r["rating"] for r in reviews) / len(reviews)),
                label=str(len(reviews)),
            )
        ]
        if mode == "bottle":
            items += [item("spec", label=s["label"], value=s["value"]) for s in bottle["specs"]]
            items += [
                item("bundle", label=s["label"], value=str(s["quantity"]), body=str(s["discount"]))
                for s in bottle["bundles"]
            ]
            items += [item("color", label=bottle["color"])]
        else:
            accents = {
                "unflavored": "a light blue",
                "raspberry": "a soft berry-pink",
                "pineapple": "a soft yellow",
                "peach": "a soft apricot",
            }
            card_alts = {
                "unflavored": "Unflavored tablet box beside a fizzing glass of water",
                "raspberry": "Raspberry tablet box beside a glass of water and raspberries",
                "pineapple": "Pineapple tablet box beside a glass of water and pineapple pieces",
                "peach": "Peach tablet box beside a glass of water and a peach slice",
            }
            items += [
                item(
                    "flavor",
                    label=f["label"],
                    value=f["id"],
                    image=ASSETS
                    + "tablet-box-"
                    + f["id"].replace("unflavored", "unflavoured")
                    + ".jpg",
                    url=ASSETS
                    + "tablet-flavour-"
                    + f["id"].replace("unflavored", "unflavoured")
                    + ".jpg",
                    alt="PLACEHOLDER – pearl Hydrogen Tablets box, "
                    + f["label"]
                    + ", with "
                    + accents[f["id"]]
                    + " accent",
                    body="PLACEHOLDER – " + card_alts[f["id"]],
                )
                for f in flavors
            ]
            items += [item("quality", label=s["label"], value=s["id"]) for s in tablet["seals"]]
            items += [
                item("fact", label=s["label"], value=s["amount"], body=s["dv"])
                for s in tablet["supplementFacts"]
            ]
            items += [
                item("facts-lead", body=tablet["factsLead"]),
                item(
                    "facts-placeholder",
                    body="PLACEHOLDER – to be confirmed against the final label",
                ),
                item("quality-title", body=tablet["qualityTitle"]),
            ]
            items += [
                item(
                    "ingredient",
                    value=f["id"],
                    body=tablet["ingredientsUnflavored"]
                    if f["id"] == "unflavored"
                    else tablet["ingredientsFlavored"][f["id"]],
                )
                for f in flavors
            ]
            items += [
                item(
                    "range-link",
                    label=tablet["rangeLink"]["lead"],
                    body=tablet["rangeLink"]["text"],
                    url=tablet["rangeLink"]["href"],
                )
            ]
            if mode == "tablet":
                items += [
                    item(
                        "subscription",
                        heading=tablet["subscription"]["title"],
                        label=tablet["subscription"]["badge"],
                        body="\n".join(tablet["subscription"]["points"]),
                    ),
                    item("one-time", label=tablet["oneTimeTitle"]),
                    item("mix-hint", body=tablet["mixHint"]),
                ]
            else:
                items += [
                    item("pack", label=range_["packLine"], body=range_["badge"]),
                    item(
                        "subscribe-link",
                        body=range_["subscribeLink"],
                        url="/products/hydrogen-tablets",
                    ),
                    item("facts-note", body=range_["factsNote"]),
                    item("ingredient-range", body=tablet["ingredientsRange"]),
                ]
        blocks = [
            block(
                "purchase",
                "purchase",
                layout=mode,
                heading=name,
                eyebrow=p.get("subtitle", p.get("label", "")),
                body=p["description"],
                url="/products/" + slug,
                items=items,
            )
        ]

        def steps(is_bottle=False, title=None, layout="dark"):
            source = bottle if is_bottle else tablet
            filenames = ["bottle-fill.jpg", "bottle-press-close.jpg", "bottle-drink.jpg"]
            return block(
                "steps",
                "how-it-works",
                layout=("bottle" if is_bottle else "tablet") + "-" + layout,
                heading=title or source["stepsTitle"],
                body="" if is_bottle else tablet["chemistry"],
                button="" if is_bottle else tablet["learnLink"],
                link="/learn",
                items=[
                    item(
                        "step",
                        heading=s["title"],
                        body=s["text"],
                        image=ASSETS + filenames[i] if is_bottle else "",
                        alt="PLACEHOLDER – " + s["title"],
                    )
                    for i, s in enumerate(source["steps"])
                ],
            )

        if mode == "bottle":
            blocks += [
                steps(True),
                block(
                    "technology",
                    "technology",
                    heading=bottle["technologyTitle"],
                    body=bottle["technologyNote"],
                    image=ASSETS + "studio-glow.jpg",
                    alt="Silver hydrogen bottle showing the blue-lit crystal-pattern chamber",
                    layout="light",
                    items=[
                        item("technology", heading=s["title"], body=s["explanation"])
                        for s in bottle["technology"]
                    ],
                ),
                block(
                    "day",
                    "day",
                    heading=bottle["dayTitle"],
                    layout="dark",
                    items=[
                        item(
                            "day-stat" if "number" in s else "day",
                            heading=s["title"],
                            body=s["text"],
                            label=s["time"],
                            image=ASSETS
                            + "bottle-day-"
                            + ["morning", "desk", "gym", "evening"][i]
                            + ".jpg",
                            alt="PLACEHOLDER – silver H2 | 4 YOU One "
                            + [
                                "in morning light",
                                "on a desk",
                                "in an open gym bag",
                                "on a bedside table",
                            ][i],
                            value=s.get("number", ""),
                            url="",
                        )
                        for i, s in enumerate(bottle["day"])
                    ]
                    + [
                        item(
                            "day-source",
                            label=bottle["day"][-1]["label"],
                            body=bottle["day"][-1]["source"],
                        )
                    ],
                ),
                block(
                    "lifestyle",
                    "lifestyle",
                    heading=bottle["lifestyleTitle"],
                    body=bottle["lifestyleText"],
                    image=ASSETS + "lifestyle-sofa.jpg",
                    alt="Woman relaxing on a sofa holding the silver H2 | 4 YOU One bottle",
                    layout="light",
                ),
            ]
        else:
            fs = block(
                "flavors",
                "flavors",
                heading=p["flavorsTitle"],
                body=range_["flavorsLine"] if mode == "range" else "",
                layout="range-dark" if mode == "range" else "tablet-light",
                items=[
                    item(
                        "flavor",
                        heading=f["label"],
                        body=tablet["flavorLines"][f["id"]],
                        value=f["id"],
                        image=ASSETS
                        + (
                            (
                                "range-flavour-"
                                if mode == "range" and f["id"] != "raspberry"
                                else "tablet-flavour-"
                            )
                            + f["id"].replace("unflavored", "unflavoured")
                            + ".jpg"
                        ),
                        alt="PLACEHOLDER – " + f["label"] + " tablet box beside a glass of water",
                    )
                    for f in flavors
                ]
                + (
                    []
                    if mode == "range"
                    else [
                        item(
                            "range-link",
                            label=tablet["rangeLink"]["lead"],
                            body=tablet["rangeLink"]["text"],
                            url=tablet["rangeLink"]["href"],
                        )
                    ]
                ),
            )
            blocks += (
                [fs, steps(title=range_["stepsTitle"], layout="light")]
                if mode == "range"
                else [steps(), fs]
            )
            if mode == "tablet":
                blocks += [
                    block(
                        "benefits",
                        "benefits",
                        heading=tablet["benefitsTitle"],
                        body=c["fdaDisclaimer"],
                        image=ASSETS + "metal-water-hero.jpg",
                        alt="A glass of fizzing hydrogen water",
                        layout="dark",
                        items=[
                            item(
                                s["icon"],
                                heading=s["title"],
                                body=s["text"],
                                url="/science#" + s["anchor"] if s["anchor"] else "",
                                label=tablet["researchLink"],
                            )
                            for s in tablet["benefits"]
                        ]
                        + [
                            item(
                                "research",
                                heading=tablet["research"]["title"],
                                body=tablet["research"]["text"],
                                value=tablet["research"]["number"],
                                label=tablet["research"]["label"],
                                alt=tablet["research"]["source"],
                                url="/science",
                            ),
                            item("research-cta", label=tablet["research"]["cta"]),
                        ],
                    ),
                    block(
                        "lifestyle",
                        "share",
                        heading=tablet["shareTitle"],
                        body=tablet["shareText"],
                        image=ASSETS + "tablet-share.jpg",
                        alt="PLACEHOLDER – friends and family around a kitchen table with glasses of water and a box of tablets",
                        button=tablet["shareLink"],
                        link="/products/h2-4-you-one",
                        layout="light",
                    ),
                ]
        blocks += [
            {
                "id": "reviews",
                "type": "liquid_reviews",
                "version": 1,
                "heading": c["metalContent"]["reviewsTitle"],
                "items": [
                    {
                        "heading": r["headline"],
                        "body": r["text"],
                        "label": r["name"],
                        "value": str(r["rating"]),
                        "theme": r["location"],
                        "alt": r["product"],
                        "image": ASSETS + "review-avatar-" + str(i % 8 + 1) + ".jpg",
                    }
                    for i, r in enumerate(reviews)
                ],
            }
        ]
        blocks += [
            block(
                "faq",
                "faq",
                heading="Frequently asked questions",
                body=c["metalContent"]["faqContactIntro"],
                eyebrow=c["metalContent"]["contactEmail"],
                button="More questions? See all FAQs →",
                link="/faq",
                layout="light",
                items=[
                    item(
                        "faq",
                        heading=f["q"],
                        body=f["a"],
                        **(
                            {
                                "label": f["links"][0]["text"],
                                "url": f["links"][0]["to"]
                                + (
                                    "#" + f["links"][0]["hash"] if f["links"][0].get("hash") else ""
                                ),
                            }
                            if f.get("links")
                            else {}
                        ),
                    )
                    for f in p["faq"]
                ],
            )
        ]
        target = c["products"]["tablets"] if mode == "bottle" else c["products"]["bottle"]
        blocks += [
            block(
                "pair",
                "pair",
                heading=bottle["pairTitle"] if mode == "bottle" else tablet["pairTitle"],
                body=bottle["pairText"] if mode == "bottle" else tablet["pairText"],
                eyebrow=target.get("subtitle", bottle["subtitle"]),
                button="Add to cart",
                url="/products/" + ("hydrogen-tablets" if mode == "bottle" else "h2-4-you-one"),
                image=ASSETS + ("tablets.jpg" if mode == "bottle" else "metal-bottle-square.jpg"),
                alt="Hydrogen tablet fizzing in a glass of water"
                if mode == "bottle"
                else "The H2 | 4 YOU silver hydrogen water bottle held in a person's hands",
                layout="tablet-dark" if mode == "bottle" else "bottle-dark",
                items=[
                    item(
                        "product",
                        heading=target["name"] if mode == "bottle" else bottle["name"],
                        body=target["blurb"],
                    )
                ]
                + (
                    [item("flavor", label=f["label"], value=f["id"]) for f in flavors]
                    if mode == "bottle"
                    else [
                        item("spec", label=s["label"], value=s["value"])
                        for s in c["metalContent"]["bottleSpecs"]
                    ]
                ),
            )
        ]
        documents.append(("/products/" + slug, name, "content", blocks))
    return documents
