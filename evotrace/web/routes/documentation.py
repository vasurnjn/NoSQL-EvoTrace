"""Academic research documentation and architecture routes."""

from __future__ import annotations
from flask import Blueprint, render_template

bp = Blueprint("documentation", __name__)


@bp.route("/")
def index():
    """Render comprehensive research overview and documentation."""
    return render_template("documentation.html")
