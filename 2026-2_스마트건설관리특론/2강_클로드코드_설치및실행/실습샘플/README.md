# 실습샘플 — 3D 현장 진도 대시보드 (2026-09-29 드라이런)
- 프롬프트: `실습_프롬프트.txt` (단일 프롬프트, 696자). 강사 WSL Claude Code 2.1.284 · Sonnet · acceptEdits 모드로 실행.
- 결과: 18턴 · 약 4분 · 산출 30k 토큰(사고 12.8k 포함) · API 환산 $0.66. 산출물 `00_practice/`(plan.md·schedule.csv·build_data.py·data.js·dashboard.html).
- 화면: `대시보드_5개월차.png`(three.js 15층 매스 + KPI + SVG 간트 + Chart.js S커브, 다크 테마). 브라우저로 열 때 CDN 인터넷 필요.
- 데이터 교체: `sources/schedule.csv` 수정 → `python build_data.py` → 새로고침, 또는 대시보드 "CSV 불러오기" 버튼.
