"""Helper: construct .ipynb files from a list of (kind, source) cells.

Used by the lab maintainer (not students). Lets us author notebooks as plain
Python data structures and dump clean JSON without manual escaping.

Usage:
    from build_notebook import md, code, write
    write("notebooks/00_setup.ipynb", [
        md("# Title\n\nIntro text"),
        code("print('hello')"),
    ])
"""

from __future__ import annotations

import json
import pathlib


def md(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": source,
    }


def write(path: str | pathlib.Path, cells: list[dict]) -> None:
    nb = {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3 (DeepBrief)",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
