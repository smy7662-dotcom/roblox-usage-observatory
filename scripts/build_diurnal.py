#!/usr/bin/env python3
"""게임별 시간대 프로파일 — 관측된 CCU 를 UTC 시각별로 묶어 하루 곡선을 만든다.

왜:
  같은 게임을 매시간 관측하면 하루 곡선의 모양이 게임마다 다르다. 미주 저녁에 피크가 서는
  게임과 아시아 저녁에 피크가 서는 게임이 갈린다. 이건 "어느 시간대 사람들이 하는가"를 재는
  것이고, 지역·연령을 직접 재는 값이 아니다(같은 시간대 안에 아이와 성인이 섞여 있다).

무엇을 쓰나:
  public/data/live_game_history.json (compact-v1, 최근 30일, 실제 관측값만).
  DAU 환산·보간·0 대체 없음. 관측이 없는 시간대는 그냥 빈칸으로 둔다.

산출:
  public/data/game_diurnal.json   게임별 24시간 곡선 + 구간 지표
  public/data/diurnal_index.json  날짜별, 시간대 성향 그룹의 CCU 합 (플랫폼 추세용)
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

# UTC 기준 구간. 한국 시간 = UTC+9, 미국 동부(서머타임) = UTC-4.
WINDOWS = {
    "asiaDay": (9, 13),    # ⚠️ 키 이름은 호환 때문에 유지 — 실제로는 아시아 "저녁"(한국 18~22시, 자카르타 16~20시)
    "euEve": (15, 19),     # 서유럽 17~21시(CEST)
    "usEve": (22, 26),     # 미 동부 18~22시 (26 = 다음날 2시)
    "usLate": (4, 8),      # 미 동부 0~4시 = 한국 13~17시·자카르타 11~15시(아시아 평일 낮, 학교 수업 시간대와 겹침)
}
MIN_HOURS = 8    # 최소 서로 다른 UTC 시각 수(수집 초기라 낮게 시작, observedHours 로 두께를 같이 보여준다)
MIN_DAYS = 1     # 최소 관측 일수


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


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def window_mean(hourly, lo, hi):
    """hourly[h] = 그 UTC 시각의 평균. 구간은 24시를 넘어가면 자정을 돌아서 잇는다."""
    hours = [h % 24 for h in range(lo, hi)]
    return mean([hourly[h] for h in hours if hourly[h] is not None])


def profile(rows):
    """rows = [(datetime, ccu)] → 24시간 곡선과 구간 지표."""
    buckets = [[] for _ in range(24)]
    days = set()
    for ts, ccu in rows:
        buckets[ts.hour].append(ccu)
        days.add(ts.date())
    hourly = [mean(b) for b in buckets]
    observed = [h for h in range(24) if hourly[h] is not None]
    if len(observed) < MIN_HOURS or len(days) < MIN_DAYS:
        return None
    day_mean = mean([hourly[h] for h in observed])
    if not day_mean:
        return None
    out = {
        "hours": [None if v is None else round(v) for v in hourly],
        "dayMean": round(day_mean),
        "peakHourUtc": max(observed, key=lambda h: hourly[h]),
        "observedHours": len(observed),
        "observedDays": len(days),
        "samples": sum(len(b) for b in buckets),
    }
    for key, (lo, hi) in WINDOWS.items():
        w = window_mean(hourly, lo, hi)
        out[key] = None if w is None else round(w / day_mean, 3)  # 하루 평균 대비 배수
    if out["asiaDay"] and out["usEve"]:
        out["usOverAsia"] = round(out["usEve"] / out["asiaDay"], 3)
    else:
        out["usOverAsia"] = None
    if out["usEve"] and out["usLate"]:
        out["lateOverUsEve"] = round(out["usLate"] / out["usEve"], 3)
    else:
        out["lateOverUsEve"] = None
    return out


def relative(p, baseline):
    """전체 평균 곡선 대비 배수. 로블록스 전체가 유럽 저녁에 피크라서, 게임끼리 비교하려면
    '전체보다 이 시간대에 얼마나 더 쏠렸나'로 봐야 한다."""
    out = {}
    for key in WINDOWS:
        v, b = p.get(key), baseline.get(key)
        out["rel_" + key] = round(v / b, 3) if v and b else None
    return out


def lean(p):
    """전체 대비 배수가 가장 큰 구간. 판정이 아니라 관측 곡선이 어디로 쏠렸는지 라벨."""
    pairs = [(p.get("rel_" + k), k) for k in ("asiaDay", "euEve", "usEve") if p.get("rel_" + k)]
    if not pairs:
        return None
    return max(pairs)[1]


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    data_dir = os.path.join(root, "public", "data")
    payload = read_json(os.path.join(data_dir, "live_game_history.json"), {})
    if payload.get("format") != "compact-v1":
        print("live_game_history.json 이 compact-v1 이 아님 — 건너뜀")
        return 0

    times = [datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
             for t in payload["times"]]
    games_meta = payload.get("games", {})
    series = {}
    for ti, uid, ccu, _fav, _tier in payload.get("rows", []):
        if ccu is None:
            continue
        series.setdefault(str(uid), []).append((times[ti], ccu))

    profiles = {uid: p for uid, rows in series.items() if (p := profile(rows))}

    # 기준선 = 프로파일이 나온 게임들의 시각별 CCU 합. 같은 게임 집합이라 시각끼리 비교된다.
    total_hourly = []
    for h in range(24):
        vals = [p["hours"][h] for p in profiles.values() if p["hours"][h] is not None]
        total_hourly.append(sum(vals) if vals else None)
    total_mean = mean([v for v in total_hourly if v is not None]) or 1
    baseline = {k: (window_mean(total_hourly, lo, hi) or 0) / total_mean for k, (lo, hi) in WINDOWS.items()}

    games, index_rows = [], {}
    for uid, p in profiles.items():
        rows = series[uid]
        p.update(relative(p, baseline))
        meta = (list(games_meta.get(uid, [])) + [None] * 5)[:5]
        p.update({"universeId": uid, "name": meta[0], "genre": meta[1], "creator": meta[2],
                  "maturity": meta[3], "minAge": meta[4], "lean": lean(p)})
        games.append(p)
        # 날짜별 성향 그룹 합계: 그 게임의 30일 프로파일로 분류한 뒤, 날짜별 평균 CCU 를 더한다.
        by_day = {}
        for ts, ccu in rows:
            by_day.setdefault(ts.date().isoformat(), []).append(ccu)
        for day, values in by_day.items():
            slot = index_rows.setdefault(day, {"date": day, "asiaDay": 0, "euEve": 0, "usEve": 0,
                                               "unknown": 0, "games": 0})
            slot[p["lean"] or "unknown"] += mean(values)
            slot["games"] += 1

    games.sort(key=lambda g: -(g["dayMean"] or 0))
    write_json(os.path.join(data_dir, "game_diurnal.json"), {
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "public/data/live_game_history.json (Roblox 공개 게임 API 실측)",
        "policy": "관측된 playing 값을 UTC 시각별로 평균한 값. 보간·DAU 환산 없음. 관측 없는 시각은 null.",
        "baseline": {k: round(v, 3) for k, v in baseline.items()},
        "windowsUtc": {k: list(v) for k, v in WINDOWS.items()},
        "minHours": MIN_HOURS, "minDays": MIN_DAYS,
        "note": "구간 값은 '하루 평균 대비 배수'. 시간대 성향이지 지역·연령 측정이 아님.",
        "games": games,
    })
    rows = [{k: (round(v) if isinstance(v, float) else v) for k, v in r.items()}
            for r in sorted(index_rows.values(), key=lambda r: r["date"])]
    write_json(os.path.join(data_dir, "diurnal_index.json"), {
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": "게임별 30일 시간대 성향으로 분류한 뒤 날짜별 관측 CCU 평균을 합산. 추정·보간 없음.",
        "rows": rows,
    })
    print(f"시간대 프로파일: 게임 {len(games)}개, 날짜 {len(rows)}일")
    return 0


if __name__ == "__main__":
    sys.exit(main())
