"""Tests for the matplotlib charts and the ``charts`` CLI command.

"""

import os

import pandas as pd
import pytest

pytest.importorskip("matplotlib")

from scene_harvest import charts  # noqa: E402
from scene_harvest import cli  # noqa: E402

SCENES = pd.DataFrame({
    "scene_id": ["a", "b", "c"],
    "scene_path": ["D:/lib/rock.ma", r"D:\lib\crate.ma", "D:/lib/hero.ma"],
    "duration_s": [1.5, 0.25, 0.75],
})
MESHES = pd.DataFrame({
    "scene_id": ["a", "a", "b", "c"],
    "triangle_count": [7000, 80, 12, 836],
})
DEPENDENCIES = pd.DataFrame({
    "kind": ["texture", "texture", "reference", "reference", "cache"],
    "exists": [False, True, True, True, False],
})
SKINS = pd.DataFrame({"max_influences_per_vertex": [3, 4, 6, 6]})


def _texts(figure):
    return [text.get_text() for axes in figure.axes for text in axes.texts]


def test_triangles_per_scene_sums_meshes_and_sorts():
    figure = charts.triangles_per_scene(SCENES, MESHES)
    labels = [label.get_text() for label in figure.axes[0].get_yticklabels()]
    assert labels == ["crate.ma", "hero.ma", "rock.ma"]
    assert "7,080" in _texts(figure)
    assert figure.axes[0].get_title(loc="left") == "3 scenes, heaviest 3 shown"


def test_triangles_per_scene_keeps_the_heaviest():
    figure = charts.triangles_per_scene(SCENES, MESHES, top_n=1)
    assert [label.get_text() for label in figure.axes[0].get_yticklabels()] == ["rock.ma"]


def test_dependencies_show_missing_per_kind():
    figure = charts.dependencies_by_kind(DEPENDENCIES)
    assert figure.axes[0].get_title(loc="left") == "2 of 5 missing"
    assert sorted(_texts(figure)) == ["1 missing", "1 missing"]
    assert [text.get_text() for text in figure.axes[0].get_legend().get_texts()] == ["found", "missing"]


def test_skin_chart_counts_skins_over_the_limit():
    figure = charts.skin_influences(SKINS, budget=4)
    assert figure.axes[0].get_title(loc="left") == "4 skins, 2 over the limit of 4"


def test_other_charts_build():
    assert charts.triangle_distribution(MESHES) is not None
    assert "2.5s in total" in charts.harvest_time(SCENES).axes[0].get_title(loc="left")


def test_empty_tables_give_no_chart():
    empty = pd.DataFrame(columns=["scene_id", "triangle_count", "kind", "exists",
                                  "max_influences_per_vertex", "scene_path", "duration_s"])
    assert charts.triangles_per_scene(empty, empty) is None
    assert charts.triangle_distribution(empty) is None
    assert charts.dependencies_by_kind(empty) is None
    assert charts.skin_influences(empty) is None
    assert charts.harvest_time(empty) is None


def test_cli_charts_on_an_empty_output(tmp_path, capsys):
    assert cli.main(["charts", "-o", str(tmp_path)]) == 1
    assert "Nothing to chart" in capsys.readouterr().out


def test_cli_charts_on_a_real_harvest(usd_library, tmp_path, capsys):
    pytest.importorskip("pxr")
    output_dir = str(tmp_path / "harvest")
    assert cli.main(["run", usd_library["root"], "-o", output_dir, "-w", "2"]) == 0
    capsys.readouterr()
    assert cli.main(["charts", "-o", output_dir]) == 0
    paths = capsys.readouterr().out.split()
    names = sorted(os.path.basename(path) for path in paths)
    assert "triangles_per_scene.png" in names
    assert "dependencies.png" in names
    assert all(os.path.getsize(path) > 1000 for path in paths)
