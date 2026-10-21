"""Liquid editorial components, rendered solely from canonical page block entries."""

import re
from html import escape

from fasthtml.common import (
    H1,
    H2,
    H3,
    H4,
    A,
    Article,
    Button,
    Details,
    Div,
    Em,
    Figcaption,
    Figure,
    Img,
    Li,
    Nav,
    NotStr,
    Ol,
    P,
    Section,
    Span,
    Strong,
    Summary,
    Ul,
    Video,
)

from app.site_liquid_editorial_content import editorial_data


def link(href, base):
    return base.rstrip("/") + href if href.startswith("/") and not href.startswith("//") else href


def names(text):
    return [Em(p, cls="bottle-one font-accent") if p in {"One", "Fizz"} else p
            for p in re.split(r"\b(One|Fizz)\b", text)]


def svg(body, view="0 0 24 24", **attrs):
    # Only application-owned drawing instructions enter the raw SVG fragment.
    return NotStr(f'<svg viewBox="{view}" ' + " ".join(
        f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items()) + f'>{body}</svg>')


ICONS = {
    "small": '<circle cx="12" cy="12" r="2.5"/><circle cx="12" cy="12" r="8" stroke-dasharray="2 3"/>',
    "you": '<path d="M12 21s-7-4.5-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 5.5-7 10-7 10Z"/>',
    "field": '<path d="M4 20V10M10 20V6M16 20v-8M22 20H2"/>',
    "bottle": '<path d="M10 2h4v3h-4zM9 5h6l1 3v13a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V8zM8 13h8"/>',
    "power": '<path d="M13 2 5 13h6l-1 9 8-11h-6z"/>',
    "glass": '<path d="M5 3h14l-2 18H7zM6 9h12"/>',
    "tablet": '<path d="M5 3h14l-2 18H7zM6 9h12"/><circle cx="12" cy="-1.6" r="2.5"/>',
    "fresh": '<path d="M6 4h12l-1.5 16h-9Z"/><circle cx="11" cy="14" r="1.2"/><circle cx="13.5" cy="10" r="1"/>',
    "soon": '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M10 3h4"/>',
    "closed": '<rect x="8" y="2.5" width="8" height="3.5" rx="1"/><path d="M8.5 6h7v14a1.5 1.5 0 0 1-1.5 1.5h-4A1.5 1.5 0 0 1 8.5 20Z"/>',
    "cool": '<path d="M12 2v20M4 7l16 10M20 7 4 17M9 3.5 12 6l3-2.5M9 20.5 12 18l3 2.5"/>',
}
ICONS["bottleBubbles"] = ICONS["bottle"] + '<circle cx="11" cy="18" r="1.1"/><circle cx="13.2" cy="14.6" r=".9"/><circle cx="11.4" cy="11.4" r=".7"/>'
ICONS["glassBubbles"] = ICONS["glass"] + '<circle cx="10.6" cy="17.4" r="1.1"/><circle cx="13.4" cy="13.4" r=".9"/><circle cx="11" cy="9.4" r=".7"/>'


def chapter(c, number, identity, *children, light=False, extra="", science=False):
    return Section(Div(P(number, cls="why-numeral", aria_hidden="true"),
                       H2(c["title"], cls="font-display"),
                       P(c.get("intro", c.get("text", "")), cls="why-lead"),
                       *children, cls="why-chapter-inner"),
                   id=identity, cls="why-page why-chapter " + extra
                   + (" metal-light" if light else "")
                   + (" why-science" if science else ""))


def hero(c, base, science=False, block=None):
    bubbles = Div(*[Span(style=f"left:{i*37%100}%;width:{4+i%4*3}px;height:{4+i%4*3}px;"
                               f"animation-duration:{14+i%5*4}s;animation-delay:-{i*3.1%18}s")
                    for i in range(22)], cls="why-bubbles")
    words = c["h1"].rsplit(" ", 1)
    heading = c["h1"] if science else [words[0]+" ", Span(words[-1], cls="why-hero-digits")]
    media = bubbles
    block = block or {}
    if block.get("video") and block.get("poster"):
        media = [Img(src=block["poster"], alt="", cls="why-hero-poster"),
                 Video(src=block["video"], poster=block["poster"], muted=True,
                       loop=True, playsinline=True, data_editorial_video="true"),
                 Div(cls="why-hero-overlay")]
    elif block.get("image"):
        media = [Img(src=block["image"], alt=block.get("alt", ""), cls="why-hero-poster"),
                 Div(cls="why-hero-overlay")]
    index = None if science else Nav(Ol(*[Li(A(
        Span(ch["number"], cls="why-index-num"), " ", ch["short"], href="#"+ch["id"]))
        for ch in c["chapters"]]), cls="why-index", aria_label="Chapters",
        data_light="true", data_visible="false")
    media_nodes = media if isinstance(media, list) else [media]
    return Div(Section(Div(*media_nodes, cls="why-hero-media", aria_hidden="true"),
        Div(P(c["label"], cls="why-label"), H1(*([heading] if science else heading),
            cls="font-display"), P(c["intro"], cls="why-intro"),
            A(c["scroll"], href="#myth-or-fact" if science else "#what-is-hydrogen",
              cls="why-scroll"), cls="metal-container why-hero-inner"),
        cls="why-hero" + (" metal-light why-science-hero" if science else "")), index,
        cls="why-page" + (" why-science" if science else ""), data_editorial_start="true")


def molecules(c):
    h = '<defs><linearGradient id="why-h" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#14A5EE"/><stop offset="1" stop-color="#0A3DC7"/></linearGradient></defs><g class="why-float"><line x1="75" y1="60" x2="125" y2="60" class="why-bond"/><circle cx="72" cy="60" r="26" fill="url(#why-h)"/><circle cx="128" cy="60" r="26" fill="url(#why-h)"/></g>'
    water = '<line x1="100" y1="52" x2="58" y2="88" class="why-bond why-bond-gray"/><line x1="100" y1="52" x2="142" y2="88" class="why-bond why-bond-gray"/><circle cx="100" cy="52" r="36" class="why-gray-o"/><circle cx="56" cy="90" r="18" class="why-gray-h"/><circle cx="144" cy="90" r="18" class="why-gray-h"/>'
    return Div(Figure(svg(h, "0 0 200 120", role="img", aria_label="Two hydrogen atoms joined together"),
                      Figcaption(c["h2Caption"])),
               Figure(svg(water, "0 0 200 120", role="img", aria_label="A water molecule: one oxygen atom bound to two hydrogen atoms"),
                      Figcaption(c["waterCaption"])), cls="why-molecules")


def basics_one(c):
    return chapter(c, "01", "what-is-hydrogen", molecules(c), P(c["note"], cls="why-note"),
        Ul(*[Li(Span(svg(ICONS[x["id"]]), cls="why-card-icon", aria_hidden="true"),
                H3(x["title"]), P(*([x["text"].split("Nature Medicine")[0], Em("Nature Medicine"), x["text"].split("Nature Medicine")[1]] if "Nature Medicine" in x["text"] else [x["text"]]))) for x in c["cards"]], cls="why-cards"),
        Div(*[Details(Summary(Span(x["q"]), Span("Learn more ", Span("+", aria_hidden="true"),
                         cls="why-more-cue")), Div(P(x["a"]), cls="why-more-body"),
                     cls="why-more-row") for x in c["more"]], cls="why-more"), light=True)


def balance(c):
    body = '<defs><radialGradient id="why-glow"><stop offset="0" stop-color="#14A5EE" stop-opacity=".9"/><stop offset="1" stop-color="#0A3DC7" stop-opacity=".6"/></radialGradient></defs><path d="M160 70V262 M120 262H200"/><circle cx="160" cy="62" r="6"/><g class="why-beam" style="transform-origin:160px 70px"><line x1="50" y1="70" x2="270" y2="70"/></g>'
    for x, side in ((50, "l"), (270, "r")):
        body += f'<g class="why-pan" data-side="{side}"><line x1="{x}" y1="70" x2="{x-38}" y2="140"/><line x1="{x}" y1="70" x2="{x+38}" y2="140"/><path d="M{x-48} 140Q{x} 162 {x+48} 140Z"/>'
        if side == "l":
            body += ''.join(f'<circle cx="{x+ox}" cy="{oy+70}" r="3.2" class="why-dot"/>' for ox,oy in [(-20,64),(-6,60),(10,65),(22,61)])
            body += '<g class="why-drop why-extra-dots" data-on="false">'+''.join(f'<circle cx="{x+ox}" cy="{138-i%2*5}" r="3.2" class="why-dot"/>' for i,ox in enumerate([-30,-15,0,15,30]))+'</g>'
            body += '<g class="why-drop why-everyday" data-on="false"><circle cx="24" cy="120" r="5"/><path d="M24 111v-3M24 129v3M15 120h-3M33 120h3M40 122h14M40 118v8M54 118v8M62 128v-10h5v-5h5v15M84 116a6 6 0 1 0 4 10a5 5 0 0 1-4-10z"/></g>'
        else:
            body += '<g class="why-defenses"><rect x="244" y="122" width="12" height="12" rx="2"/><path d="M264 134l7-13 7 13z"/><circle cx="290" cy="128" r="6"/></g><g class="why-h2drop" data-on="false"><circle cx="306" cy="126" r="9"/><text x="306" y="129" text-anchor="middle">H₂</text></g>'
        label = escape(c["left"] if side == "l" else c["right"])
        body += f'<text x="{x}" y="182" text-anchor="middle" class="why-pan-label">{label}</text></g>'
    # Labels are FastHTML text, so edited content is escaped normally.
    return Div(svg(body, "0 0 320 300", role="img", aria_label="Balance scale",
                   data_stage="0", **{"class": "why-balance"}),
               cls="why-story-visual")


def basics_two(c):
    return chapter(c, "02", "free-radicals", Div(balance(c), Ol(*[
        Li(H3(s["title"]), P(s["text"]), data_stage=str(i), data_active=str(i == 0).lower())
        for i, s in enumerate(c["steps"])], cls="why-steps"), cls="why-story"),
        P(c["closing"], cls="why-closing why-story-ending", data_visible="true"))


def flow_icon(identity):
    if identity == "balance":
        body = '<path d="M160 70V262M120 262H200"/><circle cx="160" cy="62" r="6"/><line x1="50" y1="70" x2="270" y2="70"/>'
        for x in (50, 270):
            body += f'<line x1="{x}" y1="70" x2="{x-38}" y2="140"/><line x1="{x}" y1="70" x2="{x+38}" y2="140"/><path d="M{x-48} 140Q{x} 162 {x+48} 140Z"/>'
        body += '<circle cx="30" cy="134" r="3.2" class="why-dot"/><circle cx="44" cy="130" r="3.2" class="why-dot"/><circle cx="60" cy="135" r="3.2" class="why-dot"/><circle cx="72" cy="131" r="3.2" class="why-dot"/><rect x="244" y="122" width="12" height="12" rx="2"/><path d="M264 134l7-13 7 13z"/><circle cx="290" cy="128" r="6"/>'
        return Div(svg(body, "0 0 320 300", **{"class": "why-balance is-small"}), cls="why-flow-balance")
    bodies = {
        "h2": '<circle cx="32" cy="32" r="14" class="why-flow-bubble"/><text x="32" y="36" text-anchor="middle">H₂</text>',
        "cell": '<circle cx="32" cy="32" r="24" class="why-flow-cell"/><circle cx="32" cy="32" r="7" class="why-flow-line"/>',
        "defenses": '<circle cx="32" cy="32" r="24" class="why-flow-cell"/>'+''.join(f'<circle cx="{x}" cy="{y}" r="4" class="why-flow-glow"/>' for x,y in [(22,26),(40,24),(30,40),(42,40)]),
        "balance": '<path d="M32 10v45M20 55h24M6 17h52M10 17 4 40h16L10 17M54 17 44 40h16L54 17" fill="none" stroke="#0A3DC7"/>',
    }
    return svg(bodies[identity], "0 0 64 64")


def basics_three(c):
    return chapter(c, "03", "what-hydrogen-does", Ol(*[
        Li(Span(str(i+1), cls="why-idea-num"), H3(x["title"]), P(x["text"]))
        for i,x in enumerate(c["ideas"])], cls="why-ideas"),
        Ol(*[Li(Span(flow_icon(x["id"]), cls="why-flow-icon", aria_hidden="true"),
                Strong(x["label"]), Span(x["caption"], cls="why-flow-caption"))
             for x in c["flow"]], cls="why-flow", aria_label="How hydrogen may work"),
        P(c["closing"], cls="why-closing"), light=True)


def fresh_glass(identity):
    dots = {"fresh": [(30,96),(42,88),(54,98),(64,86),(36,76),(50,70),(60,62),(34,56),(46,50),(58,44),(40,34),(52,30),(28,66),(66,74)],
            "while": [(32,96),(46,90),(58,98),(40,74),(56,70),(48,56),(62,82)],
            "later": [(38,98),(54,96),(46,88)]}
    return svg('<path class="why-fresh-water" d="M15 24h62l-6.5 82h-49Z"/><path class="why-fresh-outline" d="M10 16h72l-8 92H18Z"/>'+''.join(f'<circle cx="{x}" cy="{y}" r="2.6"/>' for x,y in dots[identity]),
               "0 0 92 112", **{"class": "why-fresh-glass", "aria-hidden": "true"})


def fresh(c, base):
    def pct(n):
        return str(float(n)/50)+"%"
    scale = Div(Div(Span(cls="why-ppb-line", aria_hidden="true"), *[
        Span(Span(f"{int(t):,} ppb"), cls="why-ppb-tick", style="--pos:"+pct(t))
        for t in c["ticks"]], *[Span(cls="why-ppb-mark why-ppb-"+m["kind"],
            style=f'--pos:{pct(m["from"])};--end:{pct(m.get("to",m["from"]))}',
            aria_hidden="true") for m in c["markers"]], cls="why-ppb-track"),
        Ul(*[Li(Span(svg('<path d="M12 3 3 7v6c0 5 9 9 9 9s9-4 9-9V7Z"/><path d="m8 12 3 3 5-6"/>'), cls="why-ppb-shield", aria_hidden="true") if m["id"] == "one" else None,
                Span(*names(m["label"])), cls="why-ppb-label why-ppb-label-"+m["id"],
                style="--pos:"+pct(m["from"])) for m in c["markers"]], cls="why-ppb-labels"),
        cls="why-ppb", data_in="true")
    return chapter(c, "04", "fresh", Div(Div(*[Figure(fresh_glass(g["id"]),
            Figcaption(g["label"])) for g in c["glasses"]], cls="why-fresh-glasses", data_in="true"),
        Div(*[Div(H3(b["title"]), P(*names(b["text"]))) for b in c["blocks"]],
            cls="why-fresh-text"), cls="why-fresh-split"),
        Div(P(c["ppbLabel"], cls="why-label"), H3(c["ppbTitle"], cls="why-fresh-ppb-title"),
            P(c["ppbText"][0], A(c["ppbText"][1], href=link(c["ppbLink"], base)), c["ppbText"][2],
              cls="why-fresh-ppb-text"), scale,
            Div(H4(c["amountTitle"], cls="why-ppb-amount-title"),
                P(c["amountText"], cls="why-ppb-amount-text"),
                Div(*[Div(P(x["label"], cls="why-ppb-card-label"),
                        P(*(["About ", Span("1.45", cls="why-ppb-num"), " mg per bottle"] if x["big"] == "About 1.45 mg per bottle" else [x["big"]]), cls="why-ppb-card-big"), P(*names(x["text"])),
                        cls="why-ppb-card") for x in c["amountCards"]], cls="why-ppb-cards"),
                cls="why-ppb-amount"),
            P(c["ppbClosing"], cls="why-closing why-story-ending", data_visible="true"),
            cls="why-fresh-ppb"),
        Ul(*[Li(Span(svg(ICONS[h["icon"]]), cls="why-habit-icon", aria_hidden="true"),
                Strong(h["title"]), Span(h["text"])) for h in c["habits"]], cls="why-habits"),
        extra="why-fresh")


def ways(c, base):
    cards = []
    for card in c["cards"]:
        steps = []
        for i, step in enumerate(card["steps"]):
            if i:
                steps.append(Li(svg('<path d="M2 17h17M15 13l4 4-4 4"/>', "0 0 22 34",
                                    **{"class": "why-way-arrow"}), aria_hidden="true"))
            identity = step["icon"]
            icon = svg(ICONS[identity], "0 -8 24 32" if identity == "tablet" else "0 0 24 24",
                       **({"class": "why-way-icon-tall"} if identity == "tablet" else {}))
            steps.append(Li(Span(icon, aria_hidden="true"), step["text"]))
        cards.append(Article(Div(Img(src=card["image"], alt=card["alt"], width=1024,
                                    height=1024, loading="lazy"), cls="why-way-photo"),
            P(card["label"], cls="why-way-label"), H3(*names(card["name"]), cls="why-way-name"),
            P("How it works", cls="why-way-sub"), Ol(*steps, cls="why-way-steps"),
            P(*names(card["text"]), cls="why-way-text"), P("Good for", cls="why-way-sub"),
            Ul(*[Li(svg('<path d="M5 12.5 10 17l9-10"/>'), Span(g)) for g in card["good"]],
               cls="why-way-good"),
            Div(P(card["price"], cls="why-way-price"), P(card["priceNote"], cls="why-way-note"),
                A(card["cta"], href=link(card["href"], base),
                  cls="metal-add-button why-way-cta bg-metal-sheen"), cls="why-way-foot"),
            cls="why-way"))
    return chapter(c, "05", "two-ways", Div(*cards, cls="why-ways-grid"),
                   P(*names(c["closing"]), cls="why-ways-closing"),
                   A(c["rangeLink"]["text"], href=link(c["rangeLink"]["href"], base),
                     cls="why-ways-range"), light=True, extra="why-ways")


def fact(card, base):
    target = card.get("link")
    if not target or target["text"] not in card["fact"]:
        return card["fact"]
    before, after = card["fact"].split(target["text"], 1)
    return [before, A(target["text"], href=link(target["href"], base)), after]


def myths(c, base):
    cards = c["cards"]
    def fact_p(card, cls):
        value = fact(card, base)
        return P(*(value if isinstance(value, list) else [value]), cls=cls)
    deck = Div(Div(*[Article(
        Div(P("Myth", cls="why-myth-tag"), P("“"+x["myth"]+"”", cls="why-myth-text font-display"),
            Button(c["reveal"], type="button", cls="why-myth-reveal bg-metal-sheen", data_reveal="true"),
            cls="why-myth-face why-myth-front"),
        Div(P("Fact", cls="why-fact-tag"), fact_p(x, "why-fact-text"),
            Button(c["next"], type="button", cls="why-myth-next", data_next="true"),
            cls="why-myth-face why-myth-back"),
        cls="why-myth-card", data_current=str(i == 0).lower(), data_flipped="false",
        aria_label=f"{i+1} of {len(cards)}", aria_roledescription="slide")
        for i,x in enumerate(cards)], cls="why-deck-stack"),
        Div(Button("←", type="button", cls="why-deck-arrow", aria_label="Previous myth", data_previous="true"),
            Div(Span(f"1 / {len(cards)}", cls="why-deck-count", aria_live="polite"),
                Span(*[Span(data_active=str(i == 0).lower()) for i in range(len(cards))],
                     cls="why-deck-dots", aria_hidden="true"), cls="why-deck-progress"),
            Button("→", type="button", cls="why-deck-arrow", aria_label="Next myth", data_next="true"),
            cls="why-deck-controls"),
        Details(Summary(c["listToggle"], cls="why-myth-list-toggle"),
                Div(Ol(*[Li(Div(P("Myth", cls="why-list-tag why-list-myth"),
                                    P("“"+x["myth"]+"”", cls="why-myth-list-q font-display")),
                            Div(P("Fact", cls="why-list-tag why-list-fact"),
                                fact_p(x, "why-myth-list-a"))) for x in cards]),
                    cls="why-myth-list-body"), cls="why-myth-list"),
        cls="why-deck", role="region", aria_roledescription="carousel", aria_label="Myths and facts")
    return chapter(c, "01", "myth-or-fact", deck,
        P(c["deeperBefore"], A(c["deeperLink"]["text"], href=c["deeperLink"]["href"],
                              target="_blank", rel="noopener noreferrer"), c["deeperAfter"],
          cls="why-deeper"), extra="why-myths", science=True)


def study(s):
    front = Div(P(s["area"], cls="why-study-area"), H3(s["result"], cls="why-study-result font-display"),
        P(s["line"], cls="why-study-line"),
        P(Span(*[Span() for _ in range(int(s["people"]))], cls="why-study-dots", aria_hidden="true"),
          s.get("peopleLabel", f'{s["people"]} people'), cls="why-study-people"),
        P(s["design"], cls="why-study-design"),
        Div(Span(s["citation"]), Span("Tap to read more ↻", cls="why-study-hint", aria_hidden="true"),
            cls="why-study-foot"), cls="why-study-face why-study-front")
    back = Div(P("What they did", cls="why-study-label"), P(s["did"]),
        P("What they found", cls="why-study-label"), P(s["found"]),
        P("Good to know", cls="why-study-label"), P(s["note"], cls="why-study-muted"),
        Div(A("Read the study ↗", href=s["href"], target="_blank", rel="noopener noreferrer"),
            Button("↻ Back", type="button", data_study_back="true"), cls="why-study-links"),
        cls="why-study-face why-study-back")
    return Div(Article(front, back, cls="why-study", data_flipped="false", tabindex="0",
                       aria_label=f'{s["area"]}: {s["result"]}. Press Enter to read more'),
               cls="why-study-cell", data_tags=" ".join(s["tags"]))


def research(c):
    seen = set()
    sources = []
    for s in c["studies"]:
        key = (s["citation"], s["title"], s["href"])
        if key in seen:
            continue
        seen.add(key)
        parts = s["citation"].split(" · ")
        sources.append(Li(Strong(parts[0]+", "+parts[-1]), " · ", s["title"], " · ",
                          Em(s["journal"]), " ", A("↗", href=s["href"], target="_blank",
                          rel="noopener noreferrer", aria_label="Open "+parts[0]+" "+parts[-1]+" study")))
    x = c["extraSources"]
    sources.extend([Li(x["fda"]["text"], " ", A("↗", href=x["fda"]["href"], target="_blank",
        rel="noopener noreferrer", aria_label="Open FDA GRAS Notice No. 520")),
        Li(x["deeper"][0], A(x["deeper"][1], href=x["deeperHref"], target="_blank",
                            rel="noopener noreferrer"), x["deeper"][2])])
    return chapter(c, "02", "research",
        Div(*[Div(svg('<path d="M8 6h32l-4 50H12Z"/><path d="M10 32h28" class="why-glass-line"/>'+''.join(f'<circle cx="{16+i%3*8}" cy="{46-i//3*9}" r="1.8"/>' for i in range(8 if col["id"] == "full" else 4 if col["id"] == "half" else 2)), "0 0 48 60", **{"class": "why-glass", "aria-hidden": "true"}),
            P(col["label"], cls="why-board-label"), H3(col["title"], cls="font-display"),
            Ul(*[Li(Strong(it["t"]), Span(it["d"])) for it in col["items"]]),
            cls="why-board-col") for col in c["board"]], cls="why-board"),
        P(c["boardNote"], cls="why-board-note"),
        Div(*[Button(f["label"], type="button", data_filter=f["id"],
                     aria_pressed=str(f["id"] == "all").lower(),
                     cls="bg-metal-sheen" if f["id"] == "all" else "") for f in c["filters"]],
            cls="why-chips", role="group", aria_label="Filter studies"),
        Div(*[study(s) for s in c["studies"]], cls="why-studies"),
        P(c["studiesNote"], cls="why-studies-note"),
        Details(Summary(Span(c["sourcesLabel"]), Span("+", aria_hidden="true")),
                Div(Div(Ul(*sources)), cls="why-sources-body"), cls="why-sources", id="sources"),
        light=True, extra="why-research", science=True)


def closing(c, base):
    cross = c["cross"][c["page"]]
    return Section(Div(P(c["label"], cls="why-label"), H2(c["heading"], cls="font-display"),
        P(*names(c["text"]), cls="why-final-text"),
        Div(A(c["primary"]["text"], href=link(c["primary"]["href"], base),
              cls="bg-metal-sheen why-final-btn"),
            A(c["secondary"]["text"], href=link(c["secondary"]["href"], base),
              cls="why-final-btn why-final-btn-outline"), cls="why-final-actions"),
        Div(A(cross["text"], href=link(cross["href"], base), cls="why-final-faq"),
            Span("·", cls="why-final-dot", aria_hidden="true"),
            A(c["faq"]["text"], href=link(c["faq"]["href"], base), cls="why-final-faq"),
            cls="why-final-links"), cls="why-final-inner"),
        cls="why-page why-final", aria_label="Closing")


def placeholder(c, base):
    return Section(Div(P(c["label"], cls="text-muted-foreground editorial-placeholder-label"),
        H1(c["heading"], cls="font-display"),
        P(c["text"], cls="text-muted-foreground editorial-placeholder-intro") if c.get("text") else None,
        *[Section(H2(b["title"], cls="font-display"), P(b["text"], cls="text-muted-foreground"),
                  id=b["anchor"], cls="editorial-placeholder-topic") for b in c.get("benefits", [])],
        P(c["disclaimer"], cls="editorial-disclaimer", data_editorial_disclaimer="true") if c.get("disclaimer") else None,
        A(c["back"], href=link(c["href"], base), cls="editorial-placeholder-back"),
        cls="metal-container metal-section editorial-placeholder-inner"),
        cls="metal-light editorial-placeholder", data_placeholder="true")


def render_editorial(block, base):
    if block.get("type") != "liquid_editorial":
        return None
    c = editorial_data(block)
    layout = block["layout"]
    renderers = {
        "hero": lambda: hero(c, base, block=block),
        "science-hero": lambda: hero(c, base, True, block),
        "basics-1": lambda: basics_one(c), "basics-2": lambda: basics_two(c),
        "basics-3": lambda: basics_three(c), "fresh": lambda: fresh(c, base),
        "ways": lambda: ways(c, base), "myths": lambda: myths(c, base),
        "research": lambda: research(c), "closing": lambda: closing(c, base),
        "disclaimer": lambda: P(c["text"], cls="editorial-disclaimer", data_editorial_disclaimer="true"),
        "learn": lambda: placeholder(c, base), "science": lambda: placeholder(c, base),
    }
    return renderers[layout]()
