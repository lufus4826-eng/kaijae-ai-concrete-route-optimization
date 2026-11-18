"""ขั้นตอนที่ 2: จัดตารางเดินรถและเลือกโรงงานด้วยวิธีติดตามเวลาเทจริงและสงวนรถโม่ข้ามโซน (Actual-Pour Tracking & Fleet-Reserved Greedy)"""
import math
from aiconcrete import (
    ON_TIME_TOL, Sim, leg, m2t, t2m, transport_cost
)

# ===== ค่าปรับแต่งส่วนกลางสำหรับขั้นตอนที่ 2 (ห้ามผูกค่าเฉพาะวัน เพื่อรองรับ Hidden Day 5) =====
SCHEDULE_CONFIG = {
    "first_load_target_offset_min": 2.0,
    "early_pour_buffer_max_min": 12.0,
    "early_pour_buffer_min_min": 2.0,
    "grade1_gap_target_offset_min": 0.0,
    "grade2_gap_ratio": 0.15,
    "mid_grade_gap_ratio": 0.20,
    "low_grade_gap_ratio": 0.40,
    "window_pad_min": 20,
    "window_step_min": 1,
    "truck_ready_horizon_min": 60,
    "reposition_weight": 15.0,
    "empty_km_weight": 0.5,
    "abundant_restricted_threshold": 7,
    "all_truck_reservation_penalty": 500.0,
    "extra_loaded_km_penalty": 0.0,
    "empty_drive_baht_per_km": 0.0,
    "safe_age_min": 120.0,
    "age_risk_weight": 0.0,
    "wait_site_penalty_factor": 2.0,
    "timing_error_weight": 8.0,
    "wait_bucket_divisor_min": 15.0,
    "rejected_penalty": 1000.0,
    "priority_lookahead_min": 0,
}

SCHEDULE_PROFILES = [
    {
        "name": "Standard Balanced",
        "all_truck_reservation_penalty": 500.0,
        "window_step_min": 1,
        "window_pad_min": 20,
        "first_load_target_offset_min": 2.0,
        "grade1_gap_target_offset_min": 0.0,
        "grade2_gap_ratio": 0.15,
        "mid_grade_gap_ratio": 0.20,
        "low_grade_gap_ratio": 0.40,
        "wait_site_penalty_factor": 2.0,
    },
    {
        "name": "Proactive VIP",
        "all_truck_reservation_penalty": 500.0,
        "window_step_min": 1,
        "window_pad_min": 20,
        "first_load_target_offset_min": 2.0,
        "grade1_gap_target_offset_min": -0.5,
        "grade2_gap_ratio": -0.30,
        "mid_grade_gap_ratio": 0.15,
        "low_grade_gap_ratio": 0.35,
        "wait_site_penalty_factor": 0.5,
    },
    {
        "name": "Moderate Reserve",
        "all_truck_reservation_penalty": 200.0,
        "window_step_min": 1,
        "window_pad_min": 20,
        "first_load_target_offset_min": 2.0,
        "grade1_gap_target_offset_min": 0.0,
        "grade2_gap_ratio": 0.15,
        "mid_grade_gap_ratio": 0.20,
        "low_grade_gap_ratio": 0.40,
        "wait_site_penalty_factor": 2.0,
    },
]



def compute_restricted_supply_by_plant(day):
    """
    ทำอะไร: นับจำนวนรถโม่ที่มีข้อจำกัดโซนโรงงาน (allowed != ALL) ที่สามารถเข้าโหลดในแต่ละโรงงานและแต่ละขนาดรถ
    ใช้วิธีอะไร: Dynamic Fleet Partition Counting (นับจาก day.trucks ของวันนั้นโดยตรง ไม่ผูกชื่อโรงงานตายตัว)
    ทำไมใช้วิธีนี้: โซนเหนือ/ตะวันตกมีรถจำกัดโซน (V2) จำนวนมากถึง 25-29 คัน ขณะที่โซนใต้/ตะวันออกมีรถจำกัดโซน (V3) เพียง 7 คัน การนับสัดส่วนนี้ช่วยให้โปรแกรมสงวนรถโม่ประเภท ALL (V1) ไว้ช่วยโซนที่มีรถจำกัดโซนน้อยได้โดยอัตโนมัติ
    """
    supply = {}
    for pid in day.plants:
        for sz in ("BIG", "SMALL"):
            supply[(pid, sz)] = sum(
                1 for t in day.trucks.values()
                if t["size"] == sz and t["allowed"] is not None and pid in t["allowed"]
            )
    return supply


def compute_arrival_window_by_actual_pour(day, sim, sid, seq, cfg):
    """
    ทำอะไร: คำนวณช่วงเวลามาถึงหน้างานที่ยอมรับได้ (arr_lo, arr_hi) และเวลาเป้าหมาย (arr_target) ของเที่ยวที่ seq
    ใช้วิธีอะไร: Actual-Pour Window Calculation (เที่ยวแรกอ้างอิงเวลานัด sim.first_arrival ส่วนเที่ยวถัดไปอ้างอิงเวลาเทเสร็จจริงของเที่ยวก่อนหน้า sim.site_free[sid] โดยตั้งเป้าให้เกรด 1 มาถึงก่อนคันก่อนหน้าเทเสร็จเล็กน้อยเพื่อกันพลาด)
    ทำไมใช้วิธีนี้: การอ้างอิงเวลาเทเสร็จจริงของคันก่อนหน้าช่วยให้คุมช่องว่างการเท (gap <= max_gap_min) ได้แม่นยำ และลดความเสี่ยงที่งานเกรด 1 (max_gap = 5 นาที) จะขาดช่วง
    """
    s = day.streams[sid]
    cust = day.customers[s["customer_id"]]
    max_gap = s["max_gap_min"]
    pour_min = s["interval_min"] * cust["pour_factor"]

    if seq == 1:
        req = sim.first_arrival[sid]
        site_open = req + cust["site_delay_min"]
        arr_lo = req - ON_TIME_TOL
        arr_hi = req + ON_TIME_TOL
        arr_target = min(site_open, req + cfg["first_load_target_offset_min"])
    else:
        prev_end = sim.site_free[sid]
        early_buf = min(
            cfg["early_pour_buffer_max_min"],
            max(cfg["early_pour_buffer_min_min"], pour_min - cfg["early_pour_buffer_min_min"])
        )
        arr_lo = prev_end - early_buf
        arr_hi = prev_end + max_gap
        if s["grade"] == 1:
            arr_target = max(arr_lo, prev_end + cfg["grade1_gap_target_offset_min"])
        elif s["grade"] == 2:
            arr_target = prev_end + max_gap * cfg["grade2_gap_ratio"]
        elif s["grade"] <= 4:
            arr_target = prev_end + max_gap * cfg["mid_grade_gap_ratio"]
        else:
            arr_target = prev_end + max_gap * cfg["low_grade_gap_ratio"]

    return arr_lo, arr_hi, arr_target


def find_best_load_option_by_preview(day, sim, sid, seq, min_load_start, restricted_supply=None, cfg=None):
    """
    ทำอะไร: ค้นหาคู่ (โรงงาน, รถโม่, เวลาเริ่มโหลด) ที่ดีที่สุดสำหรับสายเท sid ลำดับที่ seq
    ใช้วิธีอะไร: Simulator-Preview Cost, Distance & SLA Search (จำลองผลลัพธ์จริงด้วย sim.preview ทุกนาทีในหน้าต่างเป้าหมาย พร้อมถ่วงน้ำหนักระยะทางส่วนเกิน อายุคอนกรีต คิวหน้างาน และการสงวนรถ ALL)
    ทำไมใช้วิธีนี้: ป้องกันไม่ให้โปรแกรมเลือกโรงงานที่ไกลมากเพียงเพราะค่าทราย/หินถูกกว่าเล็กน้อย ซึ่งจะทำให้เสียเวลาเดินทางนาน เสี่ยงปูนหมดอายุ และดึงรถโม่หายไปจากระบบ
    """
    cfg = cfg or SCHEDULE_CONFIG
    s = day.streams[sid]
    site = day.sites[s["site_id"]]
    max_gap = s["max_gap_min"]

    arr_lo, arr_hi, arr_target = compute_arrival_window_by_actual_pour(day, sim, sid, seq, cfg)

    ok_plants = [pid for pid in day.plants if (pid, s["product_id"]) in day.plant_product]
    ok_trucks = [tid for tid, t in day.trucks.items() if t["size"] == s["truck_size"]]
    min_plant_km = min((day.travel[(pid, s["site_id"])]["km"] for pid in ok_plants), default=0.0)

    best_choice = None
    best_key = None

    for pid in ok_plants:
        p = day.plants[pid]
        allowed_trucks = [
            tid for tid in ok_trucks
            if day.trucks[tid]["allowed"] is None or pid in day.trucks[tid]["allowed"]
        ]
        if not allowed_trucks:
            continue

        reserve_all = (
            restricted_supply is not None
            and restricted_supply.get((pid, s["truck_size"]), 0) >= cfg["abundant_restricted_threshold"]
        )

        truck_ready_map = {}
        for tid in allowed_trucks:
            ts = sim.trucks[tid]
            if ts.loc == pid:
                r_time = ts.ready
                km_e = 0.0
                km_rep = 0.0
            else:
                km_e, mins_e = leg(day, ts.loc, pid, ts.ready)
                r_time = ts.ready + mins_e
                km_rep = km_e if ts.loc in day.plants else 0.0
            all_pen = cfg["all_truck_reservation_penalty"] if (reserve_all and day.trucks[tid]["allowed"] is None) else 0.0
            truck_ready_map[tid] = (int(math.ceil(r_time)), km_e, km_rep, all_pen)

        min_truck_ready = min(v[0] for v in truck_ready_map.values())
        plant_earliest = max(min_load_start, p["open_m"] + p["prep_min"], min_truck_ready)
        plant_latest = p["close_m"] - p["mix_load_min"]
        if plant_earliest > plant_latest:
            continue

        approx_depart = max(plant_earliest, int(arr_target - day.travel[(pid, s["site_id"])]["normal"]))
        _, est_mins = leg(day, pid, s["site_id"], approx_depart)
        est_travel = est_mins + site["access_min"] + p["mix_load_min"] + p["qc_min"]

        pad = cfg["window_pad_min"]
        win_lo = max(plant_earliest, int(math.floor(arr_lo - est_travel)) - pad)
        win_hi = min(plant_latest, int(math.ceil(arr_hi - est_travel)) + pad)

        cand_set = set()
        if win_lo <= win_hi:
            for t_c in range(win_lo, win_hi + 1, cfg["window_step_min"]):
                cand_set.add(t_c)
            cand_set.add(win_lo)
            cand_set.add(win_hi)
        cand_set.add(plant_earliest)
        for tid, (rt, _, _, _) in truck_ready_map.items():
            if plant_earliest <= rt <= min(plant_latest, max(win_hi, plant_earliest) + cfg["truck_ready_horizon_min"]):
                cand_set.add(rt)

        evaluated_starts = set()
        for t_cand in sorted(cand_set):
            line_res = sim.line_start(pid, t_cand)
            if line_res is None:
                continue
            actual_start = int(math.ceil(line_res[0]))
            if actual_start < plant_earliest or actual_start > plant_latest:
                continue
            if actual_start in evaluated_starts:
                continue
            evaluated_starts.add(actual_start)

            ready_tids = [tid for tid, (rt, _, _, _) in truck_ready_map.items() if rt <= actual_start]
            if not ready_tids:
                continue

            best_tid = min(
                ready_tids,
                key=lambda tid: (
                    truck_ready_map[tid][2] * cfg["reposition_weight"]
                    + truck_ready_map[tid][1] * cfg["empty_km_weight"]
                    + truck_ready_map[tid][3],
                    actual_start - truck_ready_map[tid][0],
                    tid
                )
            )

            r = sim.preview(sid, seq, pid, best_tid, actual_start)
            if r["problems"]:
                continue
            if r["load_start"] != int(r["load_start"]):
                r = sim.preview(sid, seq, pid, best_tid, int(math.ceil(r["load_start"])))
                if r["problems"]:
                    continue

            rejected = r["rejected"]
            if seq == 1:
                dev = abs(r["arrive"] - sim.first_arrival[sid])
                sla_ok = (dev <= ON_TIME_TOL) and (not rejected)
                sla_violation = 0.0 if sla_ok else (max(0.0, dev - ON_TIME_TOL) + (cfg["rejected_penalty"] if rejected else 0.0))
                timing_err = abs(r["arrive"] - arr_target) + (r["wait_ban"] * 2.0)
            else:
                gap = r["gap"] or 0.0
                sla_ok = (gap <= max_gap) and (not rejected)
                sla_violation = 0.0 if sla_ok else (max(0.0, gap - max_gap) + (cfg["rejected_penalty"] if rejected else 0.0))
                timing_err = abs(r["arrive"] - arr_target) + (r["wait_site"] * cfg["wait_site_penalty_factor"])

            waiting_at_site = sum(
                1 for o in sim.done.values()
                if day.streams[o["stream_id"]]["site_id"] == s["site_id"] and o["arrive"] <= r["arrive"] < o["pour_start"]
            )
            queue_fine = day.cost["site_queue_fine_baht"] if waiting_at_site >= site["max_queue"] else 0.0

            mat_cost = day.plant_product[(pid, s["product_id"])] * s["load_m3"]
            tr_cost = transport_cost(day, r["km_loaded"], s["load_m3"])
            rep_cost = r["km_reposition"] * day.cost["reposition_baht_per_km"]
            wait_cost = r["wait_site"] * day.cost["truck_wait_baht_per_min"]
            empty_drive_pen = max(0.0, r["km_empty_to_plant"] - r["km_reposition"]) * cfg["empty_drive_baht_per_km"]
            extra_km_pen = max(0.0, r["km_loaded"] - min_plant_km) * cfg["extra_loaded_km_penalty"]
            age_pen = max(0.0, r["age_min"] - cfg["safe_age_min"]) * cfg["age_risk_weight"]
            all_pen = truck_ready_map[best_tid][3] * 0.5

            score_cost = (
                mat_cost + tr_cost + rep_cost + wait_cost + queue_fine
                + empty_drive_pen + extra_km_pen + age_pen + all_pen
                + timing_err * cfg["timing_error_weight"]
            )

            key = (
                0 if sla_ok else 1,
                round(sla_violation, 2),
                1 if queue_fine > 0 else 0,
                round(r["wait_site"] // cfg["wait_bucket_divisor_min"]),
                round(score_cost, 2),
                r["load_start"],
            )
            if best_key is None or key < best_key:
                best_key = key
                best_choice = r

    return best_choice, best_key


def schedule_by_actual_pour_greedy(day, production=None, changes=None, config=None):
    """
    ทำอะไร: จัดตารางเดินรถทั้งหมดของวัน (plan.csv) โดยเรียงลำดับตามเวลาเริ่มโหลดจริงควบคู่กับการจัดลำดับความสำคัญของเกรดลูกค้า
    ใช้วิธีอะไร: Chronological Actual-Pour Greedy with Priority Lookahead (จำลองการเดินรถทีละเที่ยวตามลำดับเวลา load_start จริง พร้อมให้สิทธิ์สายเทเกรดสูงที่ยังมีสถานะ GREEN จองรถและไลน์ผลิตก่อนหากเวลาเริ่มโหลดห่างกันไม่เกินช่วง Priority Lookahead)
    ทำไมใช้วิธีนี้: เพื่อให้ลำดับการจำลองตรงกับ Simulator ของกรรมการ 100% และป้องกันไม่ให้ออเดอร์เกรดต่ำแย่งรถโม่คันสุดท้ายตัดหน้าออเดอร์เกรด 1-3 เพียงเพราะเวลาเริ่มโหลดเร็วกว่ากัน 1-4 นาที
    """
    cfg = config or SCHEDULE_CONFIG
    sim = Sim(day, production=production, changes=changes)
    restricted_supply = compute_restricted_supply_by_plant(day)

    next_seq = {sid: 1 for sid in day.streams}
    last_load_start = {sid: 0 for sid in day.streams}
    order_over = {oid: 0 for oid in day.orders}
    total_loads = sum(s["loads"] for s in day.streams.values())
    rows = []

    cache = {}
    watermark = 0

    for _ in range(total_loads):
        candidates = []
        for sid, s in day.streams.items():
            k = next_seq[sid]
            if k > s["loads"]:
                continue
            min_ls = max(last_load_start[sid], watermark)
            if sid not in cache:
                cache[sid] = find_best_load_option_by_preview(
                    day, sim, sid, k, min_ls, restricted_supply=restricted_supply, cfg=cfg
                )
            choice, _ = cache[sid]
            if choice is not None:
                ls = int(choice["load_start"])
                oid = s["order_id"]
                still_green = 0 if order_over[oid] == 0 else 1
                deadline = (sim.first_arrival[sid] + ON_TIME_TOL) if k == 1 else (sim.site_free[sid] + s["max_gap_min"])
                candidates.append((ls, still_green, s["grade"], s["loads"], deadline, sid, k, choice))

        candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5], x[6]))
        best_min_ls = candidates[0][0]
        lookahead = cfg.get("priority_lookahead_min", 0)
        if lookahead > 0:
            window_cands = [
                c for c in candidates
                if c[0] <= best_min_ls + lookahead and c[1] == 0 and c[2] <= 3
            ]
            if window_cands:
                # เรียงตามเกรดลูกค้า (1 -> 2 -> 3) และเวลาเส้นตายหน้างาน
                window_cands.sort(key=lambda x: (x[2], x[4], x[0], x[3], x[5], x[6]))
                chosen = window_cands[0]
                if chosen[2] < candidates[0][2]:
                    candidates[0] = chosen

        ls, _, _, _, _, sid, k, r = candidates[0]
        r["planned"] = ls
        sim.commit(r)
        watermark = max(watermark, ls)
        next_seq[sid] = k + 1
        last_load_start[sid] = ls

        s = day.streams[sid]
        if r["rejected"] or (k > 1 and (r["gap"] or 0.0) > s["max_gap_min"]):
            order_over[s["order_id"]] += 1

        rows.append(dict(
            stream_id=sid,
            seq=k,
            plant_id=r["plant_id"],
            truck_id=r["truck_id"],
            load_start=m2t(ls)
        ))

        p_used = r["plant_id"]
        t_used = r["truck_id"]
        to_del = [
            s_id for s_id, (ch, _) in cache.items()
            if s_id == sid or ch is None or ch["plant_id"] == p_used or ch["truck_id"] == t_used or int(ch["load_start"]) < watermark
        ]
        for s_id in to_del:
            del cache[s_id]

    rows.sort(key=lambda r: (t2m(r["load_start"]), r["stream_id"], int(r["seq"])))
    return rows
