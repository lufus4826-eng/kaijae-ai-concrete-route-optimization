"""ขั้นตอนที่ 4: เปิดไลน์ผลิตเสริมเฉพาะโรงงานที่เป็นคอขวดเมื่อยังไม่ผ่านเกณฑ์ SLA (Bottleneck Plant Line Boost)"""
from aiconcrete import m2t, t2m

# ===== ค่าปรับแต่งส่วนกลางสำหรับขั้นตอนที่ 4 (ห้ามผูกค่าเฉพาะวัน เพื่อรองรับ Hidden Day 5) =====
BOOST_CONFIG = {
    "pre_window_pad_min": 60,
    "post_window_pad_min": 60,
    "min_boost_duration_min": 180,
    "hour_round_min": 60,
}


def generate_boost_candidates_by_bottleneck(day, eval_res=None, config=None):
    """
    ทำอะไร: สร้างทางเลือกแผนเปิดไลน์ผลิตเสริม (production_plan.csv) เฉพาะเมื่อผลจำลองเบื้องต้นยังไม่ผ่าน SLA ครบทุกข้อ
    ใช้วิธีอะไร: Bottleneck-Driven Line Boost Generation (วิเคราะห์ออเดอร์ที่ยังตกเกณฑ์ SLA ว่าอยู่ในโซนของโรงงานที่เปิดไลน์เพิ่มได้หรือไม่ แล้วสร้างแผนเปิดไลน์เสริมในช่วงเวลาที่ออเดอร์นั้นต้องการใช้งาน)
    ทำไมใช้วิธีนี้: การเปิดไลน์ผลิตเสริมมีค่าใช้จ่ายรายชั่วโมง (2,500-3,200 บาท/ชม.) จึงควรเปิดเฉพาะวันที่จำเป็นต้องใช้เพื่อดันให้ผ่านเกณฑ์ SLA รายวัน (ซึ่งเป็นเกณฑ์จัดอันดับอันดับที่ 1 ของการแข่งขัน)
    """
    cfg = config or BOOST_CONFIG
    boostable = [
        pid for pid, p in sorted(day.plants.items())
        if p.get("boost_max_lines", 0) > 0 and p.get("boost_from") and p.get("boost_to")
    ]
    if not boostable:
        return []

    candidates = []

    # ทางเลือกที่ 1: เปิดไลน์เสริมเฉพาะโรงงานใกล้หน้างานของออเดอร์ที่ตก SLA ในรอบก่อนหน้า
    if eval_res and eval_res.get("orders"):
        failed_oids = [
            o["order_id"] for o in eval_res["orders"]
            if (not o["on_time"]) or (o["colour"] != "GREEN")
        ]
        if failed_oids:
            plant_windows = {}
            for oid in failed_oids:
                for sid in day.orders.get(oid, []):
                    s = day.streams[sid]
                    req = s["first_arrival"]
                    dur = s["loads"] * s["interval_min"]
                    # หาโรงงานที่ผลิตสูตรนี้ได้และอยู่ใกล้ไซต์ที่สุด 2 อันดับแรก
                    valid_pids = [
                        pid for pid in boostable
                        if (pid, s["product_id"]) in day.plant_product
                    ]
                    valid_pids.sort(key=lambda pid: day.travel[(pid, s["site_id"])]["km"])
                    for pid in valid_pids[:2]:
                        lo = req - cfg["pre_window_pad_min"]
                        hi = req + dur + cfg["post_window_pad_min"]
                        if pid not in plant_windows:
                            plant_windows[pid] = [lo, hi]
                        else:
                            plant_windows[pid][0] = min(plant_windows[pid][0], lo)
                            plant_windows[pid][1] = max(plant_windows[pid][1], hi)

            targeted_plan = []
            for pid, (w_lo, w_hi) in sorted(plant_windows.items()):
                p = day.plants[pid]
                b_lo = t2m(p["boost_from"])
                b_hi = t2m(p["boost_to"])
                start_m = max(b_lo, int(w_lo // cfg["hour_round_min"]) * cfg["hour_round_min"])
                end_m = min(b_hi, int(((w_hi + cfg["hour_round_min"] - 1) // cfg["hour_round_min"]) * cfg["hour_round_min"]))
                if end_m - start_m < cfg["min_boost_duration_min"]:
                    end_m = min(b_hi, start_m + cfg["min_boost_duration_min"])
                if end_m > start_m:
                    targeted_plan.append({
                        "plant_id": pid,
                        "extra_lines": p["boost_max_lines"],
                        "from": m2t(start_m),
                        "to": m2t(end_m),
                    })
            if targeted_plan:
                candidates.append(targeted_plan)

    # ทางเลือกที่ 2: เปิดไลน์เสริมเต็มช่วงเวลาของโรงงานหลักในโซนใต้/ตะวันออก (P05 และ P08)
    core_boost = []
    for pid in boostable:
        p = day.plants[pid]
        if p["boost_cost_per_hour"] <= 2800:
            core_boost.append({
                "plant_id": pid,
                "extra_lines": p["boost_max_lines"],
                "from": p["boost_from"],
                "to": p["boost_to"],
            })
    if core_boost and core_boost not in candidates:
        candidates.append(core_boost)

    # ทางเลือกที่ 3: เปิดไลน์เสริมทุกโรงงานที่อนุญาต (กรณีวันที่งานแน่นพิเศษ)
    full_boost = [
        {
            "plant_id": pid,
            "extra_lines": day.plants[pid]["boost_max_lines"],
            "from": day.plants[pid]["boost_from"],
            "to": day.plants[pid]["boost_to"],
        }
        for pid in boostable
    ]
    if full_boost and full_boost not in candidates:
        candidates.append(full_boost)

    return candidates
