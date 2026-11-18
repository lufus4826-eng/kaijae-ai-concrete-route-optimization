"""ขั้นตอนที่ 1: ปรับแก้จำนวนไลน์ผลิตจริงของโรงงานจากข้อมูลประวัติการโหลด (Historical Capacity Correction)"""
import csv
import os

# ===== ค่าปรับแต่งส่วนกลางสำหรับขั้นตอนที่ 1 (ใช้ได้กับทุกวันรวมถึง Hidden Day 5) =====
CAPACITY_CONFIG = {
    "history_filename": "plant_load_history.csv",
    "minutes_per_hour": 60.0,
    "min_valid_lines": 1,
}


def correct_capacity_by_history(day, config=None):
    """
    ทำอะไร: คำนวณและอัปเดตจำนวนไลน์ผลิตจริง (lines) ของแต่ละโรงงานในตัวแปร day.plants
    ใช้วิธีอะไร: Historical Max-Throughput Inference (หาค่าสูงสุดของจำนวนเที่ยวโหลดต่อชั่วโมงจาก plant_load_history.csv แล้วคูณด้วยเวลารอบผลิต prep_min + mix_load_min หารด้วย 60 นาที)
    ทำไมใช้วิธีนี้: ข้อมูลใน plants.csv บางโรงงานบันทึกจำนวนไลน์สูงกว่าความเป็นจริงเนื่องจากมีไลน์สำรองที่ไม่ได้เปิดใช้งานจริง การคำนวณย้อนกลับจากสถิติการโหลดสูงสุดที่ทำได้จริงช่วยให้รู้อัตราการผลิตจริงโดยไม่ต้องเดาค่าคงที่ และทำงานได้อัตโนมัติแม้ในวันตัดสิน (Day 5)
    """
    cfg = config or CAPACITY_CONFIG
    common_dir = os.path.join(os.path.dirname(os.path.abspath(day.folder)), "common")
    hist_path = os.path.join(common_dir, cfg["history_filename"])
    if not os.path.exists(hist_path):
        return day

    with open(hist_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    max_loads_per_hour = {}
    for r in rows:
        pid = r.get("plant_id", "").strip()
        if not pid:
            continue
        loads = int(r.get("loads") or 0)
        if loads > max_loads_per_hour.get(pid, 0):
            max_loads_per_hour[pid] = loads

    for pid, plant in day.plants.items():
        if pid in max_loads_per_hour:
            cycle_min = plant["prep_min"] + plant["mix_load_min"]
            inferred_lines = int(round(max_loads_per_hour[pid] * cycle_min / cfg["minutes_per_hour"]))
            if inferred_lines >= cfg["min_valid_lines"]:
                plant["lines"] = inferred_lines

    return day
