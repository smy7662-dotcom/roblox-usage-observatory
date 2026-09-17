# Roblox Usage Observatory

Roblox 전체 플랫폼과 게임별 이용량을 **추정값 없이** 관찰하기 위한 로컬 대시보드 MVP.

## 데이터 정책

- `platform_daily.json`, `platform_hourly.json`: 공개 CCU 관측값
- `reported_points.json`: 명시적 estimate/approximate 항목을 제외한 과거 보고 포인트
- `games_daily.json`: 공개 게임별 일별 관측값
- CCU에서 DAU를 환산하지 않음
- 빈 구간을 보간하지 않음
- `Average`와 `Peak`는 관측 샘플의 단순 집계임

## 실행

PowerShell에서 프로젝트 폴더로 이동한 뒤:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-data.ps1
python -m http.server 8000
```

브라우저에서 http://localhost:8000 을 연다. 브라우저 보안상 `index.html`을 직접 여는 대신 로컬 HTTP 서버를 사용한다.

## 현재 구현된 기능

- 기간: 1D, 7D, 30D, 90D, 1Y, 2Y, 5Y, 전체, 사용자 지정
- Frequency: Hourly, Daily, Weekly, Monthly
- 집계: Average, Peak
- 공식·보고 포인트 토글
- CSV/JSON 다운로드, 차트 전체화면
- 데이터 신선도·포인트 수·게임별 상위 표
