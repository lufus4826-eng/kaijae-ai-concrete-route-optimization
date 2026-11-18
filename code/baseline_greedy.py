"""AI Concrete — Level 1 starting point. Improve it!

Idea: take every load in order of the time the customer wants it, try the 2 nearest plants that can make the
product and every truck of the right size, keep the option that arrives closest to the target time. Uses the
official simulator (aiconcrete.Sim) so what you see is what the judges compute — with the data you give it.

What it does NOT do yet (your job): true plant capacity, traffic events, school / closure windows, customer
behaviour, morning repositioning, production boost, asking customers to move (order_changes.csv), cost, CO2 ...
"""
import math

from aiconcrete import Sim, m2t


def plan_day(day):
    """Return (plan_rows, order_changes_rows, production_rows) for one day."""
    sim = Sim(day)
    loads = []
    for sid, s in day.streams.items():
        for k in range(1, s["loads"] + 1):
            loads.append((s["first_arrival"] + (k - 1) * s["interval_min"], sid, k))   # declared rhythm
    loads.sort()
    rows, last = [], {}
    for target, sid, k in loads:
        s = day.streams[sid]
        ok = [p for p in day.plants if (p, s["product_id"]) in day.plant_product]
        plants = sorted(ok, key=lambda p: day.travel[(p, s["site_id"])]["km"])[:2]
        best = None
        for pid in plants:
            p = day.plants[pid]
            planned = max(last.get(sid, 0),
                          target - day.travel[(pid, s["site_id"])]["normal"] - p["mix_load_min"] - p["qc_min"])
            for tid, t in day.trucks.items():
                if t["size"] != s["truck_size"] or (t["allowed"] is not None and pid not in t["allowed"]):
                    continue
                r = sim.preview(sid, k, pid, tid, round(planned))
                if r["problems"]:
                    continue
                score = abs(r["arrive"] - target)
                if best is None or score < best[0]:
                    best = (score, r)
        r = best[1]
        if r["load_start"] != int(r["load_start"]):   # CSV holds whole minutes
            r = sim.preview(sid, k, r["plant_id"], r["truck_id"], math.ceil(r["load_start"]))
        r["planned"] = r["load_start"]
        sim.commit(r)
        last[sid] = r["load_start"]
        rows.append(dict(stream_id=sid, seq=k, plant_id=r["plant_id"], truck_id=r["truck_id"],
                         load_start=m2t(r["load_start"])))
    rows.sort(key=lambda r: (r["load_start"], r["stream_id"], r["seq"]))
    return rows, [], []
