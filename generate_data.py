"""
Generate a simulated shift-level OEE and downtime dataset for a flexible-packaging plant.

Adapted from the machine simulator in my Nexus manufacturing-analytics project. All data is
synthetic; no real plant data is used.

Output: data/plant_oee_downtime.csv  (one row per date x shift x machine x time category)

    Time Category = "Running"      -> Minutes of run time, plus production columns
    Time Category = <loss reason>  -> Minutes lost to that reason (production columns empty)

OEE is then computed in Tableau from the sums:
    Availability = Run minutes / Planned minutes
    Performance  = Total output / Ideal output
    Quality      = Good output / Total output
    OEE          = Availability x Performance x Quality
"""

import csv
import os
from datetime import date, timedelta

import numpy as np

SEED = 2026
START, END = date(2026, 1, 1), date(2026, 6, 30)
PLANNED_MIN = 450  # 8-hour shift minus 30 min scheduled break

SHIFTS = {"A (06-14)": 1.00, "B (14-22)": 0.985, "C (22-06)": 0.955}  # performance factor

# machine_id: (name, line, ideal kg/min, base scrap %, loss profile in minutes/shift)
MACHINES = {
    "EXT-01": ("Blown Film Extruder 1", "Extrusion", 3.2, 2.4,
               {"Unplanned Breakdown": 14, "Changeover": 18, "Material Shortage": 4, "Quality Hold": 3, "Minor Stops": 12}),
    "EXT-02": ("Blown Film Extruder 2", "Extrusion", 3.2, 2.6,
               {"Unplanned Breakdown": 18, "Changeover": 18, "Material Shortage": 4, "Quality Hold": 3, "Minor Stops": 14}),
    "PRN-01": ("Flexo Printer 1", "Printing", 2.1, 3.0,
               {"Unplanned Breakdown": 10, "Changeover": 34, "Material Shortage": 5, "Quality Hold": 6, "Minor Stops": 10}),
    "PRN-02": ("Rotogravure Printer 2", "Printing", 2.4, 3.4,
               {"Unplanned Breakdown": 11, "Changeover": 58, "Material Shortage": 5, "Quality Hold": 8, "Minor Stops": 11}),
    "LAM-01": ("Solventless Laminator", "Lamination", 2.6, 2.1,
               {"Unplanned Breakdown": 9, "Changeover": 22, "Material Shortage": 6, "Quality Hold": 7, "Minor Stops": 9}),
    "SLT-01": ("Slitter-Rewinder", "Conversion", 3.6, 1.6,
               {"Unplanned Breakdown": 7, "Changeover": 26, "Material Shortage": 7, "Quality Hold": 2, "Minor Stops": 16}),
    "POU-01": ("Pouch Maker", "Conversion", 1.4, 3.8,
               {"Unplanned Breakdown": 13, "Changeover": 30, "Material Shortage": 8, "Quality Hold": 5, "Minor Stops": 20}),
}
REASONS = ["Unplanned Breakdown", "Changeover", "Material Shortage", "Quality Hold", "Minor Stops"]


def storyline_multipliers(mid, d, shift):
    """Deliberate patterns so the dashboard has real findings to surface."""
    m = {r: 1.0 for r in REASONS}
    perf, scrap = 1.0, 1.0

    # EXT-02: bearing wear builds through March-April, fixed by a planned overhaul on 12 May.
    if mid == "EXT-02":
        if date(2026, 3, 1) <= d < date(2026, 5, 12):
            wear = (d - date(2026, 3, 1)).days / 72
            m["Unplanned Breakdown"] *= 1 + 3.2 * wear
            m["Minor Stops"] *= 1 + 1.2 * wear
            perf -= 0.06 * wear
        elif d >= date(2026, 5, 12):
            m["Unplanned Breakdown"] *= 0.45
            m["Minor Stops"] *= 0.7

    # Resin supplier delay: weeks of 9-22 February hit the whole plant.
    if date(2026, 2, 9) <= d <= date(2026, 2, 22):
        m["Material Shortage"] *= 5.5 if MACHINES[mid][1] == "Extrusion" else 3.0

    # PRN-02: SMED changeover programme from 1 April cuts changeover time ~35%.
    if mid == "PRN-02" and d >= date(2026, 4, 1):
        m["Changeover"] *= 0.65

    # LAM-01: adhesive curing issues in humid June -> quality holds and scrap.
    if mid == "LAM-01" and d.month == 6:
        m["Quality Hold"] *= 3.0
        scrap *= 1.6

    # Night shift: more minor stops, slower speeds.
    if shift.startswith("C"):
        m["Minor Stops"] *= 1.35
        m["Unplanned Breakdown"] *= 1.15

    # Sundays run a lighter schedule with fewer changeovers.
    if d.weekday() == 6:
        m["Changeover"] *= 0.5

    return m, perf, scrap


def main():
    rng = np.random.default_rng(SEED)
    rows = []
    d = START
    while d <= END:
        for shift, shift_perf in SHIFTS.items():
            for mid, (name, line, ideal_rate, base_scrap, profile) in MACHINES.items():
                mult, perf_adj, scrap_adj = storyline_multipliers(mid, d, shift)

                losses = {}
                for r in REASONS:
                    mean = profile[r] * mult[r]
                    # Breakdowns are lumpy: most shifts have none, some have a long one.
                    if r == "Unplanned Breakdown":
                        p_event = min(0.9, mean / 55)
                        val = rng.gamma(2.0, 27.5) if rng.random() < p_event else 0.0
                    else:
                        val = max(0.0, rng.normal(mean, mean * 0.45))
                    losses[r] = round(val)

                total_loss = sum(losses.values())
                if total_loss > PLANNED_MIN - 30:  # keep at least 30 min of running
                    scale = (PLANNED_MIN - 30) / total_loss
                    losses = {r: round(v * scale) for r, v in losses.items()}
                run_min = PLANNED_MIN - sum(losses.values())

                performance = np.clip(rng.normal(0.9 * shift_perf * perf_adj, 0.035), 0.6, 0.99)
                ideal_kg = ideal_rate * run_min
                total_kg = ideal_kg * performance
                scrap_pct = max(0.3, rng.normal(base_scrap * scrap_adj, 0.7)) / 100
                good_kg = total_kg * (1 - scrap_pct)
                energy = total_kg * rng.normal(0.62, 0.04) + 18 * (PLANNED_MIN - run_min) / 60

                base = {
                    "Date": d.isoformat(), "Shift": shift, "Line": line,
                    "Machine ID": mid, "Machine": f"{mid} {name}",
                }
                rows.append({**base, "Time Category": "Running", "Minutes": run_min,
                             "Ideal Output (kg)": round(ideal_kg, 1), "Total Output (kg)": round(total_kg, 1),
                             "Good Output (kg)": round(good_kg, 1), "Scrap (kg)": round(total_kg - good_kg, 1),
                             "Energy (kWh)": round(energy, 1)})
                for r in REASONS:
                    if losses[r] > 0:
                        rows.append({**base, "Time Category": r, "Minutes": losses[r]})
        d += timedelta(days=1)

    os.makedirs("data", exist_ok=True)
    cols = ["Date", "Shift", "Line", "Machine ID", "Machine", "Time Category", "Minutes",
            "Ideal Output (kg)", "Total Output (kg)", "Good Output (kg)", "Scrap (kg)", "Energy (kWh)"]
    with open("data/plant_oee_downtime.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows):,} rows")


if __name__ == "__main__":
    main()
