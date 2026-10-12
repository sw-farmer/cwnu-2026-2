#!/usr/bin/env python3
"""sources/schedule.csv -> outputs/data.js 변환 (표준 라이브러리만 사용).

집계 규칙 (dashboard.html의 aggregate()와 동일):
  - 원가: 시작월~종료월에 균등 배분
  - 투입인원: 해당 월에 활동 중인 행의 인원 합
  - 공정률: 원가 가중 누적 (누적원가 / 총원가)
배열 인덱스 = 월 (0번은 착공 전).
"""
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "sources" / "schedule.csv"
OUT = ROOT / "outputs" / "data.js"
COLS = ["공종", "층", "시작월", "종료월", "인원", "원가_백만원"]


def read_text(path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):  # 엑셀 'CSV(쉼표로 분리)'는 CP949일 수 있음
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    sys.exit(f"인코딩을 읽을 수 없습니다: {path}")


def load_rows(path):
    reader = csv.DictReader(read_text(path).splitlines())
    reader.fieldnames = [(h or "").strip() for h in reader.fieldnames or []]
    missing = [c for c in COLS if c not in reader.fieldnames]
    if missing:
        sys.exit(f"CSV 열이 없습니다: {', '.join(missing)}")
    rows = []
    for no, r in enumerate(reader, start=2):
        if not any((v or "").strip() for v in r.values()):
            continue
        try:
            row = {
                "공종": r["공종"].strip(),
                "층": r["층"].strip().replace("-", "~"),
                "시작월": int(float(r["시작월"])),
                "종료월": int(float(r["종료월"])),
                "인원": float(r["인원"]),
                "원가_백만원": float(r["원가_백만원"].replace(",", "")),
            }
        except (ValueError, AttributeError):
            sys.exit(f"{no}행: 숫자 형식 오류 -> {dict(r)}")
        if not row["공종"] or not re.fullmatch(r"\d+(~\d+)?", row["층"]):
            sys.exit(f"{no}행: 공종/층 형식 오류 (층은 '3' 또는 '1~5')")
        if row["시작월"] < 1 or row["종료월"] < row["시작월"]:
            sys.exit(f"{no}행: 월 범위 오류 ({row['시작월']}~{row['종료월']})")
        rows.append(row)
    if not rows:
        sys.exit("데이터 행이 없습니다.")
    return rows


def aggregate(rows):
    months = max(r["종료월"] for r in rows)
    total = sum(r["원가_백만원"] for r in rows)
    month_cost = [0.0] * (months + 1)
    headcount = [0.0] * (months + 1)
    for r in rows:
        span = r["종료월"] - r["시작월"] + 1
        for m in range(r["시작월"], r["종료월"] + 1):
            month_cost[m] += r["원가_백만원"] / span
            headcount[m] += r["인원"]
    cum, acc = [], 0.0
    for m in range(months + 1):
        acc += month_cost[m]
        cum.append(acc)
    return {
        "months": months,
        "totalCost": round(total, 2),
        "monthCost": [round(v, 2) for v in month_cost],
        "cumCost": [round(v, 2) for v in cum],
        "progress": [round(v / total * 100, 2) for v in cum],
        "headcount": [round(v, 2) for v in headcount],
    }


def main():
    rows = load_rows(SRC)
    data = {
        "source": SRC.name,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rows": rows,
        "monthly": aggregate(rows),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "// build_data.py 자동 생성 — 직접 수정하지 말고 schedule.csv를 고친 뒤 다시 실행하세요.\n"
        "window.SCHEDULE_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n",
        encoding="utf-8",
    )
    m = data["monthly"]
    print(f"{len(rows)}행 -> {OUT}")
    print(f"공기 {m['months']}개월 / 총원가 {m['totalCost']:,.0f} 백만원 / 최대인원 {max(m['headcount']):.0f}명")


if __name__ == "__main__":
    main()
