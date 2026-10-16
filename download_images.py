"""Download every card / icon / background into static/images (run once, then no internet needed).

    python download_images.py
"""
import images
import teambuilder as tb


def main() -> None:
    data = tb.load_team_data()
    store = images.ImageStore()
    jobs = images.missing(data, store)
    print(f"{len(jobs)} images to download")
    last = [-1]

    def progress(fraction: float) -> None:
        pct = int(fraction * 100)
        if pct != last[0] and pct % 10 == 0:
            last[0] = pct
            print(f"  {pct}%")

    report = images.download_images(jobs, store, progress=progress)
    print(f"downloaded {report.downloaded}, failed {len(report.failed)}")
    for job, error in report.failed:
        print(f"  FAILED {job.kind}: {job.name} ({error})")


if __name__ == "__main__":
    main()
