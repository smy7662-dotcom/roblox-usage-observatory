#!/usr/bin/env python3
"""RoMonitor 플랫폼 CCU 직접 수집 — 사용자 PC 전용.

왜 PC 인가: RoMonitor 차트 API 는 GitHub Actions 러너에서 403(2026-09-19 07:05Z 실측),
            이 PC(가정용 회선)에서는 비로그인으로 200. 그래서 이 스크립트만 RoMonitor 를 직접 부른다.
무엇을 하나: 한 시간에 한 번, 요청 1건으로 최근 2일치 30분 값을 받아 data 브랜치의
            public/data/platform_romonitor.json 에 합치고 push 한다. 사이트 반영은 다음 매시 수집 회차의 배포 때.
PC 가 꺼져 있던 동안: 다음 실행 때 마지막 칸부터(최대 14일) 거슬러 받아 빈칸을 메운다(API 가 약 20일 보관).
⚠️ RoMonitor 약관은 스크립트 수집을 금지함 — 사용자가 알고 결정(2026-09-19). 차단되면 우회하지 않고 기록만.

Windows 작업 스케줄러(RobloxObservatoryKick, 15분마다) → kick.vbs → kick.cmd 가 kick_if_due.py 다음에 부른다.
"""

import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, ".data-branch")
STATE = os.path.join(ROOT, "logs", "romonitor_local.json")
LOG = os.path.join(ROOT, "logs", "kick.log")
REMOTE = "https://github.com/smy7662-dotcom/roblox-usage-observatory.git"
MIN_INTERVAL = timedelta(minutes=55)  # 한 시간에 한 번

sys.path.insert(0, os.path.join(HERE, ".."))
import collect  # noqa: E402  같은 병합·오류칸 규칙을 그대로 쓴다


def log(msg):
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} [RoMonitor] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def git(*args, check=True):
    out = subprocess.run(["git", "-C", DATA, *args], capture_output=True, text=True, encoding="utf-8", timeout=180)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} 실패: {(out.stderr or out.stdout).strip()[:300]}")
    return out


def sync():
    if not os.path.isdir(os.path.join(DATA, ".git")):
        subprocess.run(["git", "clone", "-q", "--depth", "5", "-b", "data", REMOTE, DATA], check=True, timeout=600)
    git("fetch", "-q", "--depth", "5", "origin", "data")
    git("reset", "-q", "--hard", "origin/data")  # 이 폴더는 이 스크립트 전용이라 로컬 변경을 남기지 않는다


def run_once(now):
    sync()
    status = {"errors": []}
    collect.collect_romonitor(os.path.join(DATA, "public", "data"), status, {}, now, live=True, wayback=False)
    info = status.get("romonitor", {})
    if not git("status", "--porcelain").stdout.strip():
        log(f"변경 없음(마지막 칸 {info.get('lastPoint')})")
        return True
    git("add", "public/data/platform_romonitor.json")
    git("-c", "user.name=roblox-observatory-pc", "-c", "user.email=smy7662@gmail.com",
        "commit", "-q", "-m", f"RoMonitor 직접 수집 {(now + timedelta(hours=9)).strftime('%m-%d %H:%M')}")
    if git("push", "-q", "origin", "HEAD:data", check=False).returncode != 0:
        return False  # Actions 가 먼저 push 함 → 호출한 쪽에서 한 번 더
    log(f"+{info.get('liveAdded')}칸(수정 {info.get('liveChanged')}), 마지막 칸 {info.get('lastPoint')} → push")
    return True


def main():
    now = datetime.now(timezone.utc)
    state = {}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as fh:
            state = json.load(fh)
    last = state.get("lastAttempt")
    if last and now - collect.parse_ts(last) < MIN_INTERVAL:
        return 0
    state["lastAttempt"] = collect.iso_z(now)
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    try:
        if not run_once(now):
            log("push 충돌 → 최신 data 브랜치로 다시 받아 한 번 더")
            if not run_once(now):
                log("두 번째 push 도 실패 — 다음 시간에 다시")
                return 1
        state["lastSuccess"] = collect.iso_z(now)
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        return 0
    except Exception as err:  # noqa: BLE001 — 차단(403)·네트워크 오류는 기록만, 우회하지 않음
        log(f"실패: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
