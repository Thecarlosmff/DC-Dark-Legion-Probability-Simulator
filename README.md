# Draw Simulator (Streamlit)

    pip install -r requirements.txt
    streamlit run app.py

## Files
- `app.py`      Streamlit UI (Draw tab)
- `engine.py`   draw logic + data loading (no UI, no global state)
- `simulation.py` fast numpy simulation (many sessions at once, no per-pull history):
                 shards after N pulls, and pulls needed for a shard goal
- `charts.py`   pie charts + shard distribution
- `teambuilder.py` / `teambuilder_ui.py`  Team Builder logic and UI

## Data: one folder per server in `data/<server>/`
Any new folder with `pools.json` + `banners.json` appears in the Server selector.
Changes are picked up when you save (refresh the page); no restart needed.

- `pools.json`   regular items: `mythic` / `legendary` / `epic`, each with `champions` and `legacy`
- `banners.json` `[{"name": "Superman", "boosted": ["Cyborg", "The Flash", "Shazam"]}]`
                 boosted heroes must be in `pools.json` -> `mythic.champions`
- `rules.json`   (optional)
      {"name": "New servers", "extra_shards": false,
       "milestones": [{"pulls": 50, "reward": "text", "banner_shards": 0}]}

Limited Mythics that cannot be drawn (events / paid): leave them out of `pools.json`.
A limited Mythic that is only on a banner: put it in `banners.json`, not in `pools.json`.

`data/new` is currently a placeholder (copy of old, no extra shards, no milestones).

## Tabs
- Draw: pull one by one, see pity / milestones / log
- Simulation: average shards after N pulls, repeated M times (uses the sidebar server, banner and pity)
- Shard Goal: pulls needed to reach X shards of the banner champion (avg, min, max, pulls for 75/90/95% chance)
- Probabilities: chance of each star level after N pulls (several lengths at once), and exact distributions of Mythics, banner Mythics and extra shards ("Chance" = exactly, "At least" = that or more)
- Team Builder: build up to 10 teams of 5 (filters by rarity / faction AND-OR / class, hide used / locked),
  legacy piece per champion, faction bonuses reached, team images (PNG with the team inside, compatible with
  the desktop Teambuilder), champion info, and "My roster" (level + stars you own)

## Team Builder
- Left: champion list (rarity, faction and class icons, AND/OR, Hide Used / Hide Locked, search; stars show what you own).
  Right: the current team (rename it, subtitle, Show rank), slots with legacy piece, faction bonuses, "Generate image".
- Teams start at 5; "＋ Add team" creates more, "Delete" removes one. The selected team stays selected while you add.
- "Generate image" saves `teams/<team name>.png` (same name replaces). "All teams" shows everything in `teams/`
  (Load into a new team, download, delete). The team is stored inside the PNG, so images made by the desktop
  Teambuilder can also be loaded.

### Images (work offline)
Run once: `python download_images.py` (or the "Download images" button in the Team Builder). Everything goes to
`static/images/` with the same layout/names as the desktop app, so you can also copy its `data/images` folder there.
The app uses local files first and only falls back to the internet for missing ones.
Faction/class icons in the filters are served through Streamlit static serving, enabled by `.streamlit/config.toml`:
start the app with `streamlit run app.py` **from the project folder** (otherwise the filters show names instead of icons).

### Data: data/teambuilder/
- `champions.json`, `synergies.json`, `legacy_pieces.json`  game data (replace them to update)
- `owned.json`  your roster `[{"name", "stars", "color"}]`, loaded at start; "Save" in the app rewrites it
- `static/images/backgrounds/` (optional) .png/.jpg used at random as team image background
