# -*- coding: utf-8 -*-
"""
check_schedule.py - 공정 데이터 검증기 (스마트건설관리특론 3주차 데이터팩)

사용 (이 파일이 있는 폴더의 데이터팩을 읽음, 표준 라이브러리만 사용):
  python check_schedule.py                         기준 공정표 계산 + 요약(준공일·마일스톤·주공정)
  python check_schedule.py --compare 내결과.csv     AI가 만든 일정(작업코드·시작일·종료일[·여유])을 대조
  python check_schedule.py --progress progress_2026-09-30.csv
                                                   진도(실적) 반영 → 준공 예측·지연 일수
  python check_schedule.py --progress progress_2026-09-30.csv --crash 단축안.csv
                                                   단축안(작업코드,단축일) 평가 → 새 준공일·추가비용·간접비 절감
  --out 결과.csv  계산 결과를 csv로 저장 (UTF-8 BOM)
  --data 폴더     데이터팩 폴더 지정 (기본: 이 파일이 있는 폴더)

계산 규약 (00_README.md 3절과 동일)
  기간=작업일(시작·종료 포함) / 캘린더 STD·WX / 관계 FS: 후행시작 ≥ 선행종료+1+시차,
  SS: 후행시작 ≥ 선행시작+시차 / 시차=달력일 / 휴무일이면 다음 작업가능일로 / 여유=작업일
  주공정 = 준공일을 결정하는 연속 경로(각 작업의 시작을 실제로 결정한 선행을 준공에서 거슬러 추적)
종료코드: 0=통과, 1=실패 항목 있음
"""
import argparse, bisect, csv, io, json, os, sys
import datetime as dt

for _st in (sys.stdout, sys.stderr):      # Windows 한글 콘솔(cp949)에서 못 찍는 문자는 ?로 대체
    try: _st.reconfigure(errors="replace")
    except Exception: pass
D = dt.date.fromisoformat
ONE = dt.timedelta(days=1)


# ---------------------------------------------------------------- 입출력
def read_csv(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        sys.exit(f"[실패] {path}: 인코딩을 읽을 수 없음 (UTF-8 또는 CP949로 저장)")
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]


def write_csv(path, rows, cols):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- 캘린더
class Calendars:
    def __init__(self, rows):
        self.first, self.last = D(rows[0]["날짜"]), D(rows[-1]["날짜"])
        names = [c for c in rows[0] if c not in ("날짜", "요일", "비고")]
        self.W, self.pos = {}, {}
        for n in names:
            days = [D(r["날짜"]) for r in rows if r[n] == "1"]
            self.W[n] = days
            self.pos[n] = {d: i for i, d in enumerate(days)}

    def _chk(self, d):
        if d < self.first or d > self.last:
            sys.exit(f"[실패] 날짜 {d}가 calendar.csv 범위({self.first}~{self.last})를 벗어남")

    def next_work(self, cal, d):          # d 이후(포함) 첫 작업가능일
        self._chk(d)
        i = bisect.bisect_left(self.W[cal], d)
        if i >= len(self.W[cal]):
            sys.exit(f"[실패] {d} 이후 {cal} 작업가능일이 calendar.csv에 없음")
        return self.W[cal][i]

    def prev_work(self, cal, d):          # d 이전(포함) 마지막 작업가능일
        self._chk(d)
        i = bisect.bisect_right(self.W[cal], d) - 1
        if i < 0:
            sys.exit(f"[실패] {d} 이전 {cal} 작업가능일이 calendar.csv에 없음")
        return self.W[cal][i]

    def shift(self, cal, d, n):           # 작업가능일 d에서 n 작업일 이동
        return self.W[cal][self.pos[cal][d] + n]

    def idx(self, cal, d):
        return self.pos[cal][d]


# ---------------------------------------------------------------- 데이터 적재·검사
def load(data):
    p = lambda f: os.path.join(data, f)
    for f in ("activities.csv", "relations.csv", "calendar.csv", "project.json"):
        if not os.path.exists(p(f)):
            sys.exit(f"[실패] 데이터 파일 없음: {p(f)}")
    proj = json.load(open(p("project.json"), encoding="utf-8"))
    acts = read_csv(p("activities.csv"))
    rels = read_csv(p("relations.csv"))
    cal = Calendars(read_csv(p("calendar.csv")))
    return proj, acts, rels, cal


def build(acts, rels, cal, fails):
    A = {}
    for i, r in enumerate(acts, 2):
        c = r["작업코드"]
        if not c:
            fails.append(f"activities.csv {i}행: 작업코드 없음"); continue
        if c in A:
            fails.append(f"activities.csv {i}행: 작업코드 중복 {c}")
        A[c] = dict(r)
    kids = {c: [] for c in A}
    for c, a in A.items():
        par = a.get("상위코드", "")
        if par:
            if par not in A: fails.append(f"{c}: 상위코드 '{par}' 없음")
            else: kids[par].append(c)
    leaves = [c for c in A if not kids[c]]
    for c in leaves:
        a = A[c]
        try:
            a["dur"] = int(a["기간"])
            if a["dur"] < 1: raise ValueError
        except Exception:
            fails.append(f"{c} {a['작업명']}: 기간이 1 이상 정수가 아님 ('{a.get('기간')}')"); a["dur"] = 1
        if a.get("캘린더") not in cal.W:
            fails.append(f"{c}: 캘린더 '{a.get('캘린더')}' 가 calendar.csv에 없음"); a["캘린더"] = list(cal.W)[0]
    P = {c: [] for c in leaves}; S = {c: [] for c in leaves}
    for i, r in enumerate(rels, 2):
        a, b, t = r["선행"], r["후행"], r["관계"].upper()
        try: lag = int(r.get("시차") or 0)
        except ValueError: fails.append(f"relations.csv {i}행: 시차가 정수가 아님"); lag = 0
        if a not in P or b not in P:
            fails.append(f"relations.csv {i}행: {a}→{b} - 존재하는 세부작업(자식 없는 행)끼리만 연결 가능"); continue
        if t not in ("FS", "SS"):
            fails.append(f"relations.csv {i}행: 관계 '{t}' (FS 또는 SS만)"); continue
        P[b].append((a, t, lag)); S[a].append((b, t, lag))
    # 위상정렬
    indeg = {c: len(P[c]) for c in leaves}
    q = [c for c in leaves if indeg[c] == 0]; order = []
    while q:
        c = q.pop(0); order.append(c)
        for b, _, _ in S[c]:
            indeg[b] -= 1
            if indeg[b] == 0: q.append(b)
    if len(order) != len(leaves):
        fails.append("선행관계 순환: " + ", ".join(sorted(set(leaves) - set(order))))
    return A, kids, leaves, P, S, order


# ---------------------------------------------------------------- CPM
def cpm(A, P, S, order, cal, start, status=None, prog=None):
    """prog: {코드: dict(AS, AF, rem)} - status(기준일) 이전 실적 고정"""
    ES, EF, DRV = {}, {}, {}
    prog = prog or {}
    for c in order:
        a, k = A[c], A[c]["캘린더"]
        pg = prog.get(c)
        if pg and pg.get("AF"):
            ES[c], EF[c] = pg["AS"], pg["AF"]; continue
        if pg and pg.get("AS"):
            ES[c] = pg["AS"]
            s = cal.next_work(k, status + ONE)
            EF[c] = cal.shift(k, s, pg["rem"] - 1) if pg["rem"] > 0 else cal.prev_work(k, status)
            continue
        lo = [start] + ([status + ONE] if status else [])
        cons = [(p, EF[p] + ONE * (1 + lag) if t == "FS" else ES[p] + ONE * lag) for p, t, lag in P[c]]
        ES[c] = cal.next_work(k, max(lo + [x[1] for x in cons]))
        EF[c] = cal.shift(k, ES[c], a["dur"] - 1)
        DRV[c] = [p for p, x in cons if cal.next_work(k, x) == ES[c]]   # 이 작업의 시작을 결정한 선행
    T = max(EF.values())
    LS, LF = {}, {}
    for c in reversed(order):
        a, k = A[c], A[c]["캘린더"]
        rem_dur = a["dur"]
        pg = prog.get(c)
        if pg and pg.get("AF"):                  # 완료 작업: 실적 고정
            LS[c], LF[c] = ES[c], EF[c]; continue
        if pg and pg.get("AS") and not pg.get("AF"):
            rem_dur = max(cal.idx(k, EF[c]) - cal.idx(k, cal.next_work(k, status + ONE)) + 1, 1)
        bounds = [cal.prev_work(k, T)]
        for b, t, lag in S[c]:
            if t == "FS":
                bounds.append(cal.prev_work(k, LS[b] - ONE * (1 + lag)))
            else:
                ls_b = cal.prev_work(k, LS[b] - ONE * lag)
                bounds.append(cal.shift(k, ls_b, rem_dur - 1))
        LF[c] = min(bounds)
        LS[c] = cal.shift(k, LF[c], -(rem_dur - 1)) if not (pg and pg.get("AS")) else ES[c]
    TF = {}
    for c in order:
        k = A[c]["캘린더"]
        pg = prog.get(c)
        TF[c] = None if (pg and pg.get("AF")) else cal.idx(k, LF[c]) - cal.idx(k, EF[c])
    # 주공정 = 준공일을 결정하는 연속 경로(longest path): 준공 작업에서 '결정 선행'을 거슬러 추적
    CP, stack = set(), [c for c in order if EF[c] == T]
    while stack:
        c = stack.pop()
        if c in CP: continue
        CP.add(c); stack += DRV.get(c, [])
    return ES, EF, LS, LF, TF, T, CP


def rollup(A, kids, ES, EF):
    def go(c):
        if not kids[c]: return ES[c], EF[c]
        r = [go(k) for k in kids[c]]
        s, f = min(x[0] for x in r), max(x[1] for x in r)
        A[c]["_s"], A[c]["_f"] = s, f
        return s, f
    for c in A:
        if not A[c].get("상위코드"): go(c)


def result_rows(A, kids, order, ES, EF, LS, LF, TF, CP):
    rows = []
    for c, a in A.items():                       # activities.csv 순서 유지
        if kids[c]:
            rows.append({"작업코드": c, "상위코드": a.get("상위코드", ""), "레벨": a["레벨"], "작업명": a["작업명"],
                         "구분": "요약", "시작일": a["_s"], "종료일": a["_f"]})
        else:
            tf = TF[c]
            rows.append({"작업코드": c, "상위코드": a.get("상위코드", ""), "레벨": a["레벨"], "작업명": a["작업명"],
                         "구분": "세부", "기간": a["dur"], "캘린더": a["캘린더"], "시작일": ES[c], "종료일": EF[c],
                         "최늦시작": LS[c], "최늦종료": LF[c], "여유": "" if tf is None else tf,
                         "주공정": "★" if c in CP else ""})
    return rows


COLS = ["작업코드", "상위코드", "레벨", "작업명", "구분", "기간", "캘린더", "시작일", "종료일", "최늦시작", "최늦종료", "여유", "주공정"]


# ---------------------------------------------------------------- 진도·단축
def load_progress(path, A, cal, fails):
    prog = {}
    for i, r in enumerate(read_csv(path), 2):
        c = r["작업코드"]
        if c not in A: fails.append(f"진도 {i}행: 작업코드 {c} 없음"); continue
        AS = D(r["실제시작"]) if r.get("실제시작") else None
        AF = D(r["실제종료"]) if r.get("실제종료") else None
        rem = int(r.get("잔여기간") or 0)
        if AS: prog[c] = dict(AS=AS, AF=AF, rem=rem)
    return prog


def apply_crash(path, A, crash, prog, fails):
    plan, seen = [], set()
    for i, r in enumerate(read_csv(path), 2):
        c = r["작업코드"]
        if c in seen: fails.append(f"단축안 {i}행: {c} 중복 - 작업당 한 행으로 합쳐서 입력"); continue
        seen.add(c)
        try: n = int(r["단축일"])
        except Exception: fails.append(f"단축안 {i}행: 단축일이 정수가 아님"); continue
        if c not in crash: fails.append(f"단축안 {i}행: {c}는 crash.csv에 없는(단축 불가) 작업"); continue
        if n < 1 or n > crash[c]["max"]: fails.append(f"단축안 {i}행: {c} 단축일 {n} - 허용 1~{crash[c]['max']}"); continue
        if c in prog and prog[c].get("AF"): fails.append(f"단축안 {i}행: {c}는 이미 완료된 작업"); continue
        if c in prog:                          # 진행 중이면 잔여기간에서 단축
            if prog[c]["rem"] - n < 1: fails.append(f"단축안 {i}행: {c} 잔여 {prog[c]['rem']}일보다 많이 단축"); continue
            prog[c]["rem"] -= n
        else:
            if A[c]["dur"] - n < 1: fails.append(f"단축안 {i}행: {c} 기간보다 많이 단축"); continue
            A[c]["dur"] -= n
        plan.append((c, n, n * crash[c]["cost"]))
    return plan


# ---------------------------------------------------------------- 메인
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="공정 데이터 검증기")
    ap.add_argument("--data", default=here)
    ap.add_argument("--compare"); ap.add_argument("--progress"); ap.add_argument("--crash"); ap.add_argument("--out")
    a = ap.parse_args()
    proj, acts, rels, cal = load(a.data)
    fails, warns = [], []
    A, kids, leaves, P, S, order = build(acts, rels, cal, fails)
    if fails: report(fails, warns); sys.exit(1)
    start = D(proj["착공일"])

    # 기준 공정표
    bES, bEF, bLS, bLF, bTF, bT, bCP = cpm(A, P, S, order, cal, start)
    base_finish = bT
    prog, status, plan = None, None, []
    if a.progress:
        status = D(proj["진도기준일"])
        prog = load_progress(a.progress, A, cal, fails)
        if a.crash:
            crash = {r["작업코드"]: dict(max=int(r["최대단축일"]), cost=int(r["일당추가비용(만원)"]))
                     for r in read_csv(os.path.join(a.data, "crash.csv"))}
            plan = apply_crash(a.crash, A, crash, prog, fails)
        if fails: report(fails, warns); sys.exit(1)
    elif a.crash:
        sys.exit("[실패] --crash는 --progress와 함께 사용 (진도 반영 후 만회안을 평가)")
    ES, EF, LS, LF, TF, T, CP = cpm(A, P, S, order, cal, start, status, prog) if prog is not None else (bES, bEF, bLS, bLF, bTF, bT, bCP)
    rollup(A, kids, ES, EF)

    # 요약 출력
    contract = D(proj["계약준공일"])
    title = "진도 반영 예측" if prog is not None else "기준 공정표"
    print(f"=== {title} - {proj['공사명']} ===")
    print(f"착공 {start}  →  준공 {T}   (공기 {(T - start).days + 1}일, 계약준공 {contract}, 차이 {(T - contract).days:+d}일)")
    if prog is not None:
        print(f"진도 기준일 {status}  |  실적 반영 작업 {len(prog)}개")
    ld_day = proj.get("지체상금_일당(만원)", 0)
    if T > contract and ld_day:
        print(f"※ 계약준공 초과 {(T - contract).days}일 → 지체상금 약 {(T - contract).days * ld_day:,}만원 (일 {ld_day:,}만원)")
    print("\n[마일스톤]  이름 | 계획일 | 계약목표 | 차이")
    for m in proj["마일스톤"]:
        c = m["작업코드"]
        d = (A[c]["_f"] if kids.get(c) else EF[c]) if m.get("기준", "종료") == "종료" else (A[c]["_s"] if kids.get(c) else ES[c])
        tgt = D(m["계약목표일"])
        print(f"  ◆ {m['이름']} | {d} | {tgt} | {(d - tgt).days:+d}일")
    crit = [c for c in order if c in CP]
    print(f"\n[주공정선] 작업 {len(crit)}개 (준공일을 결정하는 연속 경로)")
    print("  " + " → ".join(f"{c} {A[c]['작업명']}" for c in crit))
    if plan:
        cost = sum(x[2] for x in plan)
        no_crash = cpm(*_reload_for_nocrash(a, proj), cal, start, status, load_progress(a.progress, A, cal, []))[5]
        saved = (no_crash - T).days
        ind = proj["간접비_일당(만원)"]
        late = lambda d: max((d - contract).days, 0)
        ld_saved = (late(no_crash) - late(T)) * ld_day
        print(f"\n[단축안 평가] 단축 작업 {len(plan)}개, 추가비용(직접비) {cost:,}만원")
        for c, n, w in plan:
            print(f"  - {c} {A[c]['작업명']}: {n}일 단축, {w:,}만원" + ("" if c in CP else "  ※ 단축 후 주공정 아님 - 효과 확인"))
        print(f"  준공 {no_crash} → {T} ({saved}일 당김, 계약준공 대비 {(T - contract).days:+d}일)")
        print(f"  간접비 절감 {saved * ind:,}만원 + 지체상금 감소 {ld_saved:,}만원 - 추가비용 {cost:,}만원 = 순효과 {saved * ind + ld_saved - cost:+,}만원")

    # 학생 결과 대조
    if a.compare:
        rows = read_csv(a.compare)
        need = {"작업코드", "시작일", "종료일"}
        if not rows or not need <= set(rows[0]):
            fails.append(f"--compare 파일에 필수 열 {sorted(need)} 필요 (현재 {list(rows[0]) if rows else '빈 파일'})")
        else:
            bad, seen = [], set()
            for r in rows:
                c = r["작업코드"]
                if c not in A or kids[c]: continue
                seen.add(c)
                try:
                    if D(r["시작일"]) != ES[c] or D(r["종료일"]) != EF[c]:
                        bad.append(f"{c} {A[c]['작업명']}: 제출 {r['시작일']}~{r['종료일']} / 검증 {ES[c]}~{EF[c]}")
                    elif r.get("여유", "") not in ("",) and TF[c] is not None and int(float(r["여유"])) != TF[c]:
                        bad.append(f"{c} {A[c]['작업명']}: 여유 제출 {r['여유']} / 검증 {TF[c]}")
                except Exception:
                    bad.append(f"{c}: 날짜 형식 오류 ({r['시작일']}, {r['종료일']}) - YYYY-MM-DD")
            miss = [c for c in leaves if c not in seen]
            if miss: fails.append(f"제출 결과에 없는 세부작업 {len(miss)}개: {miss[:8]}{' …' if len(miss) > 8 else ''}")
            if bad:
                fails.append(f"일정 불일치 {len(bad)}건 (처음 10건):\n    " + "\n    ".join(bad[:10]))
            else:
                print(f"\n[대조] 제출 {len(seen)}개 세부작업 일정이 검증기와 일치")

    if a.out:
        write_csv(a.out, result_rows(A, kids, order, ES, EF, LS, LF, TF, CP), COLS)
        print(f"\n[저장] {a.out}")
    report(fails, warns)
    sys.exit(1 if fails else 0)


def _reload_for_nocrash(a, proj):
    """단축 전 상태(원래 기간)로 다시 적재 - 단축 효과 비교용"""
    _, acts, rels, cal = load(a.data)
    A, kids, leaves, P, S, order = build(acts, rels, cal, [])
    return A, P, S, order


def report(fails, warns):
    print("\n==== 검증 결과 ====")
    for w in warns: print("[주의] " + w)
    for f in fails: print("[실패] " + f)
    print("[통과] 실패 항목 없음" if not fails else f"[실패] {len(fails)}건 - 수정 후 다시 실행")


if __name__ == "__main__":
    main()
