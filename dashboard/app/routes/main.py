"""
Main routes — Home page with state picker.
"""
from flask import Blueprint, render_template
from app.data_loader import get_states

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    """Home page showing all US states with church counts."""
    states = get_states()
    total_churches = sum(s["church_count"] for s in states)
    total_states = len(states)
    bulletin_states = sum(1 for s in states if s["has_bulletin"])
    return render_template(
        "index.html",
        states=states,
        total_churches=total_churches,
        total_states=total_states,
        bulletin_states=bulletin_states,
    )
