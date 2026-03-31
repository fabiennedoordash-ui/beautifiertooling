#!/usr/bin/env python3
"""Generate GitHub Actions step summary from results.json."""
import json
import os

results_path = "output/results.json"

if not os.path.exists(results_path):
    print("No results file found.")
    exit(0)

with open(results_path) as f:
    data = json.load(f)

total = len(data)
success = sum(1 for r in data if r["status"] == "success")
failed = sum(1 for r in data if "failed" in r["status"])
uploaded = sum(1 for r in data if r.get("imgbb_url"))

print("## Beautifier Results\n")
print("| Metric | Count |")
print("|--------|-------|")
print(f"| Total | {total} |")
print(f"| Success | {success} |")
print(f"| Uploaded | {uploaded} |")
print(f"| Failed | {failed} |")

ibb_links = [r for r in data if r.get("imgbb_url")]
if ibb_links:
    print("\n### imgbb Links\n")
    for r in ibb_links:
        name = r.get("enhanced", "").split("/")[-1]
        url = r["imgbb_url"]
        print(f"- [{name}]({url})")
