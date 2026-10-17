"""Update the game data (champions with skills, synergies, legacy pieces, effects).

    python update_data.py
"""
import updater


def main() -> None:
    report = updater.update_data()
    if report.aborted:
        print("NOT written, the new data failed validation:", report.aborted)
    for f in report.files:
        line = f"{f.filename}: {f.status}"
        if f.status == "updated":
            line += f" ({f.total} items, {len(f.added)} new, {len(f.removed)} removed, {f.changed} changed)"
        if f.detail:
            line += f" - {f.detail}"
        print(line)
        if f.added:
            print("   new:", ", ".join(f.added[:20]))
    if not report.updated:
        print("Nothing changed.")


if __name__ == "__main__":
    main()
