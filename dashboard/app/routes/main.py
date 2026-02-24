"""
Main routes — Home page with state picker.
"""

from flask import Blueprint, render_template

from app.data_loader import get_bulletin_stats, get_states

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    """Home page showing all US states with church counts."""
    states = get_states()
    total_churches = sum(s["church_count"] for s in states)
    total_states = len(states)
    bulletin_states = [s for s in states if s["has_bulletin"]]
    bulletin_state_count = len(bulletin_states)

    # Aggregate bulletin name totals and attach per-state unique counts
    total_names = 0
    total_unique = 0
    stats_by_state = {}
    for s in bulletin_states:
        stats = get_bulletin_stats(s["state_dir"])
        if stats:
            total_names += stats["total_names"]
            total_unique += stats["unique_names"]
            stats_by_state[s["state_dir"]] = stats

    # Enrich state dicts with unique_names for template
    enriched_states = []
    for s in states:
        s_copy = dict(s)
        st = stats_by_state.get(s["state_dir"])
        s_copy["unique_names"] = st["unique_names"] if st else 0
        enriched_states.append(s_copy)

    return render_template(
        "index.html",
        states=enriched_states,
        total_churches=total_churches,
        total_states=total_states,
        bulletin_states=bulletin_state_count,
        total_names=total_names,
        total_unique=total_unique,
    )
