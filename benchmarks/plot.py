"""Render recorded benchmark samples without rerunning the workload.

ReportLab is an optional maintenance dependency, never a core or benchmark dependency.
SVG and PDF share one drawing; macOS sips can rasterize that PDF to matching PNG.
All ranges summarize independent runs and do not imply confidence intervals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from reportlab.graphics import renderPDF, renderSVG
    from reportlab.graphics.charts.axes import YValueAxis
    from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, PolyLine, Rect, String
    from reportlab.lib.colors import HexColor
except ModuleNotFoundError as exc:
    REPORTLAB_IMPORT_ERROR = exc
else:
    REPORTLAB_IMPORT_ERROR = None


WIDTH, HEIGHT = 1120, 660
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
IMPLEMENTATIONS = ("raw", "runtime")
PALETTE = {"raw": "#0072B2", "runtime": "#D55E00"}
TEXT_COLOR = "#182B3A"
MUTED_COLOR = "#42576A"
GRID_COLOR = "#DAE2E8"
METRICS = {
    "first_event_p95": ("first_event_ns", "p95", 1e6, "ms"),
    "delivery_p95": ("delivery_ns", "p95", 1e6, "ms"),
    "unique_producer_events_per_second": ("producer_events_per_second", None, 1, "events/s"),
    "aggregate_delivered_frames_per_second": ("delivered_events_per_second", None, 1, "frames/s"),
}
SQLITE_SYNCHRONOUS_NAMES = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}


def _positive_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"Expected a finite positive metric; received {value!r}")
    return value


def _group(document, *, layer, implementation, persistence, connections):
    samples = sorted(
        (
            sample
            for sample in document["results"]
            if sample["layer"] == layer
            and sample["implementation"] == implementation
            and sample["persistence"] == persistence
            and sample["connections"] == connections
        ),
        key=lambda sample: sample["repeat"],
    )
    expected_repeats = list(range(1, document["workload"]["repeats"] + 1))
    expected_events = document["workload"]["http_events" if layer == "http" else "events"]
    if [sample["repeat"] for sample in samples] != expected_repeats:
        raise ValueError(f"Missing or duplicate repetitions for {layer}/{implementation}/{persistence}/{connections}")
    if any(sample["events"] != expected_events for sample in samples):
        raise ValueError("Sample event counts disagree with workload metadata")
    return samples


def _summary(samples, *, metric, unit, values, field):
    for value in values:
        _positive_number(value)
    first = samples[0]
    return {
        "metric": metric,
        "unit": unit,
        "sourceField": field,
        "layer": first["layer"],
        "implementation": first["implementation"],
        "persistence": first["persistence"],
        "readers": first["connections"],
        "eventsPerRun": first["events"],
        "repeats": [sample["repeat"] for sample in samples],
        "rawValues": values,
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def derive_series(document, input_bytes, input_name):
    """Keep the per-run values and the input byte hash alongside each displayed statistic."""
    if document["schema_version"] != 1:
        raise ValueError("Only benchmark schema_version 1 is supported")
    workload = document["workload"]
    readers = workload["connections"]
    if not readers or sorted(set(readers)) != readers or any(type(value) is not int or value <= 0 for value in readers):
        raise ValueError("workload.connections must be sorted, distinct positive integers")
    if type(workload["repeats"]) is not int or workload["repeats"] < 1:
        raise ValueError("workload.repeats must be a positive integer")
    sqlite_settings = [
        sample["raw"]["sqlite_settings"] for sample in document["results"] if sample["persistence"] == "sqlite"
    ]
    if not sqlite_settings or any(settings != sqlite_settings[0] for settings in sqlite_settings):
        raise ValueError("SQLite samples must record one consistent archive configuration")
    series = []
    for implementation in IMPLEMENTATIONS:
        for connections in readers:
            samples = _group(
                document, layer="http", implementation=implementation, persistence="sqlite", connections=connections
            )
            for metric, (field, percentile, divisor, unit) in METRICS.items():
                values = [
                    (sample["summary"][field][percentile] if percentile else sample["summary"][field]) / divisor
                    for sample in samples
                ]
                source_field = f"summary.{field}" + (f".{percentile} / {divisor:g}" if percentile else "")
                series.append(_summary(samples, metric=metric, unit=unit, values=values, field=source_field))
    for persistence in ("none", "sqlite"):
        for implementation in IMPLEMENTATIONS:
            samples = _group(
                document, layer="core", implementation=implementation, persistence=persistence, connections=1
            )
            series.append(
                _summary(
                    samples,
                    metric="core_producer_interval",
                    unit="ms",
                    values=[sample["raw"]["elapsed_ns"] / 1e6 for sample in samples],
                    field="raw.elapsed_ns / 1000000",
                )
            )
    return {
        "schemaVersion": 1,
        "inputFile": input_name,
        "sourceSHA256": hashlib.sha256(input_bytes).hexdigest(),
        "benchmarkSourceSHA256": document.get("source_sha256"),
        "recordedAtUTC": document["recorded_at_utc"],
        "sourceSampleCount": len(document["results"]),
        "workload": workload,
        "sqliteSettings": sqlite_settings[0],
        "rangeMeaning": "minimum and maximum of the per-run metric; not a confidence interval",
        "percentileMeaning": "per-run nearest-rank p95 from the source summary; no pooled percentile",
        "series": series,
    }


def _text(drawing, x, y, text, size=14, color=TEXT_COLOR, *, anchor="start", bold=False):
    drawing.add(
        String(
            x,
            y,
            text,
            fontName="Helvetica-Bold" if bold else "Helvetica",
            fontSize=size,
            fillColor=HexColor(color),
            textAnchor=anchor,
        )
    )


def _marker(drawing, x, y, implementation, radius=4.3):
    color = HexColor(PALETTE[implementation])
    if implementation == "raw":
        drawing.add(Circle(x, y, radius, fillColor=HexColor("#FFFFFF"), strokeColor=color, strokeWidth=1.8))
    else:
        drawing.add(
            Polygon(
                [x, y + radius + 0.8, x + radius + 0.8, y, x, y - radius - 0.8, x - radius - 0.8, y],
                fillColor=HexColor("#FFFFFF"),
                strokeColor=color,
                strokeWidth=1.8,
            )
        )


def _line(drawing, x1, y1, x2, y2, color, width=1, dash=None):
    drawing.add(Line(x1, y1, x2, y2, strokeColor=HexColor(color), strokeWidth=width, strokeDashArray=dash))


def _header(document, derived, title, subtitle, *, median_kind="line"):
    drawing = Drawing(WIDTH, HEIGHT)
    drawing.add(Rect(0, 0, WIDTH, HEIGHT, fillColor=HexColor("#FFFFFF"), strokeColor=None))
    _text(drawing, 55, 618, title, size=22, bold=True)
    _text(drawing, 55, 588, subtitle, size=15, color=MUTED_COLOR)
    for x, implementation in ((85, "raw"), (245, "runtime")):
        _line(
            drawing,
            x,
            548,
            x + 35,
            548,
            PALETTE[implementation],
            2.5,
            [7, 4] if implementation == "runtime" else None,
        )
        _marker(drawing, x + 17.5, 548, implementation)
        _text(drawing, x + 48, 543, implementation, size=16)
    repeats = document["workload"]["repeats"]
    _text(
        drawing,
        455,
        543,
        f"Dots: {repeats} runs | {median_kind}: median | whiskers: min-max (not CI)",
        size=15,
        color=MUTED_COLOR,
    )
    date = document["recorded_at_utc"][:10]
    environment = document["environment"]
    _text(
        drawing,
        55,
        43,
        f"Recorded {date} | {environment['cpu']} | Python {environment['python']} | SQLite {environment['sqlite']}",
        size=14,
        color=MUTED_COLOR,
    )
    _text(
        drawing,
        55,
        21,
        f"Source: {derived['inputFile']} | input SHA256 and values: series.json",
        size=14,
        color=MUTED_COLOR,
    )
    return drawing


def _nice_limits(maximum):
    rough_step = maximum * 1.12 / 4
    magnitude = 10 ** math.floor(math.log10(rough_step))
    step = next(value * magnitude for value in (1, 2, 2.5, 5, 10) if value * magnitude >= rough_step)
    return math.ceil(maximum * 1.12 / step) * step, step


def _axes(drawing, x, y, width, height, maximum, step):
    """Use ReportLab's numerical Y axis with an explicit zero baseline."""
    axis = YValueAxis()
    axis.setPosition(x, y, height)
    axis.valueMin, axis.valueMax, axis.valueStep = 0, maximum, step
    axis.labels.fontName, axis.labels.fontSize = "Helvetica", 14
    axis.labels.fillColor = HexColor(MUTED_COLOR)
    axis.labels.dx, axis.labels.dy = -9, -5
    axis.labelTextFormat = lambda value: f"{value:,.0f}" if float(value).is_integer() else f"{value:g}"
    axis.strokeColor = HexColor(MUTED_COLOR)
    axis.configure([[0, maximum]])
    for index in range(int(round(maximum / step)) + 1):
        value = index * step
        screen_y = y + height * value / maximum
        _line(drawing, x, screen_y, x + width, screen_y, GRID_COLOR)
    drawing.add(axis)
    _line(drawing, x, y, x + width, y, MUTED_COLOR)


def _whisker(drawing, x, minimum, maximum, implementation):
    color = PALETTE[implementation]
    dash = [3, 2] if implementation == "runtime" else None
    _line(drawing, x, minimum, x, maximum, color, 1.3, dash)
    half_cap = 8 if implementation == "runtime" else 12
    for value in (minimum, maximum):
        _line(drawing, x - half_cap, value, x + half_cap, value, color, 1.3)


def _http_panel(drawing, derived, metric, title, x):
    y, width, height = 200, 425, 290
    groups = [series for series in derived["series"] if series["metric"] == metric]
    maximum, step = _nice_limits(max(series["max"] for series in groups))
    readers = derived["workload"]["connections"]
    domain_max = max(readers) + max(1, max(readers) * 0.125)

    def scale_x(value):
        return x + width * value / domain_max

    def scale_y(value):
        return y + height * value / maximum

    _text(drawing, x, 514, title, size=18, bold=True)
    _axes(drawing, x, y, width, height, maximum, step)
    for value in readers:
        position = scale_x(value)
        _line(drawing, position, y, position, y - 6, MUTED_COLOR)
        _text(drawing, position, y - 28, str(value), size=15, anchor="middle")
    _text(drawing, x + width / 2, 142, "Simultaneous readers (count)", size=15, anchor="middle")
    for implementation in IMPLEMENTATIONS:
        selected = sorted(
            (series for series in groups if series["implementation"] == implementation),
            key=lambda series: series["readers"],
        )
        points = []
        for series in selected:
            position = scale_x(series["readers"])
            _whisker(drawing, position, scale_y(series["min"]), scale_y(series["max"]), implementation)
            points.extend((position, scale_y(series["median"])))
        drawing.add(
            PolyLine(
                points,
                strokeColor=HexColor(PALETTE[implementation]),
                strokeWidth=2.5,
                strokeDashArray=[7, 4] if implementation == "runtime" else None,
                fillColor=None,
            )
        )
        for series in selected:
            for value in series["rawValues"]:
                _marker(drawing, scale_x(series["readers"]), scale_y(value), implementation)


def http_figures(document, derived):
    workload = document["workload"]
    sqlite_label, _checkpoint_events = _sqlite_label(derived)
    subtitle = (
        f"{workload['http_events']} events/run | {workload['payload_bytes']} ASCII message bytes (+ metadata) | "
        f"{sqlite_label} | 1 producer | localhost HTTP"
    )
    latency = _header(document, derived, "HTTP latency across reader fanout", subtitle)
    _http_panel(latency, derived, "first_event_p95", "First-event p95 / slowest reader (ms)", 85)
    _http_panel(latency, derived, "delivery_p95", "Event delivery p95 (ms)", 650)
    _text(
        latency,
        55,
        103,
        "First event includes request setup and reader admission; delivery ends at complete client frame parsing.",
    )
    reader_counts = "/".join(str(value) for value in workload["connections"])
    _text(
        latency,
        55,
        81,
        f"At {reader_counts} readers, first-event p95 is the slowest reader. "
        "Each dot is one run; percentiles are never pooled.",
    )
    throughput = _header(document, derived, "HTTP throughput: unique events and delivered frames", subtitle)
    _http_panel(throughput, derived, "unique_producer_events_per_second", "Unique producer events/s", 85)
    _http_panel(throughput, derived, "aggregate_delivered_frames_per_second", "Aggregate delivered frames/s", 650)
    _text(
        throughput,
        55,
        103,
        "Delivered business frames = unique events x readers. Larger fanout does not mean a faster producer.",
    )
    _text(
        throughput,
        55,
        81,
        "Both rates use earliest request start to latest reader completion; terminal frames are excluded from counts.",
    )
    return (
        (
            "http-latency",
            latency,
            "HTTP first-business-event and event-delivery p95 latency",
            "Two zero-baseline panels at the recorded reader counts. "
            "Blue circles are raw; orange diamonds are runtime. "
            f"Each dot is a run p95 in milliseconds; at {reader_counts} reader observations "
            "the first-event p95 equals the slowest reader. "
            "Median lines and min-max whiskers summarize runs, not confidence intervals or tail-latency SLOs.",
        ),
        (
            "http-throughput",
            throughput,
            "HTTP unique-event and aggregate-delivered-frame throughput",
            "Two zero-baseline panels at the recorded reader counts. "
            "Blue circles are raw; orange diamonds are runtime. "
            "Each dot is a run rate; lines are medians and whiskers are min-max. "
            "Aggregate delivery multiplies events by "
            "reader count, so its increase does not imply greater producer speed.",
        ),
    )


def _sqlite_label(derived):
    settings = derived["sqliteSettings"]
    synchronous = SQLITE_SYNCHRONOUS_NAMES.get(settings["synchronous"], f"synchronous={settings['synchronous']}")
    return f"SQLite {settings['journal_mode'].upper()} / {synchronous}", settings["checkpoint_events"]


def core_figure(document, derived):
    workload = document["workload"]
    sqlite_label, checkpoint_events = _sqlite_label(derived)
    subtitle = (
        f"{workload['events']} events/run | {workload['payload_bytes']} ASCII message bytes (+ metadata) | "
        f"1 producer | checksum sink | checkpoint batch: {checkpoint_events}"
    )
    drawing = _header(
        document, derived, "Core producer interval: volatile and SQLite archive", subtitle, median_kind="bar"
    )
    groups = [series for series in derived["series"] if series["metric"] == "core_producer_interval"]
    maximum, step = _nice_limits(max(series["max"] for series in groups))
    y, height = 200, 290

    def scale_y(value):
        return y + height * value / maximum

    _text(drawing, 100, 514, "Total producer interval (ms)", size=18, bold=True)
    _axes(drawing, 100, y, 970, height, maximum, step)
    for persistence, center in (("none", 350), ("sqlite", 820)):
        for implementation, offset in (("raw", -63), ("runtime", 63)):
            series = next(
                item
                for item in groups
                if item["persistence"] == persistence and item["implementation"] == implementation
            )
            x = center + offset
            _whisker(drawing, x, scale_y(series["min"]), scale_y(series["max"]), implementation)
            _line(
                drawing,
                x - 19,
                scale_y(series["median"]),
                x + 19,
                scale_y(series["median"]),
                PALETTE[implementation],
                3,
            )
            for index, value in enumerate(series["rawValues"]):
                jitter = (index - (len(series["rawValues"]) - 1) / 2) * 12
                _marker(drawing, x + jitter, scale_y(value), implementation)
            _text(drawing, x, scale_y(series["max"]) + 21, f"{series['median']:.2f} ms", size=15, anchor="middle")
            _text(drawing, x, 171, implementation, size=15, anchor="middle")
        _text(
            drawing,
            center,
            142,
            "Volatile latest snapshot" if persistence == "none" else sqlite_label,
            size=17,
            anchor="middle",
            bold=True,
        )
    _text(
        drawing,
        55,
        103,
        "Timed interval includes production and final commit; database setup and final verification are outside it.",
    )
    _text(
        drawing,
        55,
        81,
        "SQLite adds connection, transaction and snapshot work. This compares archive adapters, not frameworks.",
    )
    return (
        "core-persistence",
        drawing,
        "Core producer interval with volatile and SQLite latest-snapshot archive",
        "A zero-baseline comparison of total producer milliseconds for the same event workload. Blue circles are raw; "
        "orange diamonds are runtime. Dots are independent runs, horizontal bars are medians, "
        "and whiskers are min-max, "
        "not confidence intervals. SQLite includes its transaction and connection costs.",
    )


def _write_svg(drawing, path, title, description):
    root = ET.fromstring(renderSVG.drawToString(drawing))
    ET.register_namespace("", SVG_NAMESPACE)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    root.set("role", "img")
    root.set("aria-labelledby", "chart-title chart-description")
    for element in list(root):
        if element.tag in (f"{{{SVG_NAMESPACE}}}title", f"{{{SVG_NAMESPACE}}}desc"):
            root.remove(element)
    title_element = ET.Element(f"{{{SVG_NAMESPACE}}}title", {"id": "chart-title"})
    title_element.text = title
    description_element = ET.Element(f"{{{SVG_NAMESPACE}}}desc", {"id": "chart-description"})
    description_element.text = description
    root.insert(0, title_element)
    root.insert(1, description_element)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def render(document, derived, output, pdf_output=None):
    """Keep optional rasterization and temporary PDFs outside the runtime package."""
    if REPORTLAB_IMPORT_ERROR is not None:
        raise RuntimeError(
            "Figure generation requires optional ReportLab in the active environment; no dependency was installed"
        ) from REPORTLAB_IMPORT_ERROR
    output.mkdir(parents=True, exist_ok=True)
    if pdf_output is not None:
        pdf_output.mkdir(parents=True, exist_ok=True)
    sips = shutil.which("sips")
    with tempfile.TemporaryDirectory(prefix="ui-stream-figures-") as temporary:
        for stem, drawing, title, description in (*http_figures(document, derived), core_figure(document, derived)):
            _write_svg(drawing, output / f"{stem}.svg", title, description)
            pdf = (pdf_output or Path(temporary)) / f"{stem}.pdf"
            renderPDF.drawToFile(drawing, str(pdf), title=title)
            if sips:
                subprocess.run(
                    [sips, "-s", "format", "png", str(pdf), "--out", str(output / f"{stem}.png")],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    timeout=30,
                )
    (output / "series.json").write_text(json.dumps(derived, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    formats = "SVG + PNG" if sips else "SVG vector files only (sips not found; PNG was not generated)"
    if pdf_output is not None:
        formats += f"; PDF vector files: {pdf_output}"
    print(f"Generated 3 figures: {formats}; derived series: {output / 'series.json'}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Recorded benchmark JSON; never modified")
    parser.add_argument(
        "--output", type=Path, default=Path("docs/performance-figures"), help="SVG/PNG and series.json directory"
    )
    parser.add_argument("--pdf-output", type=Path, help="Optionally retain shareable PDFs in this directory")
    arguments = parser.parse_args(argv)
    try:
        input_bytes = arguments.input.read_bytes()
        document = json.loads(input_bytes)
        derived = derive_series(document, input_bytes, arguments.input.name)
        render(document, derived, arguments.output, arguments.pdf_output)
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"Cannot generate benchmark figures: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
