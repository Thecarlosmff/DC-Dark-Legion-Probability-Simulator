"""Streamlit UI of the Team Builder tab (logic: teambuilder.py, images: images.py)."""
from __future__ import annotations

import html
import json

import pandas as pd
import streamlit as st

import engine as eng
import images
import teambuilder as tb

OWNED_PATH = tb.TEAM_DATA_DIR / "owned.json"
DATA_FILES = ("champions.json", "synergies.json", "legacy_pieces.json")
NO_PIECE = "— none —"
TEAM_ROOT = tb.TEAMS_DIR.parent

LIST_CSS = """<style>
.st-key-tb_list [data-testid="stVerticalBlock"] { gap: 0.15rem; }
.st-key-tb_list button { min-height: 1.7rem; padding: 0 0.5rem; }
</style>"""


# ------------------------------------------------------------------- data
@st.cache_data
def _load_data(stamp: tuple) -> tb.TeamData:
    # `stamp` (file modification times) is only a cache key: editing a data file
    # invalidates the cache.
    return tb.load_team_data()


def get_data() -> tb.TeamData:
    stamp = tuple((tb.TEAM_DATA_DIR / f).stat().st_mtime if (tb.TEAM_DATA_DIR / f).exists() else 0.0
                  for f in DATA_FILES)
    return _load_data(stamp)


def init_state(data: tb.TeamData) -> None:
    ss = st.session_state
    if "tb_roster" not in ss:
        ss.tb_roster = tb.load_roster(OWNED_PATH, data)
        ss.tb_roster_version = 0
    for name in data.champions:                      # champions added to the data later
        ss.tb_roster.setdefault(name, tb.normalize_entry("locked", 0))
    if "tb_teams" not in ss:
        ss.tb_teams = tb.TeamsState()
    ss.setdefault("tb_cur", 0)                       # the team being edited
    ss.tb_cur = max(0, min(ss.tb_cur, len(ss.tb_teams.teams) - 1))
    ss.setdefault("tb_mode_or", False)               # faction filter: AND / OR
    ss.setdefault("tb_hide_used", True)
    ss.setdefault("tb_hide_locked", False)


# ---------------------------------------------------------------- helpers
def static_serving() -> bool:
    return bool(st.get_option("server.enableStaticServing"))


def icon_label(store: images.ImageStore, kind: str, name: str) -> str:
    """Pill label: the stored icon when Streamlit can serve it, else the name."""
    url = store.url(kind, name) if static_serving() else None
    return f"![{name}]({url})" if url else name


def show_card(store: images.ImageStore, champ: tb.Champion) -> None:
    local = store.path("card", champ.name)
    src = str(local) if local else (champ.card_url or None)    # local first, no internet needed
    if src:
        st.image(src, width="stretch")


def stars_html(entry: dict) -> str:
    """'★★★☆☆' in the tier color (🔒 when locked), like the desktop list."""
    color = entry.get("color", "locked")
    if color == "locked":
        return "<span style='opacity:.6'>🔒</span>"
    stars = entry.get("stars", 0)
    return (f"<span style='color:{tb.rank_color(entry)};letter-spacing:1px;"
            f"text-shadow:0 0 2px #000,0 0 2px #000'>{'★' * stars}{'☆' * (5 - stars)}</span>")


def rank_html(entry: dict) -> str:
    label = "🔒 Locked" if entry.get("color", "locked") == "locked" else tb.rank_label(entry)
    return (f"<span style='color:{tb.rank_color(entry)};font-weight:600;"
            f"text-shadow:0 0 3px #000,0 0 3px #000'>{html.escape(label)}</span>")


def team_index(uid: int) -> int | None:
    return next((i for i, t in enumerate(st.session_state.tb_teams.teams) if t.uid == uid), None)


# -------------------------------------------------------------- callbacks
def cb_add(name: str) -> None:
    ss = st.session_state
    ss.tb_teams.add(ss.tb_cur, name)


def cb_remove(uid: int, name: str) -> None:
    i = team_index(uid)
    if i is not None:
        st.session_state.tb_teams.remove(i, name)


def cb_legacy(uid: int, name: str, key: str) -> None:
    i = team_index(uid)
    if i is not None:
        piece = st.session_state[key]
        st.session_state.tb_teams.set_legacy(i, name, None if piece == NO_PIECE else piece)


def cb_select() -> None:
    st.session_state.tb_cur = st.session_state.tb_team_radio


def cb_goto(i: int) -> None:
    st.session_state.tb_cur = i


def cb_add_team() -> None:
    ss = st.session_state
    i = ss.tb_teams.add_team()
    if i is not None:
        ss.tb_cur = i


def cb_clear_team() -> None:
    ss = st.session_state
    ss.tb_teams.clear(ss.tb_cur)


def cb_delete_team() -> None:
    ss = st.session_state
    ss.tb_teams.remove_team(ss.tb_cur)
    ss.tb_cur = max(0, min(ss.tb_cur, len(ss.tb_teams.teams) - 1))


def cb_clear_all() -> None:
    for i in range(len(st.session_state.tb_teams.teams)):
        st.session_state.tb_teams.clear(i)


def cb_field(uid: int, attr: str, key: str) -> None:
    i = team_index(uid)
    if i is not None:
        setattr(st.session_state.tb_teams.teams[i], attr, st.session_state[key])


def cb_toggle(key: str) -> None:
    st.session_state[key] = not st.session_state[key]


def cb_clear_filters() -> None:
    st.session_state.tb_fac = []
    st.session_state.tb_cls = []


def cb_load_file(filename: str) -> None:
    """Load a saved team image into a NEW team."""
    ss = st.session_state
    path = tb.TEAMS_DIR / filename
    saved = tb.read_team_png(path.read_bytes()) if path.exists() else None
    if saved is None:
        ss.tb_flash = ("error", f"No team data found in {filename}.")
        return
    load_saved_team(saved, path.stem.replace("_", " "))


def load_saved_team(saved: tb.SavedTeam, fallback_title: str) -> None:
    ss = st.session_state
    i = ss.tb_teams.add_team()
    if i is None:
        ss.tb_flash = ("error", f"You already have {tb.MAX_TEAMS} teams.")
        return
    skipped = ss.tb_teams.set_team(i, saved.names, saved.legacy, get_data(),
                                   title=saved.title or fallback_title, subtitle=saved.subtitle)
    ss.tb_cur = i
    ss.tb_flash = ("success", f"Loaded into team {i + 1} (open the Build tab)."
                   + (f" Skipped: {', '.join(skipped)}." if skipped else ""))


def cb_delete_file(filename: str) -> None:
    if filename in {p.name for p in tb.list_team_images()}:
        (tb.TEAMS_DIR / filename).unlink(missing_ok=True)


def generate_team_image(data: tb.TeamData, store: images.ImageStore, index: int):
    ss = st.session_state
    team = ss.tb_teams.teams[index]
    title = team.display_title(index)
    png = tb.render_team_png(team.members, team.legacy, data, ss.tb_roster, title=title,
                             subtitle=team.subtitle, show_rank=team.show_rank, store=store)
    return tb.save_team_image(png, title)


# ----------------------------------------------------------- images status
def render_images_bar(data: tb.TeamData, store: images.ImageStore) -> None:
    ss = st.session_state
    report = ss.pop("tb_img_report", None)
    if report is not None:
        if report.failed:
            st.warning(f"Downloaded {report.downloaded} images; {len(report.failed)} failed: "
                       + ", ".join(f"{j.name}" for j, _ in report.failed[:12]))
        else:
            st.success(f"Downloaded {report.downloaded} images.")
    jobs = images.missing(data, store)
    if not jobs:
        st.caption("🖼️ All images are stored in static/images: no internet needed.")
    else:
        stat = images.status(data, store)
        have, total = sum(h for h, _ in stat.values()), sum(t for _, t in stat.values())
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        c1.info(f"🖼️ {have}/{total} images are stored locally. Missing: "
                + ", ".join(f"{images.KIND_LABELS[k]} {t - h}" for k, (h, t) in stat.items() if t > h))
        if c2.button("Download images", key="tb_dl_images", width="stretch"):
            bar = st.progress(0.0, text="Downloading…")
            ss.tb_img_report = images.download_images(
                jobs, store, progress=lambda f: bar.progress(f, text=f"Downloading… {f:.0%}"))
            st.rerun()
    has_icons = any(store.path("faction", f) for f in data.factions) or \
        any(store.path("class", c) for c in data.classes)
    if has_icons and not static_serving():
        st.caption("ℹ️ Faction and class icons in the filters need static serving: start the app "
                   "with `streamlit run app.py` from the project folder.")


# ------------------------------------------------------------------ picker
def render_picker(data: tb.TeamData, store: images.ImageStore) -> None:
    ss = st.session_state
    ts: tb.TeamsState = ss.tb_teams
    team = ts.teams[ss.tb_cur]
    st.markdown(LIST_CSS, unsafe_allow_html=True)

    rarities = st.pills("Rarity", tb.RARITIES, selection_mode="multi", key="tb_rar",
                        default=[r for r in tb.RARITIES if r != "Epic"], label_visibility="collapsed")
    factions = st.pills("Factions", data.factions, selection_mode="multi", key="tb_fac",
                        format_func=lambda f: icon_label(store, "faction", f),
                        label_visibility="collapsed")
    b1, b2, b3, b4 = st.columns([1, 1.3, 1.7, 1.9])
    b1.button("Clear", key="tb_clear_f", on_click=cb_clear_filters, width="stretch")
    b2.button(f"Mode: {'OR' if ss.tb_mode_or else 'AND'}", key="tb_mode_btn", on_click=cb_toggle,
              args=("tb_mode_or",), width="stretch")
    b3.button(f"Hide Used: {'ON' if ss.tb_hide_used else 'OFF'}", key="tb_used_btn",
              on_click=cb_toggle, args=("tb_hide_used",), width="stretch",
              type="primary" if ss.tb_hide_used else "secondary")
    b4.button(f"Hide Locked: {'ON' if ss.tb_hide_locked else 'OFF'}", key="tb_locked_btn",
              on_click=cb_toggle, args=("tb_hide_locked",), width="stretch",
              type="primary" if ss.tb_hide_locked else "secondary")
    classes = st.pills("Class", data.classes, selection_mode="multi", key="tb_cls",
                       format_func=lambda c: icon_label(store, "class", c),
                       label_visibility="collapsed")
    if factions or classes:
        st.caption(" · ".join(x for x in (", ".join(factions or []), ", ".join(classes or [])) if x))
    query = st.text_input("Search champions", key="tb_q", placeholder="Search champions...",
                          label_visibility="collapsed")

    champs = tb.filter_champions(
        data, ss.tb_roster, query=query, rarities=rarities or [], factions=factions or [],
        faction_mode="OR" if ss.tb_mode_or else "AND", classes=classes or [],
        hide_locked=ss.tb_hide_locked, exclude=ts.used_names() if ss.tb_hide_used else ())
    full = len(team.members) >= tb.MAX_PER_TEAM
    st.caption(f"{len(champs)} champions" + (" · this team is full" if full else ""))
    with st.container(height=560, border=True, key="tb_list"):
        if not champs:
            st.caption("No champion matches the filters.")
        for champ in champs:
            c1, c2 = st.columns([7, 1], vertical_alignment="center")
            c1.markdown(
                f"<div style='display:flex;justify-content:space-between;gap:.5rem'>"
                f"<span>{html.escape(champ.name)}</span>{stars_html(ss.tb_roster[champ.name])}</div>",
                unsafe_allow_html=True)
            c2.button("＋", key=f"tb_add_{champ.name}", on_click=cb_add, args=(champ.name,),
                      help="Add to the current team", disabled=full or champ.name in team.members)


# ------------------------------------------------------------- team panel
def render_slot(data: tb.TeamData, store: images.ImageStore, team: tb.Team, name: str) -> None:
    ss = st.session_state
    champ = data.champions[name]
    show_card(store, champ)
    st.markdown(f"**{html.escape(name)}**  \n{rank_html(ss.tb_roster[name])}  \n"
                f"<small>{html.escape(champ.champ_class)}</small>", unsafe_allow_html=True)
    pieces = [p.name for p in data.legacy_for(champ)]
    key = f"tb_leg_{team.uid}_{name}"
    current = team.legacy.get(name)
    ss[key] = current if current in pieces else NO_PIECE      # always mirror the stored value
    st.selectbox("Legacy piece", [NO_PIECE] + pieces, key=key, label_visibility="collapsed",
                 on_change=cb_legacy, args=(team.uid, name, key))
    st.button("Remove", key=f"tb_rm_{team.uid}_{name}", on_click=cb_remove,
              args=(team.uid, name), width="stretch")


def synergy_table(names: list[str], data: tb.TeamData) -> pd.DataFrame:
    rows = []
    for s in tb.team_synergies(names, data):
        rows.append({
            "Faction": s.name, "Count": s.count,
            "Active bonus": f"({s.tier.count}) {s.tier.description}" if s.tier else "—",
            "Next": f"({s.next_tier.count}) {s.next_tier.description}" if s.next_tier else "—",
        })
    return pd.DataFrame(rows)


def render_team_panel(data: tb.TeamData, store: images.ImageStore) -> None:
    ss = st.session_state
    ts: tb.TeamsState = ss.tb_teams
    idx = ss.tb_cur
    team = ts.teams[idx]

    # team selector (the radio always mirrors tb_cur, so renaming/adding never resets it)
    s1, s2, s3, s4 = st.columns([8, 1.7, 1.2, 1.3], vertical_alignment="center")
    ss.tb_team_radio = idx
    s1.radio("Team", range(len(ts.teams)), key="tb_team_radio", horizontal=True,
             label_visibility="collapsed", on_change=cb_select,
             format_func=lambda i: f"{i + 1}. {ts.teams[i].display_title(i)[:12]}")
    s2.button("＋ Add team", key="tb_add_team", on_click=cb_add_team, width="stretch",
              disabled=len(ts.teams) >= tb.MAX_TEAMS)
    s3.button("Clear", key="tb_clear_team", on_click=cb_clear_team, width="stretch",
              disabled=not team.members)
    s4.button("Delete", key="tb_del_team", on_click=cb_delete_team, width="stretch",
              disabled=len(ts.teams) < 2, help="Delete this team")

    # name, rank toggle, subtitle
    h1, h2, h3 = st.columns([4, 1.5, 4], vertical_alignment="bottom")
    k_title, k_sub, k_rank = (f"tb_title_{team.uid}", f"tb_sub_{team.uid}", f"tb_rank_{team.uid}")
    ss[k_title], ss[k_sub], ss[k_rank] = team.title, team.subtitle, team.show_rank
    h1.text_input("Team name", key=k_title, placeholder=f"Team {idx + 1}",
                  on_change=cb_field, args=(team.uid, "title", k_title))
    h2.checkbox("Show rank", key=k_rank, on_change=cb_field, args=(team.uid, "show_rank", k_rank))
    h3.text_input("Subtitle", key=k_sub, on_change=cb_field, args=(team.uid, "subtitle", k_sub))

    slots = st.columns(tb.MAX_PER_TEAM)
    for i, col in enumerate(slots):
        with col:
            if i < len(team.members):
                render_slot(data, store, team, team.members[i])
            else:
                st.caption("Empty slot")
    if team.members:
        st.markdown("**Synergies**")
        st.dataframe(synergy_table(team.members, data), hide_index=True, width="stretch")
    else:
        st.info("Add champions from the list.")

    if st.button("Generate image", key=f"tb_gen_{team.uid}", disabled=not team.members):
        with st.spinner("Building the image…"):
            path = generate_team_image(data, store, idx)
        st.success(f"Saved to {path.relative_to(TEAM_ROOT)} (see it in “All teams”).")


def render_build(data: tb.TeamData, store: images.ImageStore) -> None:
    left, right = st.columns([5, 9], gap="medium")
    with left:
        render_picker(data, store)
    with right:
        render_team_panel(data, store)


# ------------------------------------------------------------------- info
def render_info(data: tb.TeamData, store: images.ImageStore) -> None:
    ss = st.session_state
    name = st.selectbox("Champion", list(data.champions), key="tb_info_name")
    champ = data.champions[name]
    left, right = st.columns([1, 3])
    with left:
        show_card(store, champ)
    with right:
        st.markdown(f"### {name}")
        st.markdown(f"{champ.champ_class} · {champ.rarity}" + (" · healer" if champ.is_healer else "")
                    + f"  \nYour rank: {rank_html(ss.tb_roster[name])}", unsafe_allow_html=True)
        if champ.avg_level:
            st.markdown(f"Community average level: **{champ.avg_level}**")
        if champ.related:
            st.markdown("Related champions: " + ", ".join(champ.related))
        if champ.codex_url:
            st.link_button("Open in the codex", champ.codex_url)
    st.markdown("**Factions**")
    for faction in champ.synergies:
        syn = data.synergies.get(faction)
        with st.expander(faction, expanded=False):
            if syn:
                if syn.description:
                    st.caption(syn.description)
                for tier in syn.tiers:
                    st.markdown(f"- **{tier.count}:** {tier.description}")
            else:
                st.caption("No synergy data.")
    pieces = data.legacy_for(champ)
    st.markdown(f"**Legacy pieces for {champ.champ_class}** ({len(pieces)})")
    st.dataframe(pd.DataFrame([{"Piece": p.name, "Rarity": p.rarity} for p in pieces]),
                 hide_index=True, width="stretch", height=260)
    st.caption("Skill data is not included in champions.json.")


# ----------------------------------------------------------------- roster
def render_roster(data: tb.TeamData) -> None:
    ss = st.session_state
    st.caption("What you own: level and stars of every champion. Used for the rank shown in the "
               "list and on images, and for “Hide Locked”. Locked / Unlocked have no stars.")
    if ss.get("tb_msg"):
        st.success(ss.pop("tb_msg"))

    c1, c2, c3 = st.columns(3)
    up = c1.file_uploader("Load roster (owned.json)", type="json", key="tb_upload")
    if up is not None and ss.get("tb_upload_id") != up.file_id:
        ss.tb_upload_id = up.file_id
        try:
            roster, unknown = tb.parse_roster(json.loads(up.getvalue()), data)
        except (ValueError, UnicodeDecodeError):
            st.error("That file is not a valid owned.json.")
        else:
            ss.tb_roster, ss.tb_roster_version = roster, ss.tb_roster_version + 1
            ss.tb_msg = "Roster loaded." + (f" Ignored unknown champions: {', '.join(unknown)}." if unknown else "")
            st.rerun()

    # The editor works on a base table that only changes when a roster is loaded,
    # so edits are not applied twice.
    version = ss.tb_roster_version
    if ss.get("tb_base_version") != version:
        ss.tb_base = pd.DataFrame([{"Champion": n, "Level": ss.tb_roster[n]["color"],
                                    "Stars": ss.tb_roster[n]["stars"]} for n in sorted(data.champions)])
        ss.tb_base_version = version
    edited = st.data_editor(
        ss.tb_base, key=f"tb_roster_editor_{version}", hide_index=True, width="stretch",
        height=420, disabled=["Champion"],
        column_config={
            "Level": st.column_config.SelectboxColumn(options=list(tb.TIERS), required=True),
            "Stars": st.column_config.NumberColumn(min_value=0, max_value=5, step=1,
                                                   format="%d", required=True),
        })
    ss.tb_roster = {r.Champion: tb.normalize_entry(r.Level, r.Stars) for r in edited.itertuples()}

    c2.download_button("Download owned.json", tb.roster_json(ss.tb_roster, data),
                       file_name="owned.json", mime="application/json", width="stretch")
    if c3.button("💾 Save to data/teambuilder/owned.json", width="stretch"):
        try:
            tb.save_roster(OWNED_PATH, ss.tb_roster, data)
            st.success("Saved. It loads automatically next time the app starts.")
        except OSError as exc:
            st.error(f"Could not save: {exc}")


# -------------------------------------------------------------- all teams
def render_all_teams(data: tb.TeamData, store: images.ImageStore) -> None:
    ss = st.session_state
    ts: tb.TeamsState = ss.tb_teams
    flash = ss.pop("tb_flash", None)
    if flash:
        (st.success if flash[0] == "success" else st.error)(flash[1])

    filled = [i for i, t in enumerate(ts.teams) if t.members]
    c1, c2 = st.columns(2)
    if c1.button("Generate all teams", disabled=not filled, key="tb_gen_all", width="stretch"):
        with st.spinner("Building the images…"):
            for i in filled:
                generate_team_image(data, store, i)
        st.success(f"Saved {len(filled)} images to {tb.TEAMS_DIR.name}/.")
    c2.button("Clear all teams", on_click=cb_clear_all, disabled=not filled, width="stretch")

    for i in filled:
        t = ts.teams[i]
        a, b = st.columns([7, 1], vertical_alignment="center")
        summary = ", ".join(f"{s.name} ×{s.count}" for s in tb.team_synergies(t.members, data)[:4])
        a.markdown(f"**{i + 1}. {html.escape(t.display_title(i))}:** {', '.join(t.members)}  \n"
                   f"<small>{html.escape(summary)}</small>", unsafe_allow_html=True)
        b.button("Select", key=f"tb_goto_{t.uid}", on_click=cb_goto, args=(i,), width="stretch",
                 help="Make this the current team in the Build tab")

    st.divider()
    st.markdown(f"#### Saved images ({tb.TEAMS_DIR.name}/)")
    files = tb.list_team_images()
    if not files:
        st.info("No saved team images yet. Use “Generate image” in the Build tab.")
    cols = st.columns(3)
    for n, path in enumerate(files):
        with cols[n % 3]:
            raw = path.read_bytes()
            saved = tb.read_team_png(raw)
            title = (saved.title if saved and saved.title else path.stem.replace("_", " "))
            st.image(raw, width="stretch")
            st.markdown(f"**{html.escape(title)}**" + (f" · {html.escape(saved.subtitle)}"
                                                         if saved and saved.subtitle else ""))
            if saved:
                st.caption(", ".join(saved.names))
            d1, d2, d3 = st.columns(3)
            d1.button("Load", key=f"tb_load_{path.name}", on_click=cb_load_file, args=(path.name,),
                      width="stretch", help="Open in a new team")
            d2.download_button("⬇", raw, file_name=path.name, mime="image/png",
                               key=f"tb_dl_{path.name}", width="stretch")
            d3.button("🗑", key=f"tb_rmf_{path.name}", on_click=cb_delete_file, args=(path.name,),
                      width="stretch", help="Delete this image")

    st.divider()
    st.markdown("**Load a team from another image**")
    st.caption("Works with images made by this app and by the desktop Teambuilder.")
    up = st.file_uploader("Team image (PNG)", type="png", key="tb_img_upload")
    if st.button("Load into a new team", disabled=up is None, key="tb_img_load"):
        saved = tb.read_team_png(up.getvalue())
        if saved is None:
            st.error("No team data found in that image.")
        else:
            load_saved_team(saved, "Imported team")
            st.rerun()


# ------------------------------------------------------------------- main
def render() -> None:
    try:
        data = get_data()
    except eng.DataFileError as exc:
        st.error("Could not load the team builder data:")
        for problem in exc.problems:
            st.markdown(f"- {problem}")
        return
    init_state(data)
    store = images.ImageStore()
    render_images_bar(data, store)

    t_build, t_info, t_roster, t_all = st.tabs(
        ["🧩 Build", "🔍 Champion info", "📋 My roster", "🗂️ All teams"])
    with t_roster:      # first, so the other tabs see the roster as just edited
        render_roster(data)
    with t_build:
        render_build(data, store)
    with t_info:
        render_info(data, store)
    with t_all:
        render_all_teams(data, store)
