#!/usr/bin/env python3
"""외부에서 따로 모은 관측 스냅샷(jsonl)을 관측소 데이터로 합친다 — 1회성 백필용.

만든 이유: 2026-09-16~18 에 Cowork 세션에서 30분 간격으로 상위 차트 + 성인향 후보 게임의
playing 을 따로 모아둔 파일이 있었다. 같은 공개 API 실측값이라 버리지 않고 합친다.

입력 jsonl 한 줄 = {"ts","universeId","name","playing","visits","favorites","genre","genre_l1",
                   "maturity","in_watchlist"}
합치는 곳:
  public/data/live_game_history.json (compact-v1)  — 중복(게임·시각)은 건너뛴다
  archive/live_games/YYYY-MM.csv.gz               — 원시 보관(tier 열에 출처 표시)

사용: python scripts/import_snapshots.py <데이터 루트> <jsonl...> [--tier top100]
"""

import argparse
import csv
import glob
import gzip
import io
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import ARCHIVE_FIELDS, load_history, pad_meta, save_history  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_root")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--tier", default="top100", help="live_game_history 의 tier 칸(관측 간격 표시용)")
    ap.add_argument("--source-tag", default="cowork_30min", help="아카이브 CSV 에 남길 출처")
    args = ap.parse_args()

    root = os.path.abspath(args.data_root)
    data_dir = os.path.join(root, "public", "data")
    history_path = os.path.join(data_dir, "live_game_history.json")
    rows, games = load_history(history_path)
    have = {(r["universeId"], r["observedAt"]) for r in rows}

    paths = [p for pattern in args.files for p in sorted(glob.glob(pattern))]
    added, skipped, dropped = 0, 0, 0
    archive_rows = []
    for path in paths:
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            uid = str(r.get("universeId") or "")
            # 삭제·비공개 게임은 id 0 / playing 0 으로 온다 — 관측값이 아니므로 버린다.
            if not uid.isdigit() or uid == "0" or r.get("playing") is None:
                dropped += 1
                continue
            ts = datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)
            observed_at = ts.strftime("%Y-%m-%dT%H:%M:00Z")  # 초 단위는 버려 시각을 맞춘다
            key = (uid, observed_at)
            if key in have:
                skipped += 1
                continue
            have.add(key)
            rows.append({"observedAt": observed_at, "universeId": uid, "ccu": r["playing"],
                         "favorites": r.get("favorites"), "tier": args.tier})
            old = pad_meta(games.get(uid))
            games[uid] = [r.get("name") or old[0], r.get("genre_l1") or r.get("genre") or old[1],
                          old[2], r.get("maturity") or old[3], old[4]]
            archive_rows.append([observed_at, uid, r["playing"], r.get("visits"), r.get("favorites"),
                                 None, None, args.source_tag, r.get("name"), r.get("genre"),
                                 r.get("genre_l1"), None, None, None, None])
            added += 1

    if not added:
        print(f"추가할 행 없음(중복 {skipped}, 버림 {dropped})")
        return 0

    now = datetime.now(timezone.utc)
    kept = save_history(history_path, rows, games, now)

    months = sorted({r[0][:7] for r in archive_rows})
    for month in months:
        path = os.path.join(root, "archive", "live_games", month + ".csv.gz")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        if not os.path.exists(path):
            writer.writerow(ARCHIVE_FIELDS)
        for row in archive_rows:
            if row[0][:7] == month:
                writer.writerow(row)
        with open(path, "ab") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            gz.write(buf.getvalue().encode("utf-8"))

    print(f"백필: 추가 {added}행, 중복 {skipped}, 버림 {dropped} → 보존 {kept}행, 아카이브 {months}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
