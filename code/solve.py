"""AI Concrete — the ONE command every team must keep working (the organisers run it on the hidden Day 5).

    python solve.py <day_dir> <out_dir>
    python solve.py ../data/day1_weekday ../out/day1

Reads one day folder (and ../common), writes <out_dir>/plan.csv, order_changes.csv, production_plan.csv.
Rules for the hidden day: same command, no internet, no manual edits, finish within 5 minutes.
Replace plan_day() with your own solver (any language is fine as long as this command produces the 3 files).
"""
import os
import sys
import time

from aiconcrete import kpis, load_day, simulate, t2m, write_csv
from baseline_greedy import plan_day as baseline_plan_day
from step1_correct_capacity_by_history import correct_capacity_by_history
from step2_schedule_by_actual_pour_greedy import (
    SCHEDULE_CONFIG, SCHEDULE_PROFILES, schedule_by_actual_pour_greedy
)
from step3_shift_orders_by_conflict_check import generate_candidate_order_changes_by_conflict_check
from step4_boost_lines_by_bottleneck import generate_boost_candidates_by_bottleneck

# ===== ค่าปรับแต่งส่วนกลางสำหรับตัวควบคุมหลัก (ห้ามผูกค่าเฉพาะวัน เพื่อรองรับ Hidden Day 5) =====
SOLVER_CONFIG = {
    "max_runtime_sec": 240.0,
}


def evaluate_candidate_plan_by_simulator(day, plan_rows, production, changes):
    """
    ทำอะไร: จำลองผลลัพธ์ของแผนเดินรถด้วยฟังก์ชัน simulate() และ kpis() ของผู้จัดเพื่อวัดผลคะแนนจริง
    ใช้วิธีอะไร: In-Memory Official KPI Evaluation (แปลงเวลา load_start เป็นนาทีแล้วรัน simulate และ kpis เพื่อคืนค่าลำดับคะแนนตามเกณฑ์ตัดสิน)
    ทำไมใช้วิธีนี้: เพื่อให้ตัวเลือกแผนที่โปรแกรมคัดเลือกสอดคล้องกับเกณฑ์การให้คะแนนจริงของกรรมการ 100% (ไม่มี Hard Error -> ผ่าน SLA รายวัน -> จำนวนข้อ SLA ที่ผ่าน -> กำไรสุทธิ -> ระยะทางรวมต่ำสุด)
    """
    plan_for_sim = [
        {
            "stream_id": r["stream_id"],
            "seq": int(r["seq"]),
            "plant_id": r["plant_id"],
            "truck_id": r["truck_id"],
            "load_start": t2m(r["load_start"]),
        }
        for r in plan_rows
    ]
    sim, errors = simulate(day, plan_for_sim, production=production, changes=changes)
    res = kpis(day, sim, errors)
    rank_key = (
        1 if res["errors"] == 0 else 0,
        1 if res["sla_pass"] else 0,
        res["sla_checks_passed"],
        res["contribution_baht"],
        -res["km_total"],
    )
    return rank_key, res


def plan_day_with_fallback(day_dir):
    """
    ทำอะไร: วางแผนจัดตารางผลิตและเดินรถคอนกรีตประจำวันแบบ Closed-Loop Multi-Profile พร้อมระบบสำรอง (Fallback) หากเกิดข้อผิดพลาด
    ใช้วิธีอะไร: Closed-Loop Multi-Scenario & Multi-Profile Optimizer with Safe Fallback
    ทำไมใช้วิธีนี้: ช่วยให้ได้แผนที่ผ่าน SLA 100% ครบทุกวัน พร้อมกำไรสุทธิสูงสุดโดยไม่ต้องเปิดไลน์ผลิตเสริมเกินจำเป็น และรับประกันว่าจะมีไฟล์ผลลัพธ์ที่ถูกต้องเสมอแม้เกิดเหตุไม่คาดฝันในวันตัดสิน (Hidden Day 5)
    """
    t0 = time.time()
    try:
        day = load_day(day_dir)
        correct_capacity_by_history(day)

        best_rank = None
        best_tuple = None
        best_res = None

        # 1. ทดสอบชุดทางเลือกการเลื่อนเวลาออเดอร์ร่วมกับชุดโปรไฟล์พารามิเตอร์การจัดตารางเดินรถ
        change_scenarios = generate_candidate_order_changes_by_conflict_check(day)
        if [] not in change_scenarios:
            change_scenarios.append([])

        for changes_cand in change_scenarios:
            if time.time() - t0 > SOLVER_CONFIG["max_runtime_sec"]:
                break
            for prof in SCHEDULE_PROFILES:
                if time.time() - t0 > SOLVER_CONFIG["max_runtime_sec"]:
                    break
                try:
                    c = dict(SCHEDULE_CONFIG, **prof)
                    plan_cand = schedule_by_actual_pour_greedy(day, production=[], changes=changes_cand, config=c)
                    rank_key, res = evaluate_candidate_plan_by_simulator(day, plan_cand, [], changes_cand)
                    if best_rank is None or rank_key > best_rank:
                        best_rank = rank_key
                        best_tuple = (plan_cand, changes_cand, [])
                        best_res = res
                except Exception as inner_exc:
                    print(f"[WARN] Candidate scenario failed: {inner_exc}", file=sys.stderr)

        # 2. หากแผนที่ดีที่สุดยังไม่ผ่าน SLA (sla_pass == False) จึงทดสอบเปิดไลน์ผลิตเสริม (ขั้นตอนที่ 4)
        if best_res is not None and not best_res["sla_pass"]:
            best_changes = best_tuple[1]
            boost_candidates = generate_boost_candidates_by_bottleneck(day, eval_res=best_res)
            for prod_cand in boost_candidates:
                if time.time() - t0 > SOLVER_CONFIG["max_runtime_sec"]:
                    break
                for prof in SCHEDULE_PROFILES:
                    if time.time() - t0 > SOLVER_CONFIG["max_runtime_sec"]:
                        break
                    try:
                        c = dict(SCHEDULE_CONFIG, **prof)
                        plan_cand = schedule_by_actual_pour_greedy(day, production=prod_cand, changes=best_changes, config=c)
                        rank_key, res = evaluate_candidate_plan_by_simulator(day, plan_cand, prod_cand, best_changes)
                        if (res["sla_pass"] and not best_res["sla_pass"]) or (res["contribution_baht"] > best_res["contribution_baht"]):
                            if rank_key > best_rank:
                                best_rank = rank_key
                                best_tuple = (plan_cand, best_changes, prod_cand)
                                best_res = res
                    except Exception as inner_exc:
                        print(f"[WARN] Boost scenario failed: {inner_exc}", file=sys.stderr)

        if best_tuple is not None:
            return day, best_tuple[0], best_tuple[1], best_tuple[2]

        # กรณีไม่มีทางเลือกใดสำเร็จ ให้รันแบบพื้นฐาน
        plan = schedule_by_actual_pour_greedy(day, production=[], changes=[])
        return day, plan, [], []
    except Exception as exc:
        print(f"[WARN] Fallback to baseline_greedy due to error: {exc}", file=sys.stderr)
        day = load_day(day_dir)
        plan, changes, production = baseline_plan_day(day)
        return day, plan, changes, production


def main():
    """
    ทำอะไร: จุดเริ่มต้นหลักของโปรแกรม รับพารามิเตอร์โฟลเดอร์ข้อมูลรายวันและโฟลเดอร์ผลลัพธ์ แล้วบันทึกไฟล์ CSV ทั้ง 3 ไฟล์
    ใช้วิธีอะไร: Command-Line Entrypoint ตามมาตรฐานของ Starter Kit
    ทำไมใช้วิธีนี้: กติกากำหนดให้กรรมการตรวจให้คะแนนด้วยคำสั่ง python solve.py <day_dir> <out_dir>
    """
    day_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    day, plan, changes, production = plan_day_with_fallback(day_dir)
    write_csv(os.path.join(out_dir, "plan.csv"), plan, ["stream_id", "seq", "plant_id", "truck_id", "load_start"])
    write_csv(os.path.join(out_dir, "order_changes.csv"), changes, ["order_id", "new_first_arrival", "reason"])
    write_csv(os.path.join(out_dir, "production_plan.csv"), production, ["plant_id", "extra_lines", "from", "to"])
    print(f"Day {day.info.get('day')}: {len(plan)} loads -> {out_dir}")


if __name__ == "__main__":
    main()
