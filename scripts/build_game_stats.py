#!/usr/bin/env python3
"""게임별 영구 통계 만들기 — 원시 관측(30일 보존) → 일단위 집계(영구) + 요약 색인.

왜 필요한가: live_game_history.json 은 보존 기간이 짧아(30일) 역대 최고·장기 추이를 못 본다.
그래서 매 회차마다 관측을 UTC 날짜로 접어 games/{universeId}.json 에 누적하고,
목록 화면용 요약을 games/index.json 으로 낸다.

입력(모두 site-data/public/data 기준)
  live_game_history.json   우리 수집기의 원시 관측(1시간 간격, 토요일 이벤트 시간대는 15분)
  games_wayback.json       웨이백에 남은 Roblox 공식 탐색 API 사본으로 만든 과거 일단위 값(2024-02~)
  game_history_daily.json  옛 아카이브(early_shift_daily)
  platform_romonitor.json  플랫폼 동접 — 같은 날 비중 계산용
출력
  games/{id}.json          일별 [날짜, 피크, 평균, 관측수, 출처] + 기록
  games/index.json         게임별 요약(현재·기간별 피크/평균·역대 최고·변화율)

원칙: 관측값만 저장한다. 보간·0 대체·DAU 환산 없음. 출처가 다른 값은 섞지 않고 우선순위로 고른다.
"""
import json, os, sys, math
from collections import defaultdict

MIN_PEAK = 5000          # 이 값 미만만 관측된 게임은 개별 파일을 만들지 않는다(파일 수 관리)
MIN_INDEX_PEAK = 20000   # 목록 화면 색인에 넣는 기준(파일 크기 관리)
SRC_RANK = {"live": 3, "wayback": 2, "legacy": 1}


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return default


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))


def day_of(ts):
    return str(ts)[:10]


def add(acc, uid, date, peak, avg, n, src):
    if peak is None and avg is None:
        return
    cur = acc[uid].get(date)
    cand = {"peak": peak, "avg": avg, "n": n or 0, "src": src}
    if cur is None:
        acc[uid][date] = cand
        return
    # 같은 날 여러 출처: 피크는 더 큰 값, 평균·관측수는 관측이 촘촘한 쪽을 쓴다.
    if (peak or 0) > (cur["peak"] or 0):
        cur["peak"] = peak
    better = (n or 0, SRC_RANK.get(src, 0)) > (cur["n"], SRC_RANK.get(cur["src"], 0))
    if better and avg is not None:
        cur["avg"], cur["n"], cur["src"] = avg, n or 0, src


def load_live(data_dir, acc, meta):
    payload = read_json(os.path.join(data_dir, "live_game_history.json"), {})
    if payload.get("format") != "compact-v1":
        return 0
    times, games = payload["times"], payload.get("games", {})
    per = defaultdict(lambda: defaultdict(list))
    for ti, uid, ccu, *_rest in payload.get("rows", []):
        if ccu is None:
            continue
        per[str(uid)][day_of(times[ti])].append(ccu)
    for uid, days in per.items():
        for date, vals in days.items():
            add(acc, uid, date, max(vals), sum(vals) / len(vals), len(vals), "live")
        m = games.get(uid) or []
        if m:
            meta[uid] = {"name": m[0], "genre": m[1], "creator": m[2],
                         "maturity": m[3] if len(m) > 3 else None}
    return len(per)


def load_wayback(data_dir, acc, meta):
    payload = read_json(os.path.join(data_dir, "games_wayback.json"), {})
    for uid, rows in (payload.get("games") or {}).items():
        for date, peak, avg, n in rows:
            add(acc, uid, date, peak, avg, n, "wayback")
        name = (payload.get("names") or {}).get(uid)
        if name and uid not in meta:
            meta[uid] = {"name": name, "genre": None, "creator": None, "maturity": None}
    return len(payload.get("games") or {})


def load_legacy(data_dir, acc, meta):
    rows = read_json(os.path.join(data_dir, "game_history_daily.json"), [])
    rows = rows if isinstance(rows, list) else rows.get("data", [])
    seen = set()
    for r in rows:
        uid = str(r.get("universeId") or "")
        if not uid.isdigit() or uid == "0":
            continue
        seen.add(uid)
        add(acc, uid, day_of(r.get("date")), r.get("peak"), r.get("avg"), 0, "legacy")
        if uid not in meta and r.get("name") and not str(r["name"]).startswith("게임 "):
            meta[uid] = {"name": r["name"], "genre": r.get("genre"), "creator": None, "maturity": None}
    return len(seen)


def platform_by_day(data_dir):
    """RoMonitor 30분 값 → UTC 날짜별 피크·평균(게임 비중 계산의 분모)."""
    payload = read_json(os.path.join(data_dir, "platform_romonitor.json"), {})
    per = defaultdict(list)
    for ts, v in payload.get("points", []):
        if v:
            per[day_of(ts)].append(v)
    return {d: {"peak": max(v), "avg": sum(v) / len(v), "n": len(v)} for d, v in per.items()}


def rnd(v):
    return None if v is None else int(round(v))


def pct(a, b):
    if not a or not b:
        return None
    return round((a / b - 1) * 100, 1)


def window(days, dates, k):
    """최근 k일(관측이 있는 날짜 기준이 아니라 달력 기준)의 피크·평균."""
    if not dates:
        return None, None
    end = dates[-1]
    y, m, d = int(end[:4]), int(end[5:7]), int(end[8:10])
    import datetime as dt
    start = (dt.date(y, m, d) - dt.timedelta(days=k - 1)).isoformat()
    sel = [days[x] for x in dates if x >= start]
    peaks = [s["peak"] for s in sel if s["peak"] is not None]
    avgs = [(s["avg"], s["n"] or 1) for s in sel if s["avg"] is not None]
    peak = max(peaks) if peaks else None
    avg = (sum(a * n for a, n in avgs) / sum(n for _, n in avgs)) if avgs else None
    return peak, avg


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    data_dir = os.path.join(root, "public", "data")
    out_dir = os.path.join(data_dir, "games")
    acc, meta = defaultdict(dict), {}
    n_live = load_live(data_dir, acc, meta)
    n_way = load_wayback(data_dir, acc, meta)
    n_leg = load_legacy(data_dir, acc, meta)
    # 이전 회차가 만든 집계를 합쳐 보존 기간 밖의 과거를 지킨다.
    n_prev = 0
    if os.path.isdir(out_dir):
        for fn in os.listdir(out_dir):
            if fn == "index.json" or not fn.endswith(".json"):
                continue
            uid = fn[:-5]
            prev = read_json(os.path.join(out_dir, fn), {})
            for date, peak, avg, n, src in prev.get("days", []):
                add(acc, uid, date, peak, avg, n, src)
            if uid not in meta and prev.get("name"):
                meta[uid] = {"name": prev["name"], "genre": prev.get("genre"),
                             "creator": prev.get("creator"), "maturity": prev.get("maturity")}
            n_prev += 1
    plat = platform_by_day(data_dir)

    index, written = [], 0
    for uid, days in acc.items():
        dates = sorted(days)
        peaks = [(days[d]["peak"], d) for d in dates if days[d]["peak"] is not None]
        if not peaks:
            continue
        all_peak, all_peak_date = max(peaks)
        if all_peak < MIN_PEAK:
            continue
        m = meta.get(uid) or {}
        rows = [[d, days[d]["peak"], round(days[d]["avg"], 1) if days[d]["avg"] is not None else None,
                 days[d]["n"], days[d]["src"]] for d in dates]
        share = None
        p = plat.get(all_peak_date)
        if p and p["peak"]:
            share = round(all_peak / p["peak"] * 100, 1)
        last = dates[-1]
        d1p, d1a = window(days, dates, 1)
        d7p, d7a = window(days, dates, 7)
        d30p, d30a = window(days, dates, 30)
        prev7 = [days[d]["avg"] for d in dates if d < last][-7:]
        payload = {
            "id": uid, "name": m.get("name") or f"게임 {uid}", "genre": m.get("genre"),
            "creator": m.get("creator"), "maturity": m.get("maturity"),
            "columns": ["date", "peak", "avg", "observations", "source"],
            "policy": "UTC 날짜 기준. 관측값만 저장하고 보간하지 않음. source=live(우리 수집)·wayback(웨이백 사본)·legacy(옛 아카이브)",
            "records": {"peak": all_peak, "peakDate": all_peak_date, "peakShareOfPlatform": share,
                        "firstSeen": dates[0], "lastSeen": last, "daysTracked": len(dates)},
            "days": rows,
        }
        write_json(os.path.join(out_dir, f"{uid}.json"), payload)
        written += 1
        if all_peak < MIN_INDEX_PEAK:
            continue
        index.append({
            "id": uid, "name": payload["name"], "genre": m.get("genre"), "creator": m.get("creator"),
            "last": last, "lastPeak": days[last]["peak"], "lastAvg": rnd(days[last]["avg"]),
            "d1Peak": d1p, "d1Avg": rnd(d1a), "d7Peak": d7p, "d7Avg": rnd(d7a), "d30Peak": d30p, "d30Avg": rnd(d30a),
            "allPeak": all_peak, "allPeakDate": all_peak_date, "peakShare": share,
            "chg1d": pct(d1a, prev7[-1] if prev7 else None),
            "chg7d": pct(d7a, (sum(x for x in prev7 if x is not None) / max(1, len([x for x in prev7 if x is not None]))) if prev7 else None),
            "days": len(dates), "firstSeen": dates[0],
        })

    index.sort(key=lambda r: (r["d1Avg"] or r["lastAvg"] or 0), reverse=True)
    for i, r in enumerate(index, 1):
        r["rank"] = i
    write_json(os.path.join(out_dir, "index.json"), {
        "updatedAt": max((r["last"] for r in index), default=None),
        "policy": "UTC 날짜 기준 일단위 집계. 순위는 우리가 추적하는 게임 안에서의 순위임.",
        "minPeakKept": MIN_PEAK, "minPeakIndexed": MIN_INDEX_PEAK,
        "count": len(index),
        "games": index,
    })
    print(f"게임 통계: live {n_live} / wayback {n_way} / legacy {n_leg} / 기존 {n_prev} → 파일 {written}개, 색인 {len(index)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
