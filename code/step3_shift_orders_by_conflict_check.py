"""ขั้นตอนที่ 3: เลื่อนเวลานัดส่งคอนกรีตเที่ยวแรกล่วงหน้าเพื่อหลบช่วงเวลาห้ามเข้าพื้นที่ รถติดหนัก และลดความแออัด (Order Shift by Conflict & Congestion Check)"""
from aiconcrete import ON_TIME_TOL, m2t

# ===== ค่าปรับแต่งส่วนกลางสำหรับขั้นตอนที่ 3 (ห้ามผูกค่าเฉพาะวัน เพื่อรองรับ Hidden Day 5) =====
SHIFT_CONFIG = {
    "post_ban_buffer_min": 5,
    "pre_ban_finish_buffer_min": 30,
    "ban_start_lookahead_min": 30,
    "ban_end_Check_pad_min": 15,
    "mega_order_min_loads": 20,
    "mega_order_min_streams": 2,
    "mega_shift_candidates_min": [90, 120, 150],
    "morning_congestion_window": (480, 690),   # 08:00 - 11:30
    "afternoon_congestion_window": (780, 960), # 13:00 - 16:00
    "earlier_relief_min_loads": 3,
    "round_step_min": 5,
}


def _round_to_step(minutes, step=5):
    """
    ทำอะไร: ปัดเศษเวลา (นาทีหลังเที่ยงคืน) ให้ลงตัวตามขั้นนาทีที่กำหนด (เช่น ทุก 5 นาที)
    ใช้วิธีอะไร: Integer Rounding Calculation
    ทำไมใช้วิธีนี้: เพื่อให้เวลาที่ขอเลื่อนใน order_changes.csv เป็นเวลามาตรฐาน (เช่น 08:35, 10:00, 10:30) สอดคล้องกับตารางเดินรถ
    """
    return int(round(minutes / step) * step)


def estimate_order_pour_Span(day, oid):
    """
    ทำอะไร: ประมาณช่วงเวลาที่ออเดอร์ต้องใช้หน้างานตั้งแต่เที่ยวแรกถึงเที่ยวสุดท้าย (req_start, est_end, total_loads)
    ใช้วิธีอะไร: Stream Duration Estimation (คำนวณจากจำนวนเที่ยวสูงสุดของสายเทในออเดอร์ คูณเวลาเทจริงต่อเที่ยว บวกเวลารอเปิดหน้างาน)
    ทำไมใช้วิธีนี้: เพื่อใช้ตรวจสอบว่าถ้าเริ่มเทตามเวลานัดเดิม ขบวนรถโม่จะไปชนช่วงเวลาห้ามเข้าพื้นที่ (no_entry) หรือชนช่วงพีคของวันหรือไม่
    """
    sids = day.orders[oid]
    s0 = day.streams[sids[0]]
    cust = day.customers[s0["customer_id"]]
    req_start = s0["first_arrival"]
    max_stream_loads = max(day.streams[sid]["loads"] for sid in sids)
    total_loads = sum(day.streams[sid]["loads"] for sid in sids)
    pour_min = s0["interval_min"] * cust["pour_factor"]
    est_duration = cust["site_delay_min"] + max_stream_loads * (pour_min + 2.0)
    return req_start, req_start + est_duration, total_loads


def find_zone_congestion_end_by_events(day, zone, ref_time):
    """
    ทำอะไร: ค้นหาเวลาสิ้นสุดของเหตุการณ์รถติดหนัก (travel_factor > 1.1) ในโซนของหน้างานที่ทับซ้อนกับเวลาอ้างอิง
    ใช้วิธีอะไร: Event Window Lookup จาก day.events
    ทำไมใช้วิธีนี้: ในวันเปิดเทอมหรือวันที่มีเหตุการณ์พิเศษ แม้พ้นเวลาห้ามเข้าเขตโรงเรียนแล้ว แต่ถนนหลักในโซนยังติดหนัก การเลื่อนออเดอร์ที่มีสิทธิ์ later_ok ข้ามช่วงรถติดช่วยให้รถไม่เสียเวลาวิ่งบนถนนนาน
    """
    max_end = ref_time
    for e in day.events:
        if not e["no_entry"] and e["factor"] > 1.1 and zone in e["zones"]:
            if e["start"] <= ref_time < e["end"]:
                max_end = max(max_end, e["end"])
    return max_end


def build_mandatory_ban_shifts_by_conflict_check(day, config=None):
    """
    ทำอะไร: สร้างรายการขอเลื่อนเวลาออเดอร์ (order_changes) สำหรับออเดอร์ที่เวลานัดเดิมชนกับช่วงเวลาห้ามเข้าพื้นที่ (no_entry) ของหน้างาน
    ใช้วิธีอะไร: Site Ban Conflict Detection & Flex Clamping (ตรวจการทับซ้อนระหว่างช่วงเวลาเทของออเดอร์กับ site['no_entry'] แล้วเลื่อนออกหลังเวลาห้ามสำหรับ later_ok หรือเลื่อนให้จบก่อนเวลาห้ามสำหรับ earlier_ok ภายใต้กรอบ flex_max_shift_min)
    ทำไมใช้วิธีนี้: หากไม่เลื่อนออเดอร์ที่ชนเวลาห้ามเข้าเขตโรงเรียนหรือเวลาปิดถนน รถจะถูกกักหน้าด่านจนผิด SLA On-Time และปูนหมดอายุ (Rejected)
    """
    cfg = config or SHIFT_CONFIG
    changes = {}

    for oid, sids in day.orders.items():
        s0 = day.streams[sids[0]]
        cust = day.customers[s0["customer_id"]]
        site = day.sites[s0["site_id"]]
        flex_type = cust["flex_type"]
        max_shift = cust["flex_max_shift_min"]

        if flex_type not in ("later_ok", "earlier_ok") or max_shift <= 0:
            continue
        if not site["no_entry"]:
            continue

        req_start, est_end, _ = estimate_order_pour_Span(day, oid)

        for ban_start, ban_end in sorted(site["no_entry"]):
            if flex_type == "later_ok":
                if (ban_start - cfg["ban_start_lookahead_min"]) <= req_start < ban_end:
                    target = _round_to_step(ban_end + cfg["post_ban_buffer_min"], cfg["round_step_min"])
                    shift = target - req_start
                    if 0 < shift <= max_shift:
                        changes[oid] = {
                            "order_id": oid,
                            "new_first_arrival": m2t(target),
                            "reason": f"เลื่อนหลบช่วงเวลาห้ามรถบรรทุกเข้าพื้นที่ ({m2t(ban_start)}-{m2t(ban_end)})",
                        }
                        break

            elif flex_type == "earlier_ok":
                if req_start < ban_end and (est_end + cfg["ban_end_Check_pad_min"]) > ban_start:
                    earliest_allowed = req_start - max_shift
                    target = _round_to_step(earliest_allowed, cfg["round_step_min"])
                    shift = target - req_start
                    if -max_shift <= shift < 0:
                        changes[oid] = {
                            "order_id": oid,
                            "new_first_arrival": m2t(target),
                            "reason": f"เลื่อนเข้าเช้าขึ้นเพื่อเทให้เสร็จก่อนเวลาปิดพื้นที่ ({m2t(ban_start)}-{m2t(ban_end)})",
                        }
                        break

    return changes


def generate_candidate_order_changes_by_conflict_check(day, config=None):
    """
    ทำอะไร: สร้างชุดทางเลือกของการเลื่อนเวลาออเดอร์ (Candidate order_changes lists) เพื่อส่งให้ตัวจำลองคัดเลือกชุดที่ให้คะแนน SLA และกำไรสูงสุด
    ใช้วิธีอะไร: Multi-Scenario Conflict, Event Jam Dodge & Peak De-confliction Generation
    ทำไมใช้วิธีนี้: ช่วยแก้ปัญหาได้รอบด้าน ทั้งการเลื่อนหลบช่วงห้ามเข้าพื้นที่ การเลื่อนข้ามช่วงรถติดหนักตามเหตุการณ์จราจร และการกระจายออเดอร์ช่วงพีค
    """
    cfg = config or SHIFT_CONFIG
    base_changes = build_mandatory_ban_shifts_by_conflict_check(day, cfg)
    scenarios = [list(base_changes.values())]

    m_lo, m_hi = cfg["morning_congestion_window"]
    a_lo, a_hi = cfg["afternoon_congestion_window"]

    # ทางเลือกที่ 1: เลื่อนออเดอร์ later_ok ข้ามช่วงรถติดหนักของโซน (Traffic Event Jam Dodge)
    traffic_scen = dict(base_changes)
    for oid in list(base_changes.keys()):
        s0 = day.streams[day.orders[oid][0]]
        site = day.sites[s0["site_id"]]
        cust = day.customers[s0["customer_id"]]
        if cust["flex_type"] == "later_ok":
            req_start = s0["first_arrival"]
            ev_end = find_zone_congestion_end_by_events(day, site["zone"], req_start)
            if ev_end > req_start:
                for add_m in [0, 15, 30]:
                    target = _round_to_step(ev_end + add_m, cfg["round_step_min"])
                    shift = target - req_start
                    if 0 < shift <= cust["flex_max_shift_min"]:
                        sc_copy = dict(traffic_scen)
                        sc_copy[oid] = {
                            "order_id": oid,
                            "new_first_arrival": m2t(target),
                            "reason": f"เลื่อนข้ามช่วงรถติดหนักในพื้นที่ ({m2t(ev_end)})",
                        }
                        sc_list = list(sc_copy.values())
                        if sc_list not in scenarios:
                            scenarios.append(sc_list)

    # ทางเลือกที่ 2: ดึงออเดอร์ earlier_ok ออกจากช่วงพีคเช้าหรือบ่าย
    earlier_relief = dict(base_changes)
    for oid, sids in day.orders.items():
        if oid in earlier_relief:
            continue
        s0 = day.streams[sids[0]]
        cust = day.customers[s0["customer_id"]]
        if cust["flex_type"] != "earlier_ok" or cust["flex_max_shift_min"] <= 0:
            continue
        req_start, _, total_loads = estimate_order_pour_Span(day, oid)
        in_morning_peak = (m_lo <= req_start <= m_hi) and (total_loads >= cfg["earlier_relief_min_loads"])
        in_afternoon_event = (a_lo <= req_start <= a_hi) and bool(day.events) and (total_loads >= cfg["earlier_relief_min_loads"])
        if in_morning_peak or in_afternoon_event:
            target = _round_to_step(req_start - cust["flex_max_shift_min"], cfg["round_step_min"])
            if target < req_start:
                earlier_relief[oid] = {
                    "order_id": oid,
                    "new_first_arrival": m2t(target),
                    "reason": "เลื่อนเข้าเช้าขึ้นเพื่อลดความแออัดของโรงงานและรถโม่ช่วงพีค",
                }
    if len(earlier_relief) > len(base_changes) and list(earlier_relief.values()) not in scenarios:
        scenarios.append(list(earlier_relief.values()))

    # ทางเลือกที่ 3: กระจายออเดอร์ช่วงบ่ายก่อนปิดถนนเทศกาล (Afternoon Pre-Closure Relief)
    if bool(day.events):
        base_ref = scenarios[-1] if len(scenarios) > 1 else list(base_changes.values())
        afternoon_relief = {r["order_id"]: dict(r) for r in base_ref}
        for oid, sids in day.orders.items():
            if oid in afternoon_relief:
                continue
            s0 = day.streams[sids[0]]
            cust = day.customers[s0["customer_id"]]
            req_start, _, total_loads = estimate_order_pour_Span(day, oid)
            if cust["flex_type"] == "later_ok" and (a_lo <= req_start <= a_hi) and total_loads <= 6:
                target = _round_to_step(req_start + cust["flex_max_shift_min"], cfg["round_step_min"])
                if target > req_start:
                    afternoon_relief[oid] = {
                        "order_id": oid,
                        "new_first_arrival": m2t(target),
                        "reason": "เลื่อนงานช่วงบ่ายออกไปตามเงื่อนไขลูกค้าเพื่อลดความแออัดก่อนเวลาปิดถนน",
                    }
        sc_list = list(afternoon_relief.values())
        if len(afternoon_relief) > len(base_changes) and sc_list not in scenarios:
            scenarios.append(sc_list)

    # ทางเลือกที่ 4: เลื่อนออเดอร์เมกะโปรเจกต์ช่วงเช้า
    mega_oids = []
    for oid, sids in day.orders.items():
        if oid in base_changes:
            continue
        s0 = day.streams[sids[0]]
        cust = day.customers[s0["customer_id"]]
        req_start, _, total_loads = estimate_order_pour_Span(day, oid)
        if cust["flex_type"] == "later_ok" and cust["flex_max_shift_min"] >= 90:
            if (len(sids) >= cfg["mega_order_min_streams"] or total_loads >= cfg["mega_order_min_loads"]) and (m_lo <= req_start <= m_hi):
                mega_oids.append(oid)

    for shift_min in cfg["mega_shift_candidates_min"]:
        if not mega_oids:
            break
        base_ref = scenarios[1] if len(scenarios) > 1 else list(base_changes.values())
        scen = {r["order_id"]: dict(r) for r in base_ref}
        added = False
        for oid in mega_oids:
            s0 = day.streams[day.orders[oid][0]]
            cust = day.customers[s0["customer_id"]]
            if shift_min <= cust["flex_max_shift_min"]:
                target = _round_to_step(s0["first_arrival"] + shift_min, cfg["round_step_min"])
                scen[oid] = {
                    "order_id": oid,
                    "new_first_arrival": m2t(target),
                    "reason": f"เลื่อนงานโครงการขนาดใหญ่หลบช่วงพีคเช้า (+{shift_min} นาที)",
                }
                added = True
        sc_list = list(scen.values())
        if added and sc_list not in scenarios:
            scenarios.append(sc_list)

    return scenarios
