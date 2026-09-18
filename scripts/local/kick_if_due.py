#!/usr/bin/env python3
"""GitHub 예약 실행이 빠졌을 때만 수집 워크플로를 대신 깨우는 로컬 보조 트리거.

왜: GitHub Actions 의 schedule 은 부하가 크면 실행을 버린다(문서에 명시).
    실측 2026-09-17: 매시 2회 예약 → 18시간에 8회만 실행, 최대 공백 5시간 48분.
    2026-09-18: */10 으로 바꾼 뒤 70분 동안 예약 실행 0회.

무엇을 하나: data 브랜치의 collection_status.json 을 읽어, collect.py 와 **같은 규칙**
(due_tiers)으로 이번 시간대 수집이 아직 안 됐는지 본다. 안 됐고 정각에서 10분 넘게
지났으면(= GitHub 예약이 먼저 할 기회를 줬는데도 안 했으면) workflow_dispatch 로 깨운다.
이미 됐거나 실행이 대기·진행 중이면 아무것도 안 한다.

Windows 작업 스케줄러가 15분마다 kick.vbs → kick.cmd → 이 파일 순으로 부른다.
PC 가 켜져 있을 때만 돈다(절전 해제는 걸지 않음).
"""

import base64
import io
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = "smy7662-dotcom/roblox-usage-observatory"
WORKFLOW = "collect-deploy.yml"
GRACE_MINUTES = 10  # 정각 뒤 이만큼은 GitHub 예약에 맡긴다
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "..", "..", "logs", "kick.log")

sys.path.insert(0, os.path.join(HERE, ".."))
from collect import due_tiers  # noqa: E402  같은 판정 규칙을 그대로 쓴다

GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"


def gh(*args):
    out = subprocess.run([GH, *args], capture_output=True, text=True, encoding="utf-8", timeout=90)
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} 실패: {out.stderr.strip()[:300]}")
    return out.stdout


def log(msg):
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    now = datetime.now(timezone.utc)
    try:
        raw = gh("api", f"repos/{REPO}/contents/public/data/collection_status.json?ref=data", "--jq", ".content")
        status = json.loads(base64.b64decode(raw.strip()).decode("utf-8"))
    except Exception as err:  # noqa: BLE001
        log(f"상태 읽기 실패 → 이번 회차 건너뜀: {err}")
        return 1

    tiers = due_tiers(status, now, None)
    if not tiers:
        log(f"수집 최신(마지막 {status.get('ranAt')}) — 할 일 없음")
        return 0
    if now.minute < GRACE_MINUTES:
        log(f"{tiers[0]} 대기 중이지만 정각 {GRACE_MINUTES}분 전이라 GitHub 예약에 맡김")
        return 0

    try:
        active = gh("run", "list", "-R", REPO, "--workflow", WORKFLOW, "--limit", "5",
                    "--json", "status", "--jq", '[.[]|select(.status!="completed")]|length')
        if int(active.strip() or 0) > 0:
            log(f"{tiers[0]} 필요하지만 실행이 이미 대기·진행 중 — 건너뜀")
            return 0
        gh("workflow", "run", WORKFLOW, "-R", REPO, "--ref", "main")
    except Exception as err:  # noqa: BLE001
        log(f"깨우기 실패: {err}")
        return 1
    log(f"{tiers[0]} 누락 감지(마지막 {status.get('ranAt')}) → workflow_dispatch 보냄")
    return 0


if __name__ == "__main__":
    sys.exit(main())
