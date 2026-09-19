# 로블록스 사용량 관측소

로블록스 전체 플랫폼과 게임별 이용량을 **추정값 없이** 관찰하는 한국어 대시보드.

## 운영 구조 (2026-09-17~)

- 수집·배포: GitHub Actions `.github/workflows/collect-deploy.yml`
- **주 동력 = 릴레이(2026-09-18~, PC 불필요)**: 각 수집 회차 끝의 `relay` 잡이 다음 정시+5분까지 기다렸다가
  자기 자신을 `workflow_dispatch` 로 실행한다. GitHub 예약은 이 계정에서 몇 시간에 한 번만 걸려서
  (09-17~18 실측: 이 저장소 18시간 9회, 교와 `*/30` 약 5시간 간격) 예약만으로는 매시간이 안 된다.
- 예약: 매시 4·14·24·34·44·54분(이미 수집한 시간대면 요청 없이 종료) — 릴레이가 끊기면 다시 잇는 용도
- 보조 트리거: GitHub 예약이 자주 유실돼서(2026-09-17 18시간에 8회, 09-18 70분간 0회) 사용자 PC 작업 스케줄러
  `RobloxObservatoryKick` 이 15분마다 `scripts/local/kick_if_due.py` 를 돌린다. collect.py 와 같은 규칙으로
  이번 시간대 수집이 빠졌고 정각에서 10분이 지났으면 `workflow_dispatch` 로 깨운다(로그 `logs/kick.log`).
  같은 작업이 `scripts/local/romonitor_local.py` 도 부른다 → RoMonitor 를 한 시간에 1건 직접 받아 data 브랜치에 push
  (GitHub 러너는 Cloudflare 봇 확인 `cf-mitigated: challenge` 403 이라 PC 전용, 2026-09-19 확인).
  "절전 해제하고 실행"(WakeToRun) 켜짐 → 절전 중에도 동작, 완전 종료 중엔 멈췄다가 켜질 때 최대 14일치 소급.
  끄려면 `Unregister-ScheduledTask -TaskName RobloxObservatoryKick`
- 코드: `main` 브랜치 / 데이터: `data` 브랜치(`public/data`, `archive/`)
- 사이트: `main` 화면 코드 + `data` 브랜치 데이터를 합쳐 GitHub Pages 로 배포
- LLM 없이 `scripts/collect.py`(파이썬 표준 라이브러리)만 돈다. 예전 Codex 에이전트 자동화는 사용량 한도에 걸리면 멈췄음.

### 수집 주기

| 대상 | 주기 | 원천 |
|---|---|---|
| 플랫폼 CCU **RoMonitor(기준)** | 매시간 직접(차트 API 1건, 최근 2일) + 6시간마다 웨이백 새 사본 확인 | romonitorstats.com 차트 API(비로그인, 최근 약 20일) · 과거 2023-04~는 웨이백 보관 사본 |
| 플랫폼 CCU RoTrends(일별·시간별) | 매시간 | robloxccu.com 아카이브 + `live_edge.json` 누적 병합 |
| 게임 상위 100 | 매시간 | Roblox 공개 게임 API `playing` |
| 게임 상위 1,000 | UTC 4시간 블록마다 | 〃 |
| 추적 게임 전체(약 3,900개) | UTC 하루 1회 | 〃 |
| 탐색 보드에 떠 있는 게임(약 230개) + 워치리스트(`data/watchlist.json`) | 매시간(등급 무관, 항상 포함) | 〃 |
| 연령등급·탐색 보드 순위 | 매시간 | 탐색 API `get-sorts`, 가이드라인 API |

- 주기는 "이번 블록에 이미 성공했나"로 판단 → 예약 실행이 밀리거나 두 번 돌아도 중복·누락 없음
- 순위는 가장 최근 실측 `playing` 기준(실측 없는 게임은 과거 일별 아카이브 평균)
- `live_edge.json` 은 최근 약 35일만 담는 이동 창이라 **기존 행을 지우지 않고 덮어쓰기만** 한다

## 데이터 정책

- `platform_romonitor.json`: **플랫폼 수준·전년 비교의 기준(2026-09-19~)**. RoMonitor 30분 값, 2023-04-16~.
  - 웨이백 사본 1건 = 14일치. 0 이하·앞뒤 1시간 최저값의 25% 미만 칸은 측정 오류로 `excluded` 에 따로 둠(보간 없음)
  - 화면의 RoMonitor 일별값은 30분 칸이 44개 이상 찬 날만 씀
  - ⚠️ RoMonitor 약관은 스크립트로 사이트 데이터를 긁거나 복사하는 걸 금지함. 사용자가 알고 공개 게시·매시간 직접 수집을 결정함(2026-09-19). 회차당 1건, 차단되면 우회하지 않음
- `platform_daily.json`, `platform_hourly.json`: RoTrends(robloxccu) 관측값(제3자 집계, Roblox 공식 합계 아님)
  - ⚠️ 2025-09부터 RoMonitor·공식 이용시간 대비 높게 나옴(같은 날 RoTrends÷RoMonitor 2025-08 1.02 → 2026-09 1.53). 수준·전년 비교에 쓰지 말 것
- `reported_points.json`: 명시적 estimate/approximate 항목을 제외한 과거 보고 포인트
- `game_history_daily.json`: 초기 일별 아카이브(Early Shift·BloxScout)
- `live_game_history.json`: 공개 게임 API 실측, 최근 30일, `compact-v1` 형식
- `archive/live_games/YYYY-MM.csv.gz`: 공개 게임 API 원시 응답 영구 보관(대시보드는 읽지 않음)
- `archive/boards/YYYY-MM.csv.gz`: 탐색 보드(인기·트렌딩 등) 순위 스냅샷 — '발견 보드' 화면이 요구하던 원시 데이터
- `chart_top_playing.json`: 최신 Top Playing Now 순위. ⚠️ 비로그인 차트라 Restricted(18+) 경험은 빠짐
- `game_diurnal.json`, `diurnal_index.json`: 시간대 프로파일(`scripts/build_diurnal.py` 가 실측에서 계산)
- CCU에서 DAU를 환산하지 않음 / 빈 구간을 보간하지 않음 / 응답에 없는 게임을 0으로 채우지 않음
- 삭제·비공개·콘텐츠 제한 게임은 API가 id 0 `[TITLE UNAVAILABLE]` 로 돌려줌 → 버림
  - ⚠️ Brookhaven 🏡RP(1686885941)도 비로그인 API에서 콘텐츠 제한으로 안 잡힘(2026-09-17 확인)

## 로컬 실행

```powershell
& .\scripts\run-collection.ps1              # 예약 규칙대로 수집 (이 폴더 public/data 에 씀)
& .\scripts\run-collection.ps1 -ForceTier all
python -m http.server 8000                   # http://localhost:8000
```

### 시간대 프로파일

- 같은 게임을 매시간 관측해 UTC 시각별 평균 CCU 를 만들고, 전체 곡선(프로파일이 나온 게임들의 시각별 합) 대비 몇 배로 쏠렸는지를 계산한다.
- 구간(UTC): 아시아 저녁 09~13(키 이름 asiaDay) / 유럽 저녁 15~19 / 미국 저녁 22~02 / 미국 심야 04~08(= 아시아 낮)
- **시간대 쏠림이지 지역·연령 측정이 아니다.** 같은 시간대 안에 아이와 성인이 섞여 있다.
- 외부에서 모은 같은 형식의 관측 jsonl 은 `scripts/import_snapshots.py` 로 합친다(1회성 백필).

`public/data` 는 `main` 에서 추적하지 않는다. 운영 데이터는 `data` 브랜치에 있다.
초기 아카이브를 다시 만들 때만 `scripts/build-data.ps1` 을 쓴다.
