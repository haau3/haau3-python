"""Writes every Medicare Advantage and ACA Marketplace plan offered in a state to a CSV file.

    HAAU3_API_KEY=... python examples/plans_to_csv.py NC plans.csv

It searches once and follows each page with `pages()`. Every answer is plain dicts and lists, read
with `.get()` because FHIR elements are optional.
"""

from __future__ import annotations

import csv
import sys

from haau3 import Haau3, fhir


def plan_row(plan: fhir.InsurancePlan) -> list[str]:
    kinds = [coding.get("code", "") for concept in plan.get("type", []) for coding in concept.get("coding", [])]
    return [plan.get("id", ""), plan.get("name", ""), plan.get("status", ""), " ".join(kinds)]


def main(argv: list[str], client: Haau3 | None = None) -> int:
    if len(argv) != 2:
        print("usage: plans_to_csv.py <state, such as NC> <file.csv>", file=sys.stderr)
        return 2
    state, path = argv
    with client or Haau3() as haau3, open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["id", "name", "status", "type"])
        rows = 0
        for page in haau3.pages(haau3.search_insurance_plan(coverage_area_state=state)):
            for entry in page.get("entry", []):
                resource = entry.get("resource")
                if resource is not None and resource["resourceType"] == "InsurancePlan":
                    writer.writerow(plan_row(resource))
                    rows += 1
    print(f"{rows} plans written to {path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
