"""Charts of a harvested dataset, drawn with matplotlib.

Each function takes the tables it needs (pandas DataFrames from
``writers.read_table``) and returns a ``matplotlib.figure.Figure``. Figures
are built without pyplot, so there is no global state: the CLI saves them as
PNGs and the UI puts the same figures on Qt canvases. Charts with no data
are skipped rather than drawn empty.

"""

import os

from scene_harvest import report
from scene_harvest import writers

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e4e3df"
SERIES = "#2a78d6"
MISSING = "#d03b3b"
TOP_N = 15
FIGURE_SIZE = (8.0, 4.0)
DPI = 120


def figures(output_dir, influence_budget=report.INFLUENCE_BUDGET):
    """Build every chart that has data for a harvest.

    Args:
        output_dir (str): Harvest output folder.
        influence_budget (int): Influence limit marked on the skin chart.

    Returns:
        list: (name, Figure) pairs in display order.

    """
    scenes = writers.read_table(output_dir, "scenes")
    meshes = writers.read_table(output_dir, "meshes")
    dependencies = writers.read_table(output_dir, "dependencies")
    skins = writers.read_table(output_dir, "skins")
    builders = (
        ("triangles_per_scene", lambda: triangles_per_scene(scenes, meshes)),
        ("triangle_distribution", lambda: triangle_distribution(meshes)),
        ("dependencies", lambda: dependencies_by_kind(dependencies)),
        ("skin_influences", lambda: skin_influences(skins, influence_budget)),
        ("harvest_time", lambda: harvest_time(scenes)),
    )
    result = []
    for name, build in builders:
        figure = build()
        if figure is not None:
            result.append((name, figure))
    return result


def save(output_dir, folder=None, influence_budget=report.INFLUENCE_BUDGET):
    """Save every chart as a PNG.

    Args:
        output_dir (str): Harvest output folder.
        folder (str, optional): Where to write; ``<output>/charts`` by default.
        influence_budget (int): Influence limit marked on the skin chart.

    Returns:
        list: Paths of the written files.

    """
    folder = folder or os.path.join(output_dir, "charts")
    os.makedirs(folder, exist_ok=True)
    paths = []
    for name, figure in figures(output_dir, influence_budget):
        path = os.path.join(folder, name + ".png")
        figure.savefig(path, facecolor=SURFACE)
        paths.append(path)
    return paths


def triangles_per_scene(scenes, meshes, top_n=TOP_N):
    """Horizontal bars of the heaviest scenes by triangle count.

    Args:
        scenes (DataFrame): ``scenes`` table.
        meshes (DataFrame): ``meshes`` table.
        top_n (int): Most scenes to show.

    Returns:
        Figure: The chart, or None without meshes.

    """
    if meshes.empty:
        return None
    totals = meshes.groupby("scene_id")["triangle_count"].sum()
    names = scenes.set_index("scene_id")["scene_path"].map(_file_name)
    data = totals.rename(index=lambda scene_id: names.get(scene_id, scene_id))
    data = data.sort_values(ascending=False).head(top_n).sort_values()
    figure, axes = _figure(
        "Triangles per scene",
        "{0}, heaviest {1} shown".format(_plural(len(totals), "scene"), len(data)),
    )
    bars = axes.barh(data.index, data.values, height=0.6, color=SERIES)
    _bar_labels(axes, bars, data.values)
    axes.set_xlabel("Triangles", color=MUTED, fontsize=9)
    _style(axes, grid_axis="x")
    return _finish(figure)


def triangle_distribution(meshes):
    """Histogram of triangle counts across all meshes.

    Args:
        meshes (DataFrame): ``meshes`` table.

    Returns:
        Figure: The chart, or None without meshes.

    """
    if meshes.empty:
        return None
    counts = meshes["triangle_count"]
    figure, axes = _figure(
        "Triangles per mesh",
        "{0}, median {1:,.0f}".format(_plural(len(counts), "mesh", "meshes"), counts.median()),
    )
    bins = min(30, max(5, len(counts)))
    axes.hist(counts, bins=bins, color=SERIES, edgecolor=SURFACE, linewidth=1)
    axes.set_xlabel("Triangles", color=MUTED, fontsize=9)
    axes.set_ylabel("Meshes", color=MUTED, fontsize=9)
    _style(axes, grid_axis="y", whole_numbers=True)
    return _finish(figure)


def dependencies_by_kind(dependencies):
    """Stacked bars of found and missing dependencies per kind.

    Args:
        dependencies (DataFrame): ``dependencies`` table.

    Returns:
        Figure: The chart, or None without dependencies.

    """
    if dependencies.empty:
        return None
    table = (
        dependencies.assign(state=dependencies["exists"].map({True: "found", False: "missing"}))
        .groupby(["kind", "state"]).size().unstack(fill_value=0)
        .reindex(columns=["found", "missing"], fill_value=0)
    )
    table = table.loc[table.sum(axis=1).sort_values().index]
    missing = int(table["missing"].sum())
    figure, axes = _figure(
        "Dependencies by kind",
        "{0} of {1} missing".format(missing, int(table.values.sum())),
    )
    found_bars = axes.barh(table.index, table["found"], height=0.6, color=SERIES,
                           edgecolor=SURFACE, linewidth=2, label="found")
    axes.barh(table.index, table["missing"], left=table["found"], height=0.6, color=MISSING,
              edgecolor=SURFACE, linewidth=2, label="missing")
    totals = table["found"] + table["missing"]
    for bar, kind in zip(found_bars, table.index):
        if table.loc[kind, "missing"]:
            axes.text(totals[kind] + totals.max() * 0.01, bar.get_y() + bar.get_height() / 2,
                      "{0} missing".format(int(table.loc[kind, "missing"])),
                      va="center", fontsize=9, color=MUTED)
    axes.set_xlabel("Files", color=MUTED, fontsize=9)
    axes.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=MUTED)
    _style(axes, grid_axis="x", whole_numbers=True)
    return _finish(figure)


def skin_influences(skins, budget=report.INFLUENCE_BUDGET):
    """Bars of how many skins reach each max influence count.

    Args:
        skins (DataFrame): ``skins`` table.
        budget (int): Limit drawn as a dashed line.

    Returns:
        Figure: The chart, or None without skins.

    """
    if skins.empty:
        return None
    counts = skins["max_influences_per_vertex"].value_counts().sort_index()
    over = int((skins["max_influences_per_vertex"] > budget).sum())
    figure, axes = _figure(
        "Max influences per vertex",
        "{0}, {1} over the limit of {2}".format(_plural(len(skins), "skin"), over, budget),
    )
    bars = axes.bar(counts.index.astype(int), counts.values, width=0.6, color=SERIES)
    _column_labels(axes, bars, counts.values)
    axes.axvline(budget + 0.5, color=MUTED, linestyle="--", linewidth=1)
    axes.text(budget + 0.6, axes.get_ylim()[1] * 0.92, "limit", color=MUTED, fontsize=9)
    axes.set_xticks(list(range(1, max(int(counts.index.max()), budget) + 2)))
    axes.set_xlabel("Max influences on any vertex", color=MUTED, fontsize=9)
    axes.set_ylabel("Skins", color=MUTED, fontsize=9)
    _style(axes, grid_axis="y", whole_numbers=True)
    return _finish(figure)


def harvest_time(scenes, top_n=TOP_N):
    """Horizontal bars of the slowest scenes to harvest.

    Args:
        scenes (DataFrame): ``scenes`` table.
        top_n (int): Most scenes to show.

    Returns:
        Figure: The chart, or None without scenes.

    """
    if scenes.empty:
        return None
    data = scenes.assign(name=scenes["scene_path"].map(_file_name)).set_index("name")["duration_s"]
    data = data.sort_values(ascending=False).head(top_n).sort_values()
    figure, axes = _figure(
        "Harvest time per scene",
        "{0:.1f}s in total across {1}".format(scenes["duration_s"].sum(), _plural(len(scenes), "scene")),
    )
    bars = axes.barh(data.index, data.values, height=0.6, color=SERIES)
    _bar_labels(axes, bars, data.values, fmt="{0:.2f}s")
    axes.set_xlabel("Seconds", color=MUTED, fontsize=9)
    _style(axes, grid_axis="x")
    return _finish(figure)


def _figure(title, subtitle):
    from matplotlib.figure import Figure

    figure = Figure(figsize=FIGURE_SIZE, dpi=DPI, facecolor=SURFACE)
    axes = figure.add_subplot(1, 1, 1)
    axes.set_facecolor(SURFACE)
    figure.suptitle(title, x=0.02, ha="left", fontsize=12, color=INK, fontweight="bold")
    axes.set_title(subtitle, loc="left", fontsize=9, color=MUTED, pad=8)
    return figure, axes


def _style(axes, grid_axis, whole_numbers=False):
    from matplotlib.ticker import MaxNLocator

    axes.tick_params(colors=MUTED, labelsize=9, length=0)
    if whole_numbers:
        # Counts of files, skins or meshes: no 0.25 ticks.
        getattr(axes, grid_axis + "axis").set_major_locator(MaxNLocator(integer=True))
    getattr(axes, grid_axis + "axis").grid(True, color=GRID, linewidth=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(GRID)


def _bar_labels(axes, bars, values, fmt="{0:,}"):
    peak = max(values) if len(values) else 0
    for bar, value in zip(bars, values):
        axes.text(bar.get_width() + peak * 0.01, bar.get_y() + bar.get_height() / 2,
                  fmt.format(value), va="center", fontsize=9, color=MUTED)


def _column_labels(axes, bars, values):
    for bar, value in zip(bars, values):
        axes.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), str(int(value)),
                  ha="center", va="bottom", fontsize=9, color=MUTED)


def _finish(figure):
    figure.tight_layout()
    return figure


def _plural(count, word, plural=None):
    return "{0} {1}".format(count, word if count == 1 else (plural or word + "s"))


def _file_name(path):
    return path.replace("\\", "/").rsplit("/", 1)[-1]
