# 로블록스 사용량 관측소

로블록스 전체 플랫폼과 게임별 이용량을 **추정값 없이** 관찰하는 한국어 대시보드.

## 데이터 정책

- `platform_daily.json`, `platform_hourly.json`: 공개 CCU 관측값
- `reported_points.json`: 명시적 estimate/approximate 항목을 제외한 과거 보고 포인트
- `games_daily.json`: 공개 게임별 일별 관측값
- `game_history_daily.json`: 초기 일별 아카이브와 현재 게임 관측값을 합친 장기 시계열
- `live_games.json`: 선택적으로 수집하는 Roblox 공개 게임 API 스냅샷
- CCU에서 DAU를 환산하지 않음
- 빈 구간을 보간하지 않음
- 평균과 피크는 관측 샘플의 단순 집계임
- 서로 다른 원천의 구간은 상세 화면에서 원천을 구분함

## 실행

PowerShell에서 프로젝트 폴더로 이동한 뒤:

```powershell
& .\scripts\build-data.ps1
python -m http.server 8000
```

브라우저에서 http://localhost:8000 을 연다. 브라우저 보안상 `index.html`을 직접 여는 대신 로컬 HTTP 서버를 사용한다.

## 현재 구현된 기능

- 기간: 7일, 30일, 90일, 1년, 전체, 사용자 지정
- 주기: 시간별, 일별, 주별, 월별
- 집계: 평균, 피크
- 플랫폼 개요, 게임 탐색, 게임 상세 시계열, 게임 비교
- 장르별 구성, 제작자 데이터 준비 화면, 발견 보드 수집 준비 화면
- CSV/JSON 다운로드, 차트 크게 보기
- 데이터 범위 카드와 공백·보간 정책 표시

## 공개 게임 API 스냅샷 수집

현재 보유한 게임 ID에서 Roblox 공개 게임 API의 최신 `playing`, 방문 수, 즐겨찾기, 제작자, 장르 정보를 일괄 수집한다.

```powershell
& .\scripts\collect-public-snapshot.ps1
```

이 수집기는 인증 키를 사용하지 않으며, 플랫폼 전체 CCU나 DAU를 만들어내지 않는다. 수집 후 대시보드를 다시 빌드하면 제작자·장르 확장에 사용할 수 있다.
