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
USER_AGENT = "roblox-usage-observatory/1.0 (+https://github.com/smy7662-dotcom/roblox-usage-observatory)"

TIER_ORDER = ["top100", "top1000", "all"]
TIER_SIZE = {"top100": 100, "top1000": 1000, "all": None}
HISTORY_DAYS = 30

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


# ── 1. 플랫폼 CCU ─────────────────────────────────────────────────────────────

def collect_platform(data_dir, status):
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


# ── 2. 게임별 CCU ─────────────────────────────────────────────────────────────

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
        "games": {uid: games.get(uid, [None, None, None]) for uid in sorted(used, key=int)},
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
    if not done_since("top100", hour_start):
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


def append_archive(root, rows, now):
    """원시 관측 영구 보관: 월별 gzip CSV 에 gzip 멤버를 이어 붙인다(대시보드는 읽지 않음)."""
    path = os.path.join(root, "archive", "live_games", now.strftime("%Y-%m") + ".csv.gz")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    if not os.path.exists(path):
        writer.writerow(ARCHIVE_FIELDS)
    for r in rows:
        writer.writerow([r["observedAt"], r["universeId"], r["ccu"], r["visits"], r["favorites"], r["maxPlayers"],
                         r["updated"], r["tier"], r["name"], r["genre"], r["genre_l1"], r["genre_l2"],
                         r["creatorId"], r["creatorName"], r["creatorType"]])
    with open(path, "ab") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))


def collect_games(root, data_dir, status, tier, now):
    status["tiers"] = [tier]
    history_path = os.path.join(data_dir, "live_game_history.json")
    history_rows, games = load_history(history_path)
    ranked = ranked_universe_ids(data_dir, history_rows)
    size = TIER_SIZE[tier]
    selected = ranked[:size] if size else ranked
    observed_at = now.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    log(f"게임: {tier} {len(selected)}개 수집 시작 ({observed_at})")

    fetched, failed = fetch_games(selected, observed_at, tier, status)
    if not fetched:
        raise RuntimeError(f"게임 API 응답 0행 ({tier}, 실패 배치 {failed})")
    for r in fetched:
        history_rows.append({k: r[k] for k in ("observedAt", "universeId", "ccu", "favorites", "tier")})
        games[r["universeId"]] = [r["name"], r["genre_l1"] or r["genre"], r["creatorName"]]
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
    args = ap.parse_args()

    root = os.path.abspath(args.data_root)
    data_dir = os.path.join(root, "public", "data")
    status_path = os.path.join(data_dir, "collection_status.json")
    status_prev = read_json(status_path, {})
    now = utc_now()
    status = {"ranAt": iso_z(now), "errors": [], "lastSuccess": dict(status_prev.get("lastSuccess", {})),
              "policy": "매시간 상위 100, UTC 4시간 블록마다 상위 1,000, UTC 하루 1회 추적 목록 전체. 실제 공개 API 관측값만 저장."}
    saved = False

    tiers = [] if args.no_games else due_tiers(status_prev, now, args.force_tier)
    last_platform = status_prev.get("lastPlatformSuccess")
    platform_fresh = last_platform and now - parse_ts(last_platform) < timedelta(minutes=40)
    if not tiers and platform_fresh and not args.force_tier:
        # 예약이 30분 간격 두 번이라, 앞 회차가 이미 처리한 시간대면 커밋·배포 없이 끝낸다.
        log("이번 시간대 수집 이미 완료 — 변경 없음")
        return 0

    if not args.no_platform:
        try:
            collect_platform(data_dir, status)
            saved = True
        except Exception as err:  # noqa: BLE001
            status["errors"].append(f"플랫폼 병합 실패(기존 파일 유지): {err}")
            log(f"플랫폼 병합 실패: {err}")

    if tiers:
        try:
            saved = collect_games(root, data_dir, status, tiers[0], now) or saved
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
