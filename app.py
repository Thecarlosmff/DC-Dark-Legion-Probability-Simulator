"""Run with:  streamlit run app.py"""
from __future__ import annotations

import streamlit as st

import charts
import engine as eng
import pandas as pd
import simulation
import teambuilder_ui

st.set_page_config(page_title="DC Dark Legion – Draw Simulator", page_icon="🎲", layout="wide")

RARITY_COLOR = {eng.MYTHIC: "red", eng.LEGENDARY: "orange", eng.EPIC: "violet"}
LOG_LIMIT = 300  # newest pulls shown in the log
DATA_FILES = ("pools.json", "banners.json", "rules.json")


# ------------------------------------------------------------- data files
@st.cache_data
def _load_catalog(profile: str, stamp: tuple) -> eng.Catalog:
    # `stamp` (file modification times) is only a cache key: editing a data
    # file invalidates the cache, so changes show up on the next rerun.
    return eng.load_catalog(profile)


def get_catalog(profile: str) -> eng.Catalog:
    folder = eng.DATA_DIR / profile
    stamp = tuple((folder / f).stat().st_mtime if (folder / f).exists() else 0.0
                  for f in DATA_FILES)
    return _load_catalog(profile, stamp)


def profile_label(profile: str) -> str:
    try:
        return get_catalog(profile).rules.name or profile
    except eng.DataFileError:
        return f"{profile} (invalid data)"


# ---------------------------------------------------------------- sidebar
def sidebar(profile: str, catalog: eng.Catalog) -> tuple[str, eng.SessionParams]:
    """Render the session parameters; return the current values."""
    rules = catalog.rules
    st.sidebar.header("Session Parameters")
    if st.session_state.get("banner") not in catalog.banners:  # other server / removed
        st.session_state.pop("banner", None)
    banner = st.sidebar.selectbox("Banner", list(catalog.banners), key="banner")
    st.sidebar.caption("Boosted: " + ", ".join(catalog.banners[banner]))

    mythic = st.sidebar.number_input("Mythic pity", 0, eng.MAX_MYTHIC_PITY, 0, key="p_mythic")
    legendary = st.sidebar.number_input(
        "Legendary pity", 0, eng.MAX_LEGENDARY_PITY, 0, key="p_legendary")
    since = st.sidebar.number_input(
        "Non-banner Mythics since last limited", 0, eng.MAX_BANNER_PITY, 0, key="p_since")
    nonlimited = 0
    if rules.extra_shards:  # only needed to know how many bonus-shard rolls are left
        nonlimited = st.sidebar.number_input(
            "Non-limited Mythics pulled (this banner)", 0, 100_000, 0, key="p_nonlimited")
    on_banner = 0
    if rules.milestones:
        on_banner = st.sidebar.number_input(
            "Pulls already done on this banner", 0, 100_000, 0, key="p_on_banner")

    params = eng.SessionParams(mythic, legendary, since, nonlimited, on_banner)
    # Remembered so callbacks (Reset) can read the values the user sees.
    st.session_state["_inputs"] = (profile, banner, params)
    return banner, params


MAX_SHARDS = simulation.STAR_LEVELS[-1][0]  # 1040 = 5 Red Stars


def level_label(shards: int) -> str:
    """'Purple ★ (240)'-style label, like the old app's star selector."""
    if shards == 0:
        return "No stars (0)"
    return f"{simulation.level_stars(simulation.LEVEL_NAMES[shards])} ({shards})"


def owned_shards_input() -> int:
    """Current level + extra shards of the banner champion -> total shards."""
    st.sidebar.header("Your champion")
    level = st.sidebar.selectbox(
        "Current level", [0] + [shards for shards, _ in simulation.STAR_LEVELS],
        format_func=level_label, key="owned_level")
    extra = st.sidebar.number_input(
        "Shards on top of that level", 0, MAX_SHARDS, 0, key="owned_extra")
    total = min(level + extra, MAX_SHARDS)
    st.sidebar.caption(f"**Total shards: {total}** · used by the Simulation and "
                       "Shard Goal tabs")
    return total


# -------------------------------------------------------------- callbacks
def do_draw(n: int) -> None:
    st.session_state.draw.draw_many(int(n))


def do_reset() -> None:
    profile, banner, params = st.session_state["_inputs"]
    try:
        st.session_state.draw.reset(banner, params, get_catalog(profile))
    except eng.DataFileError:
        pass  # the rerun right after shows the error


# ---------------------------------------------------------------- display
def format_pull(index: int, pull: eng.Pull, banner: str) -> str:
    text = pull.item
    if pull.extra_shards:
        text += f" + {pull.extra_shards} {banner} shards"
    line = f"- `#{index}` 🎲 :{RARITY_COLOR[pull.rarity]}[{text}]"
    if pull.is_banner:
        line += " ⭐" + (" *(guaranteed)*" if pull.forced_banner else "")
    if pull.forced_mythic:
        line += " *(pity)*"
    for m in pull.milestones:
        line += f" 🏆 **{m.pulls} pulls:** {m.reward}"
    return line


def render_log(state: eng.DrawState) -> None:
    with st.container(height=460, border=True):
        if not state.history:
            st.caption("No pulls yet. Press a Draw button!")
            return
        total = state.total_pulls
        start = max(0, total - LOG_LIMIT)
        lines = [format_pull(i + 1, state.history[i], state.banner)
                 for i in range(total - 1, start - 1, -1)]  # newest first
        if start:
            lines.append(f"*…{start} older pulls not shown (Export has all of them).*")
        st.markdown("\n".join(lines))


def render_milestones(state: eng.DrawState) -> None:
    milestones = state.rules.milestones
    if not milestones:
        return
    done = state.pulls_on_banner
    nxt = state.next_milestone()
    if nxt:
        prev = max((m.pulls for m in milestones if m.pulls <= done), default=0)
        st.progress((done - prev) / (nxt.pulls - prev),
                    text=f"Next milestone at {nxt.pulls} pulls ({nxt.pulls - done} to go)")
    else:
        st.progress(1.0, text="All milestones reached")
    with st.expander("Milestone rewards", expanded=True):
        lines = []
        for m in milestones:
            extra = f" (+{m.banner_shards} {state.banner} shards)" if m.banner_shards else ""
            lines.append(f"- {'✅' if m.pulls <= done else '⬜'} **{m.pulls}** pulls: {m.reward}{extra}")
        st.markdown("\n".join(lines))


def render_breakdown(state: eng.DrawState) -> None:
    rules = state.rules
    st.subheader("📊 Session Results")
    totals = state.rarity_totals

    metrics: list[tuple[str, int]] = [("Pulls", state.total_pulls)]
    if rules.extra_shards:
        metrics.append((f"Extra {state.banner} shards", state.extra_shards_total))
    if rules.milestones:
        metrics.append(("Pulls on banner", state.pulls_on_banner))
    metrics += [("Mythic pity", state.mythic_pity), ("Legendary pity", state.legendary_pity),
                ("Non-limited since last limited", state.non_banner_since_last)]
    if rules.extra_shards:
        metrics.append(("Non-limited this banner", state.non_limited_pulled))
    metrics += [("Forced Mythics", state.forced_mythic_count),
                ("Forced Banner", state.forced_banner_count)]
    cols = st.columns(2)
    for i, (label, value) in enumerate(metrics):
        cols[i % 2].metric(label, value)

    render_milestones(state)

    st.markdown(
        f"**Mythic:** {totals[eng.MYTHIC]} (Banner {state.banner_pulls}x) · "
        f"**Legendary:** {totals[eng.LEGENDARY]} · **Epic:** {totals[eng.EPIC]}"
    )

    show_epics = st.toggle("Show Epics in item list", value=False)
    for label, items in state.grouped_results().items():
        if not items or (label.startswith(eng.EPIC) and not show_epics):
            continue
        with st.expander(f"{label} ({sum(c for _, c in items)})"):
            st.markdown("\n".join(f"- {name}: **{count}**" for name, count in items))



# ------------------------------------------------------------- simulation
def render_simulation(profile: str, catalog: eng.Catalog, banner: str,
                      params: eng.SessionParams, owned: int) -> None:
    rules = catalog.rules
    st.caption("Runs many independent sessions starting from the sidebar "
               "parameters (server, banner, pity) and averages the results.")
    c1, c2 = st.columns(2)
    pulls = int(c1.number_input("Pulls per simulation", 1, 100_000, 1000, key="sim_pulls"))
    sessions = int(c2.number_input("Simulations", 1, 1_000_000, 10_000, key="sim_n"))
    st.caption(f"Banner shards you already own: **{owned}** (set in the sidebar, "
               "under “Your champion”).")

    too_big = pulls * sessions > simulation.MAX_TOTAL_DRAWS
    if too_big:
        st.warning(f"That is {pulls * sessions:,} pulls in total; the limit is "
                   f"{simulation.MAX_TOTAL_DRAWS:,}. Lower the pulls or simulations.")
    if st.button("Run simulation", type="primary", disabled=too_big):
        bar = st.progress(0.0, text="Simulating…")
        result = simulation.simulate(
            catalog, banner, params, pulls, sessions,
            progress=lambda f: bar.progress(f, text=f"Simulating… {f:.0%}"))
        bar.empty()
        st.session_state.sim = {
            "result": result, "banner": banner, "rules": rules,
            "server": rules.name or profile, "params": params,
        }

    sim = st.session_state.get("sim")
    if sim is None:
        st.info("Choose the settings and press **Run simulation**.")
        return
    show_simulation(sim, owned)


def show_simulation(sim: dict, owned: int) -> None:
    res: simulation.SimulationResult = sim["result"]
    rules: eng.Rules = sim["rules"]
    p: eng.SessionParams = sim["params"]
    st.markdown(
        f"**{res.sessions:,} simulations × {res.pulls:,} pulls** · server "
        f"{sim['server']} · banner {sim['banner']} · starting pity "
        f"Mythic {p.mythic_pity}, Legendary {p.legendary_pity}"
    )

    shards = res.banner_shards + owned
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Average banner shards", f"{shards.mean():.2f}")
    m2.metric("Median", f"{int(pd.Series(shards).median())}")
    lo, hi = (int(x) for x in pd.Series(shards).quantile([0.05, 0.95]))
    m3.metric("90% of runs between", f"{lo} – {hi}")
    m4.metric("Min – Max", f"{int(shards.min())} – {int(shards.max())}")
    if owned:
        st.caption(f"Includes the {owned} shards you already own.")

    mean = lambda a: float(a.mean())
    rows = [
        (f"{sim['banner']} copies (40 shards each)", mean(res.banner_copies),
         mean(res.banner_copies) * simulation.BANNER_COPY_SHARDS),
    ]
    if rules.extra_shards:
        rows.append(("Extra banner shards (bonus on non-limited Mythics)", None, mean(res.extra_shards)))
    if res.milestone_shards:
        rows.append(("Milestone shards", None, float(res.milestone_shards)))
    rows.append((f"Total {sim['banner']} shards", None, mean(res.banner_shards)))
    rows += [
        ("Mythic champions, non-banner (10 shards each)", mean(res.mythic_champions),
         mean(res.mythic_champions) * simulation.MYTHIC_CHAMPION_SHARDS),
        ("Mythic legacy pieces", mean(res.mythic_legacy), None),
        ("Legendary champions (10 shards each)", mean(res.legendary_champions),
         mean(res.legendary_champions) * simulation.LEGENDARY_CHAMPION_SHARDS),
        ("Legendary legacy pieces", mean(res.legendary_legacy), None),
        ("Epic champions (1 shard each)", mean(res.epic_champions),
         mean(res.epic_champions) * simulation.EPIC_CHAMPION_SHARDS),
        ("Epic legacy pieces", mean(res.epic_legacy), None),
        ("Forced Mythics (50-pull pity)", mean(res.forced_mythic), None),
        ("Forced banner Mythics (3-Mythic pity)", mean(res.forced_banner), None),
    ]
    st.markdown("**Average per simulation**")
    st.dataframe(
        pd.DataFrame(
            [{"Item": n, "Avg count": "" if c is None else f"{c:.2f}",
              "Avg shards": "" if s is None else f"{s:.2f}"} for n, c, s in rows]),
        hide_index=True, width="stretch")
    if res.milestones_reached:
        st.caption("Milestones reached: " + ", ".join(
            f"{m.pulls} pulls ({m.reward})" for m in res.milestones_reached))

    left, right = st.columns([3, 2])
    with left:
        st.pyplot(charts.shard_histogram(res, owned), width="content")
    with right:
        st.markdown("**Chance to reach each level**")
        table = [{"Level": n, "Shards": needed, "Chance": f"{prob:.1%}"}
                 for n, needed, prob in res.chance_to_reach(owned) if prob >= 0.0005]
        if table:
            st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch", height=380)
        else:
            st.caption("No new level reachable in these settings.")



# ------------------------------------------------------------ shard goals
GOAL_SHARDS_PER_PULL = 0.3   # deliberately low: only used to estimate the run time


def parse_ints(text: str) -> list[int]:
    values = []
    for token in text.replace(";", ",").split(","):
        token = token.strip()
        if token:
            if not token.isdigit():
                raise ValueError(f"'{token}' is not a whole number")
            values.append(int(token))
    return values


def render_goals(profile: str, catalog: eng.Catalog, banner: str,
                 params: eng.SessionParams, owned: int) -> None:
    rules = catalog.rules
    st.caption("How many pulls do you need to reach a number of shards of the banner "
               "champion? Starts from the sidebar parameters (server, banner, pity).")
    c1, c2 = st.columns([2, 1])
    targets_text = c1.text_input("Shard targets (total shards, comma separated)", "240",
                                 key="goal_targets",
                                 help="e.g. 80, 240, 440. Levels: 240 = 1 Purple Star, 440 = 1 Gold Star.")
    sessions = int(c2.number_input("Simulations", 1, 200_000, 10_000, key="goal_n"))
    thresholds_text = st.text_input("Pulls to check (optional, comma separated)", "",
                                    key="goal_thresholds",
                                    help="Shows the chance of reaching each target within that many pulls.")
    st.caption(f"Banner shards you already own: **{owned}** (set in the sidebar, "
               "under “Your champion”).")

    try:
        targets = parse_ints(targets_text)
        thresholds = parse_ints(thresholds_text)
    except ValueError as exc:
        st.error(f"Could not read the numbers: {exc}.")
        return
    if not targets:
        st.info("Enter at least one shard target.")
        return
    if max(targets) > 5000:
        st.error("Targets above 5000 shards are not supported.")
        return

    remaining = {t: t - owned for t in targets if t - owned >= 1}
    est = sessions * (max(remaining.values()) / GOAL_SHARDS_PER_PULL if remaining else 0)
    too_big = est > simulation.MAX_TOTAL_DRAWS
    if too_big:
        st.warning("That would take too long. Lower the number of simulations or the targets.")
    if not remaining:
        st.success("You already own enough shards for every target.")

    if st.button("Run shard goal simulation", type="primary", key="goal_run",
                 disabled=too_big or not remaining):
        bar = st.progress(0.0, text="Simulating…")
        result = simulation.simulate_goals(
            catalog, banner, params, sorted(set(remaining.values())), sessions,
            progress=lambda f: bar.progress(f, text=f"Simulating… {f:.0%}"))
        bar.empty()
        st.session_state.goal = {
            "result": result, "targets": targets, "owned": owned, "banner": banner,
            "server": rules.name or profile, "params": params, "thresholds": thresholds,
        }

    goal = st.session_state.get("goal")
    if goal is None:
        st.info("Choose the targets and press **Run shard goal simulation**.")
        return
    if goal["owned"] != owned:
        st.info(f"The shards in the sidebar ({owned}) differ from this run "
                f"({goal['owned']}). Press **Run shard goal simulation** to update.")
    show_goals(goal)


def show_goals(goal: dict) -> None:
    res: simulation.GoalResult = goal["result"]
    owned: int = goal["owned"]
    p: eng.SessionParams = goal["params"]
    st.markdown(
        f"**{res.sessions:,} simulations** · server {goal['server']} · banner "
        f"{goal['banner']} · owned {owned} shards · starting pity "
        f"Mythic {p.mythic_pity}, Legendary {p.legendary_pity}"
    )
    if res.capped:
        st.warning(f"{res.capped} simulations hit the safety limit and are counted at the limit.")

    rows, labels = [], {}
    for total in goal["targets"]:
        need = total - owned
        level = simulation.LEVEL_NAMES.get(total, "")
        row = {"Target": total, "Level": level, "Still needed": max(need, 0)}
        if need < 1:
            row["Avg pulls"] = "already reached"
        else:
            stats = res.stats(need)
            row.update({
                "Avg pulls": f"{stats['mean']:.1f}", "Median": int(stats["median"]),
                "Min": stats["min"], "Max": stats["max"],
                "75% chance": res.pulls_for_chance(need, 0.75),
                "90% chance": res.pulls_for_chance(need, 0.90),
                "95% chance": res.pulls_for_chance(need, 0.95),
            })
            labels[need] = f"{total} shards" + (f" ({level})" if level else "")
        rows.append(row)
    st.dataframe(pd.DataFrame(rows).fillna(""), hide_index=True, width="stretch")
    st.caption("“75% / 90% / 95% chance” = pulls you need to have that probability of "
               "reaching the target.")

    if goal["thresholds"]:
        table = {}
        for total in goal["targets"]:
            need = total - owned
            if need >= 1:
                table[f"{total} shards"] = [f"{res.chance_within(need, x):.1%}"
                                            for x in goal["thresholds"]]
        if table:
            st.markdown("**Chance of reaching the target within X pulls**")
            df = pd.DataFrame(table, index=pd.Index(goal["thresholds"], name="Pulls ≤ X"))
            st.dataframe(df, width="stretch")

    if labels:
        st.pyplot(charts.pulls_cdf(res, labels), width="content")



# ---------------------------------------------------------- probabilities
def render_probabilities(profile: str, catalog: eng.Catalog, banner: str,
                         params: eng.SessionParams, owned: int) -> None:
    st.caption("Chances after a fixed number of pulls: how many shards or levels, how many "
               "Mythics, how many banner Mythics. Uses the sidebar settings (server, banner, "
               "pity, shards you own).")
    c1, c2 = st.columns([2, 1])
    lengths_text = c1.text_input("Pull lengths (comma separated)", "1000", key="prob_lengths",
                                 help="Each length is simulated separately, e.g. 500, 1000, 1040.")
    sessions = int(c2.number_input("Simulations", 1, 200_000, 10_000, key="prob_n"))
    st.caption(f"Banner shards you already own: **{owned}** (set in the sidebar, "
               "under “Your champion”).")

    try:
        lengths = list(dict.fromkeys(parse_ints(lengths_text)))  # unique, same order
    except ValueError as exc:
        st.error(f"Could not read the numbers: {exc}.")
        return
    if not lengths:
        st.info("Enter at least one number of pulls.")
        return
    if min(lengths) < 1 or max(lengths) > 100_000:
        st.error("Each pull length must be between 1 and 100,000.")
        return

    too_big = sum(lengths) * sessions > simulation.MAX_TOTAL_DRAWS
    if too_big:
        st.warning("That would take too long. Lower the pull lengths or the simulations.")
    if st.button("Run probability simulation", type="primary", key="prob_run", disabled=too_big):
        bar = st.progress(0.0, text="Simulating…")
        results = {}
        for i, length in enumerate(lengths):
            results[length] = simulation.simulate(
                catalog, banner, params, length, sessions,
                progress=lambda f, i=i: bar.progress(
                    (i + f) / len(lengths), text=f"Simulating… {(i + f) / len(lengths):.0%}"))
        bar.empty()
        st.session_state.prob = {
            "results": results, "banner": banner, "rules": catalog.rules,
            "server": catalog.rules.name or profile, "params": params,
        }

    prob = st.session_state.get("prob")
    if prob is None:
        st.info("Choose the pull lengths and press **Run probability simulation**.")
        return
    show_probabilities(prob, owned)


def distribution_view(values, color: str, title: str, xlabel: str) -> None:
    unique, chance, at_least = simulation.count_distribution(values)
    left, right = st.columns([3, 2])
    left.pyplot(charts.count_chart(values, color, title, xlabel), width="content")
    right.caption(f"Average: **{values.mean():.2f}**")
    right.dataframe(
        pd.DataFrame({xlabel: unique, "Chance": [f"{c:.2%}" for c in chance],
                      "At least": [f"{a:.2%}" for a in at_least]}),
        hide_index=True, width="stretch", height=340)


def show_probabilities(prob: dict, owned: int) -> None:
    results: dict[int, simulation.SimulationResult] = prob["results"]
    rules: eng.Rules = prob["rules"]
    p: eng.SessionParams = prob["params"]

    lengths = list(results)
    if len(lengths) > 1:
        if st.session_state.get("prob_pick") not in lengths:
            st.session_state.pop("prob_pick", None)
        length = st.radio("Pull length", lengths, horizontal=True, key="prob_pick",
                          format_func=lambda n: f"{n:,} pulls")
    else:
        length = lengths[0]
    res = results[length]
    st.markdown(
        f"**{res.sessions:,} simulations × {length:,} pulls** · server {prob['server']} · "
        f"banner {prob['banner']} · starting pity Mythic {p.mythic_pity}, "
        f"Legendary {p.legendary_pity}")

    t_levels, t_mythics, t_banner, t_extra = st.tabs(
        ["⭐ Banner shards", "Mythics", "Banner Mythics", "Extra shards"])

    with t_levels:
        rows = simulation.level_buckets(res, owned)
        title = f"Level after {length:,} pulls" + (f" (starting with {owned} shards)" if owned else "")
        left, right = st.columns([3, 2])
        left.pyplot(charts.bucket_chart(rows, title), width="content")
        right.markdown("**Where you end up**")
        right.dataframe(
            pd.DataFrame([{
                "Level": "Locked (below 40)" if r["shards"] == 0
                else f"{simulation.level_stars(r['level'])} ({r['shards']})",
                "Chance": f"{r['chance']:.2%}", "At least": f"{r['at_least']:.2%}",
            } for r in rows]),
            hide_index=True, width="stretch", height=340)
        st.caption("“Chance” = ending exactly at that level. “At least” = ending at "
                   "that level or higher.")
        stats = simulation.summary_stats(res)
        with st.expander("Summary statistics (shards gained)"):
            st.dataframe(pd.DataFrame([
                ("Mean shards", f"{stats['mean']:.2f}"),
                ("Median", f"{stats['median']:.1f}"),
                ("Q1 (25%)", f"{stats['q1']:.1f}"), ("Q3 (75%)", f"{stats['q3']:.1f}"),
                ("Min shards", f"{stats['min']:.0f}"), ("Max shards", f"{stats['max']:.0f}"),
                ("Std deviation", f"{stats['std']:.2f}"),
                ("Avg Mythic pull rate", f"{stats['mythic_rate']:.4%}"),
                ("Expected pulls per Mythic", f"{stats['pulls_per_mythic']:.2f}"),
                ("Avg limited Mythic pull rate", f"{stats['limited_rate']:.4%}"),
                ("Expected pulls per limited", f"{stats['pulls_per_limited']:.2f}"),
            ], columns=["Statistic", "Value"]), hide_index=True, width="stretch")

    with t_mythics:
        distribution_view(res.mythics, "lightgreen",
                          f"Mythics in {length:,} pulls (all, banner included)", "Mythics")
    with t_banner:
        distribution_view(res.banner_copies, "darkred",
                          f"{prob['banner']} copies in {length:,} pulls", "Banner Mythics")
    with t_extra:
        if rules.extra_shards:
            distribution_view(res.extra_shards, "skyblue",
                              f"Extra banner shards in {length:,} pulls", "Extra shards")
        else:
            st.info("This server has no extra shards.")


# ------------------------------------------------------------------- main
profiles = eng.list_profiles()
if not profiles:
    st.error(f"No server profiles found. Expected folders like `{eng.DATA_DIR / 'old'}` "
             "containing pools.json and banners.json.")
    st.stop()

st.sidebar.header("Server")
if st.session_state.get("profile") not in profiles:
    st.session_state.pop("profile", None)
profile = st.sidebar.selectbox("Server", profiles, key="profile",
                               format_func=profile_label, label_visibility="collapsed")
try:
    catalog = get_catalog(profile)
except eng.DataFileError as exc:
    st.error(f"Could not load the data files of server profile `{profile}`:")
    for problem in exc.problems:
        st.markdown(f"- {problem}")
    st.stop()

banner, params = sidebar(profile, catalog)
owned = owned_shards_input()

if "draw" not in st.session_state:
    st.session_state.draw = eng.DrawState(banner, params, catalog=catalog)
state: eng.DrawState = st.session_state.draw

# Sidebar, server or data files changed since the session started
if (banner, params, catalog) != (state.banner, state.params, state.catalog):
    if not state.history:
        state.reset(banner, params, catalog)  # nothing drawn yet: apply silently
    else:
        st.warning("Parameters, server or data files changed since this session "
                   "started. Press **Reset** to apply them.")

st.title("🎲 Draw Simulator")
st.caption(
    f"Server: **{state.rules.name or profile}** · Banner: **{state.banner}** · "
    f"Base rates: Mythic {eng.MYTHIC_RATE:.0%} (pity {eng.MYTHIC_GUARANTEE}), "
    f"Legendary {eng.LEGENDARY_RATE:.0%} (pity {eng.LEGENDARY_GUARANTEE})."
)

tab_draw, tab_sim, tab_goal, tab_prob, tab_team = st.tabs(
    ["🎲 Draw", "📈 Simulation", "🎯 Shard Goal", "🎰 Probabilities", "🛡️ Team Builder"])
with tab_sim:
    render_simulation(profile, catalog, banner, params, owned)
with tab_goal:
    render_goals(profile, catalog, banner, params, owned)
with tab_prob:
    render_probabilities(profile, catalog, banner, params, owned)
with tab_team:
    teambuilder_ui.render()
with tab_draw:
    b1, b2, b3, b4 = st.columns([1, 1, 1, 1], vertical_alignment="bottom")
    b1.button("Draw (Single)", on_click=do_draw, args=(1,), width="stretch")
    b2.button("Draw (10)", on_click=do_draw, args=(10,), width="stretch")
    custom_n = b3.number_input("Pulls", 1, 100_000, 50, key="custom_n",
                               label_visibility="collapsed")
    b4.button("Draw (X)", on_click=do_draw, args=(custom_n,), width="stretch")

    left, right = st.columns([2, 1])
    with left:
        render_log(state)
        r1, r2 = st.columns(2)
        r1.button("Reset", on_click=do_reset, width="stretch")
        r2.download_button(
            "Export draws (.txt)",
            data="\n".join(p.item for p in state.history),
            file_name="draws.txt",
            mime="text/plain",
            disabled=not state.history,
            width="stretch",
        )
    with right:
        render_breakdown(state)

    st.divider()
    st.subheader("Charts")
    kind = st.radio("Chart", charts.CHART_KINDS, horizontal=True, label_visibility="collapsed")
    if state.history:
        st.pyplot(charts.build_chart(state, kind), width="content")
    else:
        st.caption("Draw something to see the charts.")
