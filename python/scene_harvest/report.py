"""Summary statistics over a harvested dataset, computed with pandas.

`summarize` turns the consolidated tables into a handful of small frames
and numbers (polycount distribution, heaviest scenes, missing dependencies,
skin influence limits). `render` formats them for the console.

"""

import os

import pandas as pd

from scene_harvest import manifest as manifest_mod
from scene_harvest import writers

TOP_N = 5
INFLUENCE_BUDGET = 4


def summarize(output_dir, influence_budget=INFLUENCE_BUDGET):
    """Compute the report figures of a harvest.

    Args:
        output_dir (str): Harvest output folder.
        influence_budget (int): Max influences per vertex a game or
            realtime target allows; skins above it are flagged.

    Returns:
        dict: Plain values and pandas objects, see `render`.

    """
    scenes = writers.read_table(output_dir, "scenes")
    meshes = writers.read_table(output_dir, "meshes")
    dependencies = writers.read_table(output_dir, "dependencies")
    skins = writers.read_table(output_dir, "skins")

    triangles = meshes.groupby("scene_id")["triangle_count"].sum()
    heaviest = (
        scenes.set_index("scene_id")[["scene_path"]]
        .join(triangles.rename("triangles"), how="inner")
        .sort_values("triangles", ascending=False)
        .head(TOP_N)
    )
    heaviest["scene_path"] = heaviest["scene_path"].map(os.path.basename)

    missing = dependencies[~dependencies["exists"]]
    return {
        "status": manifest_mod.Manifest.load(output_dir).counts(),
        "scene_count": len(scenes),
        "mesh_count": len(meshes),
        "triangles_per_scene": triangles.describe(),
        "heaviest": heaviest,
        "dependency_count": len(dependencies),
        "missing_by_kind": missing.groupby("kind").size().sort_values(ascending=False),
        "missing_paths": missing["path"].value_counts().head(TOP_N),
        "skin_count": len(skins),
        "max_influences": skins["max_influences_per_vertex"].value_counts().sort_index(),
        "over_budget": int((skins["max_influences_per_vertex"] > influence_budget).sum()),
        "influence_budget": influence_budget,
    }


def render(summary):
    """Format a summary as console text.

    Args:
        summary (dict): Result of `summarize`.

    Returns:
        str: Multi-line report.

    """
    lines = []
    status = ", ".join(f"{key}={value}" for key, value in sorted(summary["status"].items())) or "empty"
    _section(lines, "Harvest")
    lines.append(f"Manifest:   {status}")
    lines.append(f"Scenes:     {summary['scene_count']}")
    lines.append(f"Meshes:     {summary['mesh_count']}")

    _section(lines, "Triangles per scene")
    lines.append(_frame_text(summary["triangles_per_scene"]))
    lines.append("")
    lines.append("Heaviest scenes:")
    lines.append(_frame_text(summary["heaviest"]))

    _section(lines, "Dependencies")
    lines.append(f"Total:      {summary['dependency_count']}")
    lines.append(f"Missing:    {int(summary['missing_by_kind'].sum())}")
    lines.append(_frame_text(summary["missing_by_kind"]))
    lines.append("")
    lines.append("Most referenced missing files:")
    lines.append(_frame_text(summary["missing_paths"]))

    _section(lines, "Skinning")
    lines.append(f"Skins:      {summary['skin_count']}")
    lines.append(f"Over {summary['influence_budget']} influences/vertex: {summary['over_budget']}")
    lines.append("Max influences per vertex (skins):")
    lines.append(_frame_text(summary["max_influences"]))
    return "\n".join(lines)


def _section(lines, title):
    if lines:
        lines.append("")
    lines.append("=" * 60)
    lines.append(title)
    lines.append("=" * 60)


def _frame_text(data):
    if isinstance(data, (pd.Series, pd.DataFrame)) and data.empty:
        return "  (none)"
    return data.to_string()
