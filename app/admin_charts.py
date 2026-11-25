"""Small server-rendered SVG charts for the merchant admin (no client charting library)."""
from html import escape

from fasthtml.common import NotStr


def _nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def line_chart(values, dates, *, previous=None, label="Sales", format_value=str, height=220):
    """A Shopify-style line chart: soft gridlines, y-axis labels, start/middle/end dates and an
    optional dashed comparison line. Lines are SVG stretched to the card; labels are HTML so the
    text never distorts at any width."""
    values = list(values) or [0]
    previous = list(previous or [])
    peak = max(values + previous + [0])
    ceiling = _nice_ceiling(peak)
    steps = 3 if peak else 0

    def x(index, count):
        return index * 100 / max(1, count - 1) if count > 1 else 50

    def y(value):
        return 100 - (value / ceiling) * 100

    def path(series):
        return " ".join(f"{'M' if i == 0 else 'L'}{x(i, len(series)):.2f},{y(v):.2f}" for i, v in enumerate(series))

    lines = [f'<line x1="0" x2="100" y1="{y(ceiling * step / max(1, steps)):.2f}" '
             f'y2="{y(ceiling * step / max(1, steps)):.2f}" class="c-grid"/>' for step in range(steps + 1)]
    if previous:
        lines.append(f'<path d="{path(previous)}" class="c-previous"/>')
    line = path(values)
    lines.append(f'<path d="{line} L{x(len(values) - 1, len(values)):.2f},100 L{x(0, len(values)):.2f},100 Z" class="c-area"/>')
    lines.append(f'<path d="{line}" class="c-line"/>')
    y_labels = "".join(f'<span class="c-y" style="top:{y(ceiling * step / max(1, steps)):.2f}%">'
                       f'{escape(format_value(ceiling * step / max(1, steps)))}</span>' for step in range(steps + 1))
    x_labels = ""
    if dates:
        count = len(dates)
        for index in sorted({0, count // 2, count - 1}):
            edge = " is-start" if index == 0 else " is-end" if index == count - 1 else ""
            x_labels += f'<span class="c-x{edge}" style="left:{x(index, count):.2f}%">{escape(dates[index])}</span>'
    summary = f"{label}: {format_value(sum(values))} in total over {len(values)} day" + ("" if len(values) == 1 else "s")
    return NotStr(f'<div class="p-chart" role="img" aria-label="{escape(summary)}" style="--chart-h:{height}px">'
                  f'<div class="c-plot"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">'
                  f'{"".join(lines)}</svg>{y_labels}{x_labels}</div></div>')


def short_money(minor: float, currency: str) -> str:
    """Compact axis labels: $1.2k, €350."""
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    amount = minor / 100
    text = f"{amount / 1000:.1f}k" if amount >= 1000 else f"{amount:.0f}"
    return f"{symbol}{text}" if symbol else f"{text} {currency}"
