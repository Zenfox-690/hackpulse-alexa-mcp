#!/usr/bin/env python3
"""Autonomous Live Catalog Refresh & Freshness Reporter.

Executes authenticated live multi-source discovery against the production HackPulse instance,
evaluates freshness across Devpost, Devfolio, and Unstop, and produces a structured
GitHub Actions Step Summary report.
"""

import argparse
import json
import os
import sys
import httpx


def generate_markdown_summary(
    refresh_summary: dict,
    freshness_data: dict,
    threshold_hours: int,
) -> str:
    """Generates the structured GitHub Actions Step Summary markdown."""
    overall_status = refresh_summary.get("overall_status", "unknown").upper()
    is_fresh = freshness_data.get("is_fresh", False)
    freshness_state = "FRESH" if is_fresh else "STALE"
    total_records = freshness_data.get("total_hackathons", 0)
    open_records = freshness_data.get("open_hackathons", 0)

    lines = [
        "# HackPulse Catalog Refresh",
        "",
        f"**Overall status:** {overall_status}",
        "",
    ]

    sources_fresh = freshness_data.get("sources", {})
    sources_refresh = refresh_summary.get("sources", {})

    expected_sources = ["Devpost", "Devfolio", "Unstop"]
    all_source_names = list(expected_sources)
    for s in sources_refresh:
        if s not in all_source_names:
            all_source_names.append(s)

    for src in all_source_names:
        r_info = sources_refresh.get(src, {})
        f_info = sources_fresh.get(src, {})
        status = (r_info.get("status") or f_info.get("status") or "UNKNOWN").upper()
        disc = r_info.get("discovered", f_info.get("discovered_count", 0))
        added = r_info.get("added", f_info.get("added_count", 0))
        updated = r_info.get("updated", f_info.get("updated_count", 0))
        last_success = f_info.get("last_successful_at") or "None"
        err = r_info.get("error") or f_info.get("last_error")

        lines.append(f"### {src}")
        lines.append(f"- **Status:** {status}")
        lines.append(f"- **Discovered:** {disc}")
        lines.append(f"- **Added:** {added}")
        lines.append(f"- **Updated:** {updated}")
        lines.append(f"- **Last successful refresh:** {last_success}")
        if err:
            lines.append(f"- **Error:** `{err}`")
        lines.append("")

    lines.append("### Catalog Freshness")
    lines.append(f"- **State:** {freshness_state}")
    lines.append(f"- **Threshold:** {threshold_hours}h")
    lines.append(f"- **Total Opportunities:** {total_records} (Open: {open_records})")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="HackPulse Catalog Refresh Trigger")
    parser.add_argument("--url", default=os.environ.get("HACKPULSE_LIVE_URL", ""))
    parser.add_argument("--token", default=os.environ.get("CATALOG_REFRESH_TOKEN", ""))
    parser.add_argument("--retention-days", type=int, default=int(os.environ.get("RETENTION_DAYS", "30")))
    parser.add_argument("--threshold-hours", type=int, default=int(os.environ.get("THRESHOLD_HOURS", "24")))
    args = parser.parse_args()

    live_url = (args.url or "").strip().rstrip("/")
    token = (args.token or "").strip()
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    if not live_url or not token:
        msg = [
            "# HackPulse Catalog Refresh (Configuration Notice)",
            "",
            "> [!WARNING]",
            "> Automated live catalog refresh requires repository settings:",
            "> - **Variable:** `HACKPULSE_LIVE_URL` (e.g. `https://hackpulse-alexa-mcp.onrender.com`)",
            "> - **Secret:** `CATALOG_REFRESH_TOKEN` (matching the environment variable on Render)",
            "",
            "Refresh execution was skipped because credentials are not configured in this environment.",
        ]
        summary_md = "\n".join(msg)
        print(summary_md)
        if summary_path and os.path.exists(os.path.dirname(summary_path)):
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(summary_md + "\n")
        # If running manually via workflow_dispatch, notify operator by failing
        if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
            sys.exit(1)
        sys.exit(0)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "HackPulse-CatalogRefresh-Bot/1.0",
    }

    refresh_endpoint = f"{live_url}/sync/refresh?retention_days={args.retention_days}"
    freshness_endpoint = f"{live_url}/catalog/freshness?threshold_hours={args.threshold_hours}"

    print(f"Triggering authenticated refresh at {refresh_endpoint}...")

    with httpx.Client(timeout=90.0) as client:
        # Step 1: POST to /sync/refresh
        try:
            refresh_resp = client.post(refresh_endpoint, headers=headers)
        except Exception as e:
            err_msg = f"Network failure connecting to {refresh_endpoint}: {e}"
            print(f"ERROR: {err_msg}", file=sys.stderr)
            if summary_path:
                with open(summary_path, "a", encoding="utf-8") as f:
                    f.write(f"# HackPulse Catalog Refresh\n\n**Overall status:** FAILURE\n\n- Connection error: `{err_msg}`\n")
            sys.exit(1)

        if refresh_resp.status_code == 401:
            print("ERROR: Authentication failed. Invalid or expired CATALOG_REFRESH_TOKEN.", file=sys.stderr)
            if summary_path:
                with open(summary_path, "a", encoding="utf-8") as f:
                    f.write("# HackPulse Catalog Refresh\n\n**Overall status:** FAILURE\n\n- Authentication rejected: HTTP 401 Unauthorized.\n")
            sys.exit(1)

        if refresh_resp.status_code != 200:
            print(f"ERROR: Refresh endpoint returned HTTP {refresh_resp.status_code}: {refresh_resp.text}", file=sys.stderr)
            if summary_path:
                with open(summary_path, "a", encoding="utf-8") as f:
                    f.write(f"# HackPulse Catalog Refresh\n\n**Overall status:** FAILURE\n\n- HTTP {refresh_resp.status_code}: {refresh_resp.text[:200]}\n")
            sys.exit(1)

        refresh_data = refresh_resp.json()
        summary = refresh_data.get("refresh_summary", {})

        # Step 2: GET /catalog/freshness
        print(f"Querying catalog freshness at {freshness_endpoint}...")
        try:
            freshness_resp = client.get(freshness_endpoint, timeout=30.0)
            freshness_data = freshness_resp.json() if freshness_resp.status_code == 200 else {}
        except Exception as e:
            print(f"Warning: Failed to fetch freshness status: {e}", file=sys.stderr)
            freshness_data = {}

    summary_md = generate_markdown_summary(
        refresh_summary=summary,
        freshness_data=freshness_data,
        threshold_hours=args.threshold_hours,
    )

    print("\n" + summary_md)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(summary_md + "\n")

    overall_status = summary.get("overall_status", "unknown")
    is_fresh = freshness_data.get("is_fresh", False)

    # Fail the CI step if the refresh totally failed or catalog is stale
    if overall_status == "failure":
        print("ERROR: All discovery sources failed during catalog refresh.", file=sys.stderr)
        sys.exit(1)

    if not is_fresh and freshness_data:
        print(f"WARNING/FAIL: Catalog is STALE beyond {args.threshold_hours}h threshold.", file=sys.stderr)
        sys.exit(1)

    print("Catalog refresh completed successfully.")


if __name__ == "__main__":
    main()
