#!/usr/bin/env python3
"""로블록스 사용량 관측소 — 자동 수집기 (GitHub Actions·로컬 공용, 표준 라이브러리만 사용)

한 번 실행하면:
  1. 플랫폼 CCU: robloxccu.com 아카이브 + live_edge 를 기존 파일에 **누적 병합**한다.
     live_edge 는 최근 약 35일만 담는 이동 창이라, 매번 아카이브+edge 로 새로 만들면
     창 밖으로 밀려난 시간대가 사라진다. 그래서 기존 행은 지우지 않고 덮어쓰기만 한다.
     시각은 문자열로 받아 UTC 로만 정규화한다 (PowerShell 날짜 자동 변환으로 시간대가
     어긋났던 문제 재발 방지).
  2. 게임별 CCU: Roblox 공개 게임 API 의 playing 값을 등급별 주기로 수집한다.
     - top100: 매시간 / top1000: UTC 4시간 블록마다 / all: UTC 하루 1회
     - 주기는 "이번 블록에 이미 성공했는가"로 판단해서, 예약 실행이 밀리거나 두 번 돌아도
       중복·누락 없이 동작한다.
     - 응답에 없는 게임은 0 으로 채우지 않는다. DAU 환산·보간 없음.
  3. 대시보드용 파일(public/data)과 원시 아카이브(archive/live_games/YYYY-MM.csv.gz)를 쓴다.

사용:
  python scripts/collect.py --data-root <데이터 루트>          # 예약 실행
  python scripts/collect.py --data-root <루트> --force-tier all  # 등급 강제
  python scripts/collect.py --data-root <루트> --no-games        # 플랫폼만
종료 코드: 0 = 저장까지 성공(일부 실패는 status.errors 에 기록), 2 = 아무 것도 저장 못 함.
"""

import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ARCHIVE_DAILY_URL = "https://robloxccu.com/data/ccu_daily.json"
ARCHIVE_HOURLY_URL = "https://robloxccu.com/data/ccu_hourly.json"
EDGE_URL = "https://robloxccu.com/data/live_edge.json"
GAMES_API = "https://games.roblox.com/v1/games"
SORTS_API = "https://apis.roblox.com/explore-api/v1/get-sorts?sessionId=observatory&device=all&country=all"
AGE_API = "https://apis.roblox.com/experience-guidelines-api/experience-guidelines/get-age-recommendation"
USER_AGENT = "roblox-usage-observatory/1.0 (+https://github.com/smy7662-dotcom/roblox-usage-observatory)"

TIER_ORDER = ["top100", "top1000", "all"]
TIER_SIZE = {"top100": 100, "top1000": 1000, "all": None}
# 원시 관측은 화면이 매번 통째로 받으므로 짧게 둔다. 장기 값은 build_game_stats.py 가
# games/{id}.json 에 일단위로 영구 보존한다.
HISTORY_DAYS = 7
# 게임 메타 배열 = [이름, 장르, 제작자, 연령등급, 최소연령]. 앞 3칸은 옛 화면 코드가 그대로 읽는다.
META_LEN = 5
AGE_LOOKUPS_PER_RUN = 20  # 등급 모르는 게임을 한 회차에 몇 개까지 조회할지

TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def log(msg):
    print(msg, flush=True)


def utc_now():
    return datetime.now(timezone.utc)


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    """ISO 문자열 → UTC datetime. 시간대 표기가 없으면 UTC 로 본다(원천이 전부 UTC 'Z')."""
    m = TS_RE.match(str(value).strip())
    if not m:
        raise ValueError(f"시각 형식 아님: {value!r}")
    y, mo, d, h, mi, s, tz = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=timezone.utc)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        dt = dt - sign * timedelta(hours=hh, minutes=mm)
    return dt


def norm_day(value):
    text = str(value).strip()
    if DAY_RE.match(text):
        return text
    return parse_ts(text).strftime("%Y-%m-%d")


def num(value):
    if value is None:
        return None
    f = float(value)
    return int(f) if f.is_integer() else f


def read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def http_json(url, attempts=6):
    """GET → (json, headers). 429·5xx·네트워크 오류는 Retry-After 또는 지수 백오프로 재시도."""
    last = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8")), resp.headers
        except urllib.error.HTTPError as err:
            last = err
            if err.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
            retry_after = err.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else min(60, 10 * 2 ** attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last = err
            if attempt == attempts - 1:
                raise
            wait = min(60, 5 * 2 ** attempt)
        log(f"  재시도 {attempt + 1}/{attempts - 1}: {last} → {wait}초 대기")
        time.sleep(wait)
    raise last


def http_post_json(url, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── 1. 플랫폼 CCU ─────────────────────────────────────────────────────────────

def collect_platform(data_dir, status, last_archive_fetch=None):
    daily_path = os.path.join(data_dir, "platform_daily.json")
    hourly_path = os.path.join(data_dir, "platform_hourly.json")
    old_daily = read_json(daily_path, [])
    old_hourly = read_json(hourly_path, [])

    daily = {}
    for row in old_daily:
        d = norm_day(row["date"])
        daily[d] = {**row, "date": d, "avg": num(row.get("avg")), "peak": num(row.get("peak"))}
    hourly = {}
    for row in old_hourly:
        ts = iso_z(parse_ts(row["timestamp"]))
        hourly[ts] = {**row, "timestamp": ts, "ccu": num(row.get("ccu")), "peak": num(row.get("peak"))}

    # 아카이브는 실패해도 치명적이지 않다(이미 기존 파일에 들어 있음).
    # 2026-07-01 에서 끝난 정적 파일(시간별 약 0.9MB)이라 매시간 받을 필요가 없다 → 하루 한 번만.
    status["lastArchiveFetch"] = last_archive_fetch
    if not (last_archive_fetch and utc_now() - parse_ts(last_archive_fetch) < timedelta(hours=24)):
        status["lastArchiveFetch"] = iso_z(utc_now())
        try:
            archive_daily, _ = http_json(ARCHIVE_DAILY_URL)
            for row in archive_daily:
                if not row.get("date"):
                    continue
                d = norm_day(row["date"])
                daily[d] = {"date": d, "avg": num(row.get("ccu_avg")), "peak": num(row.get("ccu_peak")),
                            "source": "robloxccu_daily_archive", "sourceUrl": ARCHIVE_DAILY_URL}
            archive_hourly, _ = http_json(ARCHIVE_HOURLY_URL)
            for row in archive_hourly.get("measured", []):
                if not row.get("t"):
                    continue
                ts = iso_z(parse_ts(row["t"]))
                hourly[ts] = {"timestamp": ts, "ccu": num(row.get("ccu")), "peak": num(row.get("peak")),
                              "source": "robloxccu_hourly_archive", "sourceUrl": ARCHIVE_HOURLY_URL}
        except Exception as err:  # noqa: BLE001 — 원인만 기록하고 edge 병합은 계속
            status["errors"].append(f"플랫폼 아카이브 수신 실패(기존값 유지): {err}")

    edge, _ = http_json(EDGE_URL)
    if not edge.get("daily") or not edge.get("hourly"):
        raise RuntimeError("live_edge 응답에 daily/hourly 가 없음")
    for row in edge["daily"]:
        d = norm_day(row["t"])
        daily[d] = {"date": d, "avg": num(row.get("ccu")), "peak": num(row.get("peak")),
                    "source": "robloxccu_live_edge", "sourceUrl": EDGE_URL}
    for row in edge["hourly"]:
        ts = iso_z(parse_ts(row["t"]))
        hourly[ts] = {"timestamp": ts, "ccu": num(row.get("ccu")), "peak": num(row.get("peak")),
                      "source": "robloxccu_live_edge", "sourceUrl": EDGE_URL}

    daily_out = [daily[k] for k in sorted(daily)]
    hourly_out = [hourly[k] for k in sorted(hourly)]
    if len(daily_out) < len(old_daily) or len(hourly_out) < len(old_hourly):
        raise RuntimeError(f"병합 결과가 기존보다 짧음(일별 {len(old_daily)}→{len(daily_out)}, "
                           f"시간별 {len(old_hourly)}→{len(hourly_out)}) — 저장 중단")

    write_json(daily_path, daily_out)
    write_json(hourly_path, hourly_out)
    write_json(os.path.join(data_dir, "platform_edge_latest.json"), {
        "updatedAt": edge.get("updated_at"),
        "source": edge.get("source"),
        "sourceUrl": EDGE_URL,
        "factor": edge.get("factor"),
        "dailyStart": daily_out[0]["date"], "dailyEnd": daily_out[-1]["date"],
        "hourlyStart": hourly_out[0]["timestamp"], "hourlyEnd": hourly_out[-1]["timestamp"],
        "policy": "Roblox 공식 플랫폼 합계가 아닌, 공개 게임 관측값을 집계하는 제3자 원천. 값은 원천 그대로 보존.",
    })

    meta_path = os.path.join(data_dir, "meta.json")
    meta = read_json(meta_path, {})
    coverage = meta.setdefault("coverage", {})
    coverage["platformDaily"] = {"start": daily_out[0]["date"], "end": daily_out[-1]["date"]}
    coverage["platformHourly"] = {"start": hourly_out[0]["timestamp"], "end": hourly_out[-1]["timestamp"]}
    write_json(meta_path, meta)

    status["platform"] = {
        "edgeUpdatedAt": edge.get("updated_at"),
        "daily": len(daily_out), "hourly": len(hourly_out),
        "addedDaily": len(daily_out) - len(old_daily), "addedHourly": len(hourly_out) - len(old_hourly),
        "hourlyEnd": hourly_out[-1]["timestamp"],
    }
    log(f"플랫폼: 일별 {len(old_daily)}→{len(daily_out)}, 시간별 {len(old_hourly)}→{len(hourly_out)}, "
        f"edge {edge.get('updated_at')}")


# ── 1.5 연령등급·인기 차트 ────────────────────────────────────────────────────
# 등급(Minimal/Mild/Moderate/Restricted)은 게임 API 에 없고 탐색·가이드라인 API 에만 있다.
# 비로그인 상태에서는 Restricted(18+) 게임이 차트에 아예 안 뜨므로, 차트 등급 분포는
# "비로그인으로 보이는 범위"라는 한계를 그대로 안고 간다.

BOARD_FIELDS = ["observed_at", "sort_id", "sort_name", "rank", "universe_id", "name", "playing",
                "maturity", "min_age"]


def fetch_sorts(root, data_dir, status, observed_at):
    """탐색 차트 1회 호출 → 등급 라벨 + 보드 순위.

    부산물로 보드별 순위를 archive/boards 에 쌓는다. '발견 보드' 화면이 요구하던
    (보드명·관측시각·게임·순위) 원시 데이터가 이것이고, 진입·이탈·체류는 쌓인 뒤 계산한다.
    """
    try:
        payload, _ = http_json(SORTS_API, attempts=3)
    except Exception as err:  # noqa: BLE001
        status["errors"].append(f"탐색 차트 수신 실패: {err}")
        return {}
    labels, top, board_rows = {}, [], []
    for sort in payload.get("sorts", []):
        for rank, g in enumerate(sort.get("games") or [], 1):
            uid = str(g.get("universeId") or "")
            if not uid.isdigit() or uid == "0":
                continue
            labels[uid] = (g.get("contentMaturity"), g.get("minimumAge"))
            board_rows.append([observed_at, sort.get("sortId"), sort.get("sortDisplayName"), rank, uid,
                               g.get("name"), num(g.get("playerCount")), g.get("contentMaturity"),
                               g.get("minimumAge")])
            if sort.get("sortId") == "top-playing-now":
                top.append({"rank": rank, "universeId": uid, "name": g.get("name"),
                            "playing": num(g.get("playerCount")), "maturity": g.get("contentMaturity"),
                            "minAge": g.get("minimumAge")})
    if board_rows:
        append_csv_gz(os.path.join(root, "archive", "boards", observed_at[:7] + ".csv.gz"),
                      BOARD_FIELDS, board_rows)
    if top:
        write_json(os.path.join(data_dir, "chart_top_playing.json"), {
            "observedAt": iso_z(utc_now()), "sourceUrl": SORTS_API,
            "policy": "비로그인 탐색 차트 순위 그대로. Restricted(18+) 경험은 비로그인에 노출되지 않아 빠진다.",
            "games": top})
    status["sorts"] = {"labeled": len(labels), "topPlayingNow": len(top)}
    return labels


def fetch_age_ratings(uids, status):
    """등급을 모르는 게임만 가이드라인 API 로 개별 조회(회차당 상한)."""
    out = {}
    for uid in uids[:AGE_LOOKUPS_PER_RUN]:
        try:
            payload = http_post_json(AGE_API, {"universeId": str(uid)})
        except Exception as err:  # noqa: BLE001
            status["errors"].append(f"등급 조회 실패 {uid}: {err}")
            continue
        # 응답 형태(2026-09-18 실측): ageRecommendationDetails.summary.ageRecommendation
        #   {"displayName":"Mild","contentMaturity":"mild","minimumAge":0}
        rating = (payload.get("ageRecommendationDetails", {}).get("summary", {})
                  .get("ageRecommendation", {}))
        maturity = rating.get("contentMaturity")
        if maturity:
            out[str(uid)] = (maturity, rating.get("minimumAge"))
        time.sleep(1.0)
    return out


def load_watchlist(repo_root, status):
    """상위 차트 밖이라도 항상 관측할 게임 목록(코드 브랜치의 data/watchlist.json)."""
    path = os.path.join(repo_root, "data", "watchlist.json")
    payload = read_json(path, {})
    ids = [str(g["universeId"]) for g in payload.get("games", []) if str(g.get("universeId", "")).isdigit()]
    if ids:
        status["watchlist"] = len(ids)
    return ids


# ── 1-2. RoMonitor 플랫폼 CCU (웨이백 사본) ──────────────────────────────────
# 2026-09-19 부터 플랫폼 수준·전년 비교의 기준. RoTrends(robloxccu)는 2025-09 이후 공식 이용시간 대비 과대.
# RoMonitor 사이트에는 요청하지 않는다. 웨이백이 보관한 차트 응답(사본 1건 = 14일치 30분 값)만 받는다.
ROMONITOR_CDX = ("https://web.archive.org/cdx/search/cdx?url=romonitorstats.com/api/v1/charts/get"
                 "&matchType=prefix&output=json&fl=timestamp,original,statuscode"
                 "&filter=original:.*platform-ccus.*&limit=5000")
ROMONITOR_SERIES = ("Roblox Global CCUs", "Global Playing")  # 2023년 사본은 이름이 Global Playing
ROMONITOR_CHECK_HOURS = 6
ROMONITOR_STALE_HOURS = 26  # 최신 칸이 이보다 오래되면 하루 한 번(UTC 00시대) 오류로 올려 알림
ROMONITOR_MAX_FETCH = 20
# 측정 오류로 보는 값: 0 이하, 또는 앞뒤 1시간 안의 정상값 중 가장 작은 값의 25% 미만으로 뚝 떨어진 칸.
# 플랫폼 동접이 30분 사이 4분의 1 아래로 떨어졌다가 되돌아오는 건 측정 오류로 본다(2026-09-19 검수: 0값 15칸,
# 7,490명 같은 칸). 지운 칸은 excluded 에 원값·사유와 함께 남기고, 빈칸을 채우지 않는다.
ROMONITOR_GLITCH_RATIO = 0.25
# 직접 수집(2026-09-19 사용자 결정): RoMonitor 차트 API 1건으로 최근 2일치 30분 값을 받는다.
# ⚠️ GitHub Actions 러너에서는 403(2026-09-19 07:05Z 실측) → 사용자 PC(scripts/local/romonitor_local.py)에서만 돈다.
#    PC 가 꺼져 있던 동안은 다음 실행 때 최대 14일치를 거슬러 받아 빈칸을 메운다(API 가 최근 약 20일 보관).
# 비로그인으로 최근 약 20일까지 열림(Codex 세션 확인). 회차가 오래 비면 최대 14일까지 거슬러 받는다.
# ⚠️ RoMonitor 약관은 스크립트 수집을 금지함 — 사용자가 알고 결정. 요청은 회차당 1건, 차단(403·챌린지)되면 우회하지 않는다.
ROMONITOR_LIVE_URL = "https://romonitorstats.com/api/v1/charts/get?name=platform-ccus&timeslice=half-hourly&start={start}&ends={end}"
ROMONITOR_LIVE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


def split_romonitor_glitches(points):
    # 오류값끼리 붙어 있으면(예: 0 옆의 7,490명) 서로를 기준으로 삼아 못 잡으므로,
    # 이미 뺀 칸은 기준에서 제외하고 새로 빠지는 칸이 없을 때까지 반복한다.
    reason = {t: "0 이하" for t, v in points.items() if v is None or v <= 0}
    for _ in range(10):
        found = False
        for t, v in points.items():
            if t in reason:
                continue
            ref = []
            for k in (-2, -1, 1, 2):
                n = iso_z(parse_ts(t) + timedelta(minutes=30 * k))
                if n in points and n not in reason:
                    ref.append(points[n])
            if len(ref) >= 2 and v < ROMONITOR_GLITCH_RATIO * min(ref):
                reason[t] = f"앞뒤 1시간 최저값의 {v / min(ref):.3f}배"
                found = True
        if not found:
            break
    clean = sorted((t, v) for t, v in points.items() if t not in reason)
    excluded = sorted([t, points[t], r] for t, r in reason.items())
    return clean, excluded


def fetch_raw(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        b = resp.read()
    return gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b



def fetch_romonitor_live(points, now):
    last = max(points) if points else None
    start = now - timedelta(days=2)
    if last:
        start = min(start, parse_ts(last) - timedelta(hours=12))
    start = max(start, now - timedelta(days=14))
    url = ROMONITOR_LIVE_URL.format(start=start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), end=now.strftime("%Y-%m-%dT%H:%M:%S.999Z"))
    req = urllib.request.Request(url, headers={"User-Agent": ROMONITOR_LIVE_UA, "Accept": "application/json",
                                               "Referer": "https://romonitorstats.com/"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
    series = json.loads(body.decode("utf-8"))  # 차단 페이지(HTML)면 여기서 실패 → 우회하지 않고 기록만
    return next(x for x in series if x.get("name") in ROMONITOR_SERIES)["data"]

def collect_romonitor(data_dir, status, status_prev, now, live=False, wayback=True):
    """RoMonitor 플랫폼 CCU 를 합친다. live=직접 수집(PC 전용), wayback=웨이백 새 사본 확인(6시간마다).
    바뀐 게 있을 때만 파일을 다시 쓴다(PC 와 Actions 가 같은 파일을 번갈아 쓰므로 불필요한 충돌 방지)."""
    last = status_prev.get("lastRomonitorCheck")
    check_wayback = wayback and not (last and now - parse_ts(last) < timedelta(hours=ROMONITOR_CHECK_HOURS))
    path = os.path.join(data_dir, "platform_romonitor.json")
    payload = read_json(path, {})
    points = {t: v for t, v in payload.get("points", [])}
    for t, v, _ in payload.get("excluded", []):  # 원값은 계속 보관했다가 매번 같은 규칙으로 다시 가른다
        points[t] = v
    done = set(payload.get("captures", []))
    failed = dict(payload.get("failedCaptures", {}))
    changed = False

    live_added, live_changed = 0, 0
    last_live = payload.get("lastLiveFetch")
    if live:
        # 새로 받은 값이 이긴다(최근 칸은 RoMonitor 가 나중에 고치기도 함)
        for k, v in fetch_romonitor_live(points, now).items():
            if v is None:
                continue
            t = iso_z(parse_ts(k))
            if t not in points:
                live_added += 1
            elif points[t] != num(v):
                live_changed += 1
            points[t] = num(v)
        last_live = iso_z(now)
        changed = True

    rows = json.loads(fetch_raw(ROMONITOR_CDX).decode("utf-8"))[1:] if check_wayback else []
    todo = [r for r in rows if r[2] == "200" and r[0] not in done and failed.get(r[0], 0) < 3]
    todo.sort(key=lambda r: r[0], reverse=True)  # 최신 사본부터
    added, conflicts, fetched = 0, 0, 0
    for ts, orig, _ in todo[:ROMONITOR_MAX_FETCH]:
        changed = True
        try:
            series = json.loads(fetch_raw(f"https://web.archive.org/web/{ts}id_/{orig}").decode("utf-8"))
            data = next(x for x in series if x.get("name") in ROMONITOR_SERIES)["data"]
        except Exception as err:  # noqa: BLE001 — 웨이백 일시 오류는 다음 확인 때 재시도(최대 3회)
            failed[ts] = failed.get(ts, 0) + 1
            log(f"  RoMonitor 사본 {ts} 실패({failed[ts]}회): {err}")
            time.sleep(4)
            continue
        for k, v in data.items():
            if v is None:
                continue
            t = iso_z(parse_ts(k))
            if t in points:
                if points[t] != v:
                    conflicts += 1
                continue  # 이미 있는 칸(직접 수집값 포함)은 옛 사본으로 덮지 않는다
            added += 1
            points[t] = num(v)
        done.add(ts)
        failed.pop(ts, None)
        fetched += 1
        time.sleep(2)

    ordered, excluded = split_romonitor_glitches(points)
    if changed:
        write_json(path, {
            "format": "romonitor-v1",
            "source": payload.get("source", "RoMonitor Stats · Roblox Global CCUs"),
            "sourceUrl": payload.get("sourceUrl", "https://romonitorstats.com/api/v1/charts/get?name=platform-ccus&timeslice=half-hourly"),
            "archive": "https://web.archive.org",
            "stepSeconds": 1800,
            "updatedAt": iso_z(now),
            "lastLiveFetch": last_live,
            "policy": "RoMonitor 플랫폼 차트 원값: 사용자 PC 가 매시간 직접 수집(최근 2일, 꺼져 있던 동안은 최대 14일 소급) + 웨이백 보관 사본(2023-04~). 0 이하·앞뒤 1시간 최저값의 25% 미만 칸은 측정 오류로 보고 excluded 로 뺌. 보간·보정 없음. 시각은 UTC.",
            "captures": sorted(done),
            "failedCaptures": failed,
            "excluded": excluded,
            "points": [[t, v] for t, v in ordered],
        })
    if wayback:
        status["lastRomonitorCheck"] = iso_z(now) if check_wayback else last
    last_point = ordered[-1][0] if ordered else None
    status["romonitor"] = {"liveAdded": live_added, "liveChanged": live_changed, "lastLiveFetch": last_live,
                           "newCaptures": fetched, "addedPoints": added, "conflicts": conflicts,
                           "points": len(ordered), "excluded": len(excluded), "lastPoint": last_point,
                           "pendingCaptures": max(0, len(todo) - ROMONITOR_MAX_FETCH)}
    # 최신 칸이 너무 오래되면(PC 수집이 멈춤) 하루 한 번만 오류로 올린다 — 매시간 실패 메일 방지
    if wayback and last_point and now - parse_ts(last_point) > timedelta(hours=ROMONITOR_STALE_HOURS) and now.hour == 0:
        status["errors"].append(f"RoMonitor 최신 칸이 {last_point} 에서 멈춤 — PC 수집기(RobloxObservatoryKick) 확인 필요")
    log(f"RoMonitor: 직접 +{live_added}(수정 {live_changed}), 새 사본 {fetched}건 +{added}, 충돌 {conflicts}, 마지막 {last_point}")
    return changed


# ── 2. 게임별 CCU ─────────────────────────────────────────────────────────────

def pad_meta(meta):
    """게임 메타를 [이름, 장르, 제작자, 등급, 최소연령] 길이로 맞춘다(옛 3칸 파일 호환)."""
    meta = list(meta or [])
    return (meta + [None] * META_LEN)[:META_LEN]


def load_history(path):
    """live_game_history.json → (행 목록, 게임 메타). 예전 행 형식과 compact-v1 둘 다 읽는다."""
    payload = read_json(path, {})
    rows, games = [], {}
    if payload.get("format") == "compact-v1":
        times, tiers = payload["times"], payload["tiers"]
        games = {k: list(v) for k, v in payload.get("games", {}).items()}
        for ti, uid, ccu, fav, tier_i in payload.get("rows", []):
            rows.append({"observedAt": times[ti], "universeId": uid, "ccu": ccu, "favorites": fav,
                         "tier": tiers[tier_i]})
        return rows, games
    for item in payload.get("data", []):
        uid = str(item.get("universeId", ""))
        if not uid.isdigit() or uid == "0" or not item.get("observedAt"):
            continue
        rows.append({"observedAt": iso_z(parse_ts(item["observedAt"])), "universeId": uid,
                     "ccu": num(item.get("avg")), "favorites": num(item.get("favorites")),
                     "tier": item.get("tier") or "all"})
        games[uid] = [item.get("name"), item.get("genre"), item.get("creator")]
    return rows, games


def save_history(path, rows, games, now):
    cutoff = now - timedelta(days=HISTORY_DAYS)
    kept = {}
    for r in rows:
        if parse_ts(r["observedAt"]) >= cutoff:
            kept[(r["universeId"], r["observedAt"])] = r
    ordered = sorted(kept.values(), key=lambda r: (r["observedAt"], int(r["universeId"])))
    times = sorted({r["observedAt"] for r in ordered})
    t_index = {t: i for i, t in enumerate(times)}
    used = {r["universeId"] for r in ordered}
    write_json(path, {
        "format": "compact-v1",
        "updatedAt": iso_z(now),
        "retentionDays": HISTORY_DAYS,
        "policy": "Roblox 공개 게임 API의 실제 playing 관측값만 보존. DAU 환산·보간·0 대체 없음.",
        "sourceUrl": GAMES_API,
        "columns": ["timeIndex", "universeId", "playing", "favorites", "tierIndex"],
        "tiers": TIER_ORDER,
        "times": times,
        "games": {uid: pad_meta(games.get(uid)) for uid in sorted(used, key=int)},
        "rows": [[t_index[r["observedAt"]], r["universeId"], r["ccu"], r["favorites"], TIER_ORDER.index(r["tier"])]
                 for r in ordered],
    })
    return len(ordered)


def ranked_universe_ids(data_dir, history_rows):
    """최근 실측 playing 기준 순위. 아직 실측이 없는 게임은 과거 일별 아카이브 평균으로 뒤에 붙인다."""
    archive = read_json(os.path.join(data_dir, "game_history_daily.json"), [])
    archive_latest = {}
    for row in archive:
        uid = str(row.get("universeId", ""))
        if not uid.isdigit() or uid == "0":
            continue
        prev = archive_latest.get(uid)
        if prev is None or str(row.get("date")) > str(prev.get("date")):
            archive_latest[uid] = row
    live_latest = {}
    for r in history_rows:
        prev = live_latest.get(r["universeId"])
        if prev is None or r["observedAt"] > prev["observedAt"]:
            live_latest[r["universeId"]] = r
    ids = set(archive_latest) | set(live_latest)

    def score(uid):
        if uid in live_latest and live_latest[uid]["ccu"] is not None:
            return (1, live_latest[uid]["ccu"])
        avg = archive_latest.get(uid, {}).get("avg")
        return (0, avg if avg is not None else -1)

    return sorted(ids, key=lambda u: score(u), reverse=True)


def in_event_window(now):
    """토요일 이벤트 시간대(UTC 14:30~17:00) — 주요 게임의 어드민 어뷰즈가 여기 몰려 있어
    1시간 간격으로는 피크를 놓친다(2026-09-19: 우리 12.1M, 실제 14.3M)."""
    if now.weekday() != 5:
        return False
    return (now.hour == 14 and now.minute >= 30) or (15 <= now.hour < 17)


def due_tiers(status_prev, now, force_tier):
    if force_tier:
        return [force_tier]
    last = status_prev.get("lastSuccess", {})

    def done_since(tier, block_start):
        # 상위 등급 성공은 하위 등급을 포함한다 (all ⊃ top1000 ⊃ top100).
        for t in TIER_ORDER[TIER_ORDER.index(tier):]:
            if last.get(t) and parse_ts(last[t]) >= block_start:
                return True
        return False

    hour_start = now.replace(minute=0, second=0, microsecond=0)
    block4_start = hour_start.replace(hour=hour_start.hour - hour_start.hour % 4)
    day_start = hour_start.replace(hour=0)
    if not done_since("all", day_start):
        return ["all"]
    if not done_since("top1000", block4_start):
        return ["top1000"]
    # 토요일 이벤트 시간대만 15분 블록으로 쪼갠다.
    top_start = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0) if in_event_window(now) else hour_start
    if not done_since("top100", top_start):
        return ["top100"]
    return []


# 응답 헤더는 분당 300회를 알려주지만, 0.25초 간격으로는 숨은 제한에 7번 걸렸다(2026-09-17 전체 수집).
def fetch_games(ids, observed_at, tier, status, delay=1.0, batch_size=50):
    rows, failed_batches = [], 0
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        try:
            payload, headers = http_json(f"{GAMES_API}?universeIds={','.join(batch)}")
            status["rateLimit"] = {k: headers.get(k) for k in
                                   ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")}
        except Exception as err:  # noqa: BLE001
            failed_batches += 1
            status["errors"].append(f"게임 API 배치 {i // batch_size + 1} 실패: {err}")
            continue
        for g in payload.get("data", []):
            # 삭제·비공개 게임은 id 0 "[TITLE UNAVAILABLE]", playing 0 으로 온다 — 관측값이 아니므로 버린다.
            if not g.get("id"):
                continue
            creator = g.get("creator") or {}
            rows.append({
                "observedAt": observed_at, "universeId": str(g["id"]), "ccu": num(g.get("playing")),
                "favorites": num(g.get("favoritedCount")), "visits": num(g.get("visits")),
                "maxPlayers": g.get("maxPlayers"), "updated": g.get("updated"), "tier": tier,
                "name": g.get("name"), "genre": g.get("genre"), "genre_l1": g.get("genre_l1"),
                "genre_l2": g.get("genre_l2"), "creatorId": creator.get("id"),
                "creatorName": creator.get("name"), "creatorType": creator.get("type"),
            })
        if i + batch_size < len(ids):
            time.sleep(delay)
    return rows, failed_batches


ARCHIVE_FIELDS = ["observed_at", "universe_id", "playing", "visits", "favorites", "max_players", "updated",
                  "tier", "name", "genre", "genre_l1", "genre_l2", "creator_id", "creator_name", "creator_type"]


def append_csv_gz(path, fields, rows):
    """월별 gzip CSV 에 gzip 멤버를 이어 붙인다(대시보드는 읽지 않는 영구 보관용)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    if not os.path.exists(path):
        writer.writerow(fields)
    for row in rows:
        writer.writerow(row)
    with open(path, "ab") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))


def append_archive(root, rows, now):
    """게임 관측 원시 보관."""
    append_csv_gz(os.path.join(root, "archive", "live_games", now.strftime("%Y-%m") + ".csv.gz"),
                  ARCHIVE_FIELDS,
                  [[r["observedAt"], r["universeId"], r["ccu"], r["visits"], r["favorites"], r["maxPlayers"],
                    r["updated"], r["tier"], r["name"], r["genre"], r["genre_l1"], r["genre_l2"],
                    r["creatorId"], r["creatorName"], r["creatorType"]] for r in rows])


def collect_games(root, repo_root, data_dir, status, tier, now):
    status["tiers"] = [tier]
    history_path = os.path.join(data_dir, "live_game_history.json")
    history_rows, games = load_history(history_path)
    ranked = ranked_universe_ids(data_dir, history_rows)
    size = TIER_SIZE[tier]
    selected = ranked[:size] if size else ranked
    observed_at = now.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 탐색 보드(인기·트렌딩 등)에 지금 떠 있는 게임과 워치리스트는 순위와 무관하게 매시간 관측한다.
    # 보드 게임 상당수가 CCU 101~1,000위라 top100 만으로는 4시간마다밖에 안 잡힌다
    # (2026-09-18 확인: 폐기한 30분 수집기의 보드 게임 414개 중 97개만 top100).
    labels = fetch_sorts(root, data_dir, status, observed_at)
    watch = load_watchlist(repo_root, status)
    selected = list(dict.fromkeys(list(selected) + list(labels) + watch))
    log(f"게임: {tier} {len(selected)}개(보드 {len(labels)}개·워치리스트 {len(watch)}개 포함) 수집 시작 ({observed_at})")

    fetched, failed = fetch_games(selected, observed_at, tier, status)
    if not fetched:
        raise RuntimeError(f"게임 API 응답 0행 ({tier}, 실패 배치 {failed})")
    for r in fetched:
        history_rows.append({k: r[k] for k in ("observedAt", "universeId", "ccu", "favorites", "tier")})
        old = pad_meta(games.get(r["universeId"]))
        mat, min_age = labels.get(r["universeId"], (old[3], old[4]))
        games[r["universeId"]] = [r["name"], r["genre_l1"] or r["genre"], r["creatorName"], mat, min_age]
    # 차트에 없어 등급을 모르는 게임은 관측된 순서대로 조금씩 채운다.
    unknown = [uid for uid in (r["universeId"] for r in fetched) if not pad_meta(games.get(uid))[3]]
    for uid, (mat, min_age) in fetch_age_ratings(unknown, status).items():
        meta = pad_meta(games.get(uid))
        games[uid] = meta[:3] + [mat, min_age]
    status["ageLookups"] = {"unknown": len(unknown), "checked": min(len(unknown), AGE_LOOKUPS_PER_RUN)}
    kept = save_history(history_path, history_rows, games, now)
    append_archive(root, fetched, now)

    status["games"] = {"tier": tier, "selected": len(selected), "returned": len(fetched),
                       "failedBatches": failed, "observedAt": observed_at, "historyRows": kept}
    if failed == 0:
        status["lastSuccess"][tier] = observed_at
    log(f"게임: {len(fetched)}/{len(selected)}개 응답, 실패 배치 {failed}, 보존 행 {kept}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True, help="public/data 와 archive/ 를 담는 루트")
    ap.add_argument("--force-tier", choices=TIER_ORDER)
    ap.add_argument("--no-games", action="store_true")
    ap.add_argument("--no-platform", action="store_true")
    ap.add_argument("--romonitor-live", action="store_true", help="RoMonitor 직접 수집(PC 전용 — Actions 러너는 403)")
    args = ap.parse_args()

    root = os.path.abspath(args.data_root)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 코드 브랜치(워치리스트 위치)
    data_dir = os.path.join(root, "public", "data")
    status_path = os.path.join(data_dir, "collection_status.json")
    status_prev = read_json(status_path, {})
    now = utc_now()
    status = {"ranAt": iso_z(now), "errors": [], "lastSuccess": dict(status_prev.get("lastSuccess", {})),
              "policy": "매시간 상위 100(토 14:30~17:00 UTC 는 15분), UTC 4시간 블록마다 상위 1,000, UTC 하루 1회 추적 목록 전체. 실제 공개 API 관측값만 저장."}
    saved = False

    tiers = [] if args.no_games else due_tiers(status_prev, now, args.force_tier)
    last_platform = status_prev.get("lastPlatformSuccess")
    fresh_window = timedelta(minutes=12) if in_event_window(now) else timedelta(minutes=50)
    platform_fresh = last_platform and now - parse_ts(last_platform) < fresh_window
    if not tiers and platform_fresh and not args.force_tier:
        # 예약이 10분마다 걸려 있으므로, 앞 회차가 이미 처리한 시간대면 커밋·배포 없이 끝낸다.
        log("이번 시간대 수집 이미 완료 — 변경 없음")
        return 0

    if not args.no_platform:
        try:
            collect_platform(data_dir, status, status_prev.get("lastArchiveFetch"))
            saved = True
        except Exception as err:  # noqa: BLE001
            status["errors"].append(f"플랫폼 병합 실패(기존 파일 유지): {err}")
            log(f"플랫폼 병합 실패: {err}")

    try:
        saved = collect_romonitor(data_dir, status, status_prev, now, live=args.romonitor_live) or saved
    except Exception as err:  # noqa: BLE001 — 웨이백 장애는 다음 확인 때 다시 시도, 수집 실패로 치지 않음
        status["lastRomonitorCheck"] = status_prev.get("lastRomonitorCheck")
        status["romonitorError"] = str(err)
        log(f"RoMonitor 웨이백 확인 실패: {err}")

    if tiers:
        try:
            saved = collect_games(root, repo_root, data_dir, status, tiers[0], now) or saved
        except Exception as err:  # noqa: BLE001
            status["errors"].append(f"게임 수집 실패(기존 파일 유지): {err}")
            log(f"게임 수집 실패: {err}")
    else:
        status["tiers"] = []

    status["lastPlatformSuccess"] = status["ranAt"] if "platform" in status else last_platform
    write_json(status_path, status)
    for e in status["errors"]:
        log(f"⚠️ {e}")
    return 0 if saved else 2


if __name__ == "__main__":
    sys.exit(main())
