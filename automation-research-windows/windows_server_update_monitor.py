#!/usr/bin/env python3
"""
Windows Server 업데이트 자동 정리기

참고 설계(automation-research) 반영 포인트:
- 검색엔진 의존 대신 Microsoft 공식 소스 직접 파싱(Trusted Feed 방식)
- 기간 제어(최근 N개월 또는 start/end 날짜)
- 배치 수집 + 중복 제거 누적 DB
- 페이지/리스트 상단 최신순 전제의 조기 종료(Early termination)

기능:
- Microsoft Learn release info에서 특정 Windows Server 버전(기본: all) 또는 전체 버전의 KB 목록 수집
- 각 KB 문서에서 상세 변경사항/알려진 이슈 추출
- 기간(최근 1/3/6개월 또는 사용자 지정 start/end) 기준 필터링
- 위험도(LOW/MEDIUM/HIGH) 분류
- Markdown + JSON 리포트 생성
- 반복 실행 시 중복 제거된 누적 DB(JSON) 저장
- advisory 단건 JSON을 batch_data/ws<version>/ 에 저장

사용 예:
  python3 windows_server_update_monitor.py --window-months 1
  python3 windows_server_update_monitor.py --window-months 3 --version all --exclude 2016
  python3 windows_server_update_monitor.py --start-date 2025-11-01 --end-date 2026-02-29 --version all
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.request import Request, urlopen

from security_utils import atomic_write_json, atomic_write_text

RELEASE_INFO_URL = "https://learn.microsoft.com/en-us/windows/release-health/windows-server-release-info"
UA = "Mozilla/5.0 (compatible; WindowsUpdateMonitor/1.0)"

VERSION_MARKERS = {
    "2025": ("Windows Server 2025 (OS build 26100)", "Windows Server 2022 (OS build 20348)"),
    "2022": ("Windows Server 2022 (OS build 20348)", "Windows Server 2019 (OS build 17763)"),
    "2019": ("Windows Server 2019 (OS build 17763)", "Windows Server 2016 (OS build 14393)"),
    "2016": ("Windows Server 2016 (OS build 14393)", None),
}
ALL_VERSIONS = ["2025", "2022", "2019", "2016"]
VALID_VERSIONS = set(ALL_VERSIONS)


@dataclass
class UpdateInfo:
    kb: str
    title: str
    release_date: str
    url: str
    highlights: List[str]
    known_issues: str
    risk_level: str
    major_summary: str


def fetch(url: str) -> str:
    if not (url.startswith("https://learn.microsoft.com/") or url.startswith("https://support.microsoft.com/")):
        raise ValueError(f"허용되지 않은 수집 URL: {url}")
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="ignore")


def strip_html(html: str) -> str:
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_kb_urls_for_version(release_html: str, version: str, limit: int = 30) -> List[str]:
    start_marker, end_marker = VERSION_MARKERS[version]
    s = release_html.find(start_marker)
    if s < 0:
        raise RuntimeError(f"버전 섹션을 찾지 못했습니다: {version}")
    e = release_html.find(end_marker, s + 1) if end_marker else len(release_html)
    section = release_html[s:e]

    urls = re.findall(r'https://support\.microsoft\.com[^"\'\s<>]+', section)
    cleaned = []
    seen = set()
    for u in urls:
        u = u.replace("&amp;", "&")
        kb = extract_kb(u)
        if not kb:
            continue
        canonical = f"https://support.microsoft.com/help/{kb}"
        if canonical not in seen:
            seen.add(canonical)
            cleaned.append(canonical)

    return cleaned[:limit]


def extract_kb(url: str) -> Optional[str]:
    m = re.search(r"help/(\d{7})", url, flags=re.I)
    if m:
        return m.group(1)
    m = re.search(r"kb(\d{7})", url, flags=re.I)
    if m:
        return m.group(1)
    return None


def extract_release_date(text: str, title: str) -> str:
    m = re.search(r"([A-Za-z]+\s+\d{1,2},\s+\d{4})", title)
    if not m:
        m = re.search(r"Release Date:\s*([0-9/.-]+)", text, flags=re.I)
    if not m:
        return ""

    raw = m.group(1)
    for fmt in ("%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def extract_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
    if not m:
        return ""
    return unescape(re.sub(r"\s+", " ", m.group(1))).strip()


def extract_highlights(html: str) -> List[str]:
    anchor = re.search(r"The following is a summary of the issues[\s\S]{0,3000}?If you installed earlier updates", html, flags=re.I)
    block = anchor.group(0) if anchor else html
    items = re.findall(r"<li[^>]*>([\s\S]*?)</li>", block, flags=re.I)

    out = []
    for it in items:
        t = strip_html(it)
        if t and len(t) > 15:
            out.append(t)
    return out[:8]


def extract_known_issues(html: str) -> str:
    m = re.search(
        r"<h[23][^>]*>\s*Known issues(?: in this update)?\s*</h[23]>([\s\S]*?)(<h[23][^>]*>|$)",
        html,
        flags=re.I,
    )
    if m:
        text = strip_html(m.group(1))
        return re.sub(r"\s+", " ", text).strip(" :-") or "정보 없음"

    text_all = strip_html(html)
    m2 = re.search(r"Known issues in this update(.*?)(Servicing stack update|How to get this update|File information)", text_all, flags=re.I | re.S)
    if m2:
        return re.sub(r"\s+", " ", m2.group(1)).strip(" :-")

    return "정보 없음"


def classify_risk(highlights: List[str], known_issues: str) -> str:
    blob = (" ".join(highlights) + " " + known_issues).lower()
    if "not aware of any issues" in blob or "현재 알려진 문제 없음" in blob:
        if any(k in blob for k in ["vulnerability", "cve", "취약", "security"]):
            return "MEDIUM"
        return "LOW"

    high_kw = ["unable", "fail", "restart", "out-of-band", "remote code execution", "rce", "critical", "중요", "실패"]
    med_kw = ["security", "secure boot", "driver", "compatibility", "known issue", "문제", "경고"]

    if any(k in blob for k in high_kw):
        return "HIGH"
    if any(k in blob for k in med_kw):
        return "MEDIUM"
    return "LOW"


def major_summary(highlights: List[str]) -> str:
    if not highlights:
        return "주요 변경 사항을 추출하지 못했습니다."
    return " / ".join(highlights[:3])


def cutoff_date(months: int) -> datetime:
    return datetime.now() - timedelta(days=30 * months)


def resolve_date_range(window_months: int, start_date: str, end_date: str) -> Tuple[datetime, datetime]:
    if start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    else:
        start = cutoff_date(window_months)
        end = datetime.now()

    if start > end:
        raise SystemExit("start-date가 end-date보다 늦습니다.")
    return start, end


def collect(version: str, start_dt: datetime, end_dt: datetime, max_kb: int = 80) -> List[UpdateInfo]:
    release_html = fetch(RELEASE_INFO_URL)
    kb_urls = extract_kb_urls_for_version(release_html, version, limit=max_kb)

    out: List[UpdateInfo] = []
    old_hit = 0

    for u in kb_urls:
        html = fetch(u)
        title = extract_title(html)
        text = strip_html(html)
        date_str = extract_release_date(text, title)
        if not date_str:
            continue

        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        # 최신순 리스트 전제: 시작일 이전이 연속으로 나오면 조기 종료
        if d < start_dt:
            old_hit += 1
            if old_hit >= 2:
                break
            continue

        if d > end_dt:
            continue

        highlights = extract_highlights(html)
        known = extract_known_issues(html)
        risk = classify_risk(highlights, known)

        out.append(
            UpdateInfo(
                kb=extract_kb(u) or "",
                title=title,
                release_date=date_str,
                url=u,
                highlights=highlights,
                known_issues=known,
                risk_level=risk,
                major_summary=major_summary(highlights),
            )
        )

    out.sort(key=lambda x: x.release_date, reverse=True)
    return out


def merge_patch_db(version: str, items: List[UpdateInfo], outdir: Path) -> Path:
    db_path = outdir / f"ws{version}_patch_db.json"
    existing = []
    if db_path.exists():
        try:
            existing = json.loads(db_path.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    index = {}
    for row in existing:
        key = f"{row.get('kb','')}-{row.get('release_date','')}"
        index[key] = row

    for it in items:
        row = asdict(it)
        key = f"{it.kb}-{it.release_date}"
        index[key] = row

    merged = list(index.values())
    merged.sort(key=lambda x: x.get("release_date", ""), reverse=True)
    atomic_write_json(db_path, merged, mode=0o600, ensure_ascii=False, indent=2)
    return db_path


def write_batch_advisories(version: str, items: List[UpdateInfo], outdir: Path) -> Path:
    batch_dir = outdir / "batch_data" / f"ws{version}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    for x in items:
        fname = f"KB{x.kb}_{x.release_date}.json" if x.kb else f"UNKNOWN_{x.release_date}.json"
        atomic_write_json(batch_dir / fname, asdict(x), mode=0o600, ensure_ascii=False, indent=2)
    return batch_dir


def write_outputs(items: List[UpdateInfo], version: str, outdir: Path, start_dt: datetime, end_dt: datetime) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")

    json_path = outdir / f"ws{version}_updates_{stamp}.json"
    md_path = outdir / f"ws{version}_updates_{stamp}.md"
    latest_md = outdir / f"ws{version}_updates_latest.md"

    atomic_write_json(json_path, [asdict(x) for x in items], mode=0o600, ensure_ascii=False, indent=2)
    db_path = merge_patch_db(version, items, outdir)
    batch_dir = write_batch_advisories(version, items, outdir)

    lines = [
        f"# Windows Server {version} 업데이트 리포트 ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
        "",
        "## 요약",
        f"- 조회 기간: {start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')}",
        "- 수집 방식: Microsoft Learn release feed + KB 상세 파싱(Trusted Source)",
        f"- 수집 건수: {len(items)}",
        f"- HIGH 위험: {sum(1 for x in items if x.risk_level == 'HIGH')}건",
        f"- MEDIUM 위험: {sum(1 for x in items if x.risk_level == 'MEDIUM')}건",
        f"- LOW 위험: {sum(1 for x in items if x.risk_level == 'LOW')}건",
        f"- 누적 패치 DB: {db_path.name}",
        f"- 배치 산출물: {batch_dir}",
        "",
        "## 업데이트 상세",
    ]

    for x in items:
        lines += [
            "",
            f"### {x.release_date} - KB{x.kb} ({x.risk_level})",
            f"- 제목: {x.title}",
            f"- 링크: {x.url}",
            f"- 주요 변경 요약: {x.major_summary}",
            f"- 알려진 위험/이슈: {x.known_issues}",
            "- 상세 변경 항목:",
        ]
        lines += [f"  - {h}" for h in x.highlights] if x.highlights else ["  - (추출된 항목 없음)"]

    content = "\n".join(lines) + "\n"
    atomic_write_text(md_path, content, mode=0o600)
    atomic_write_text(latest_md, content, mode=0o600)

    print(f"완료: {md_path}")
    print(f"완료: {json_path}")
    print(f"완료: {latest_md}")
    print(f"완료: {db_path}")


def resolve_versions(version_arg: str, exclude_arg: str) -> List[str]:
    version_arg = (version_arg or "all").strip().lower()
    exclude_set = {x.strip() for x in (exclude_arg or "").split(",") if x.strip()}

    if version_arg == "all":
        base = list(ALL_VERSIONS)
    else:
        base = [x.strip() for x in version_arg.split(",") if x.strip()]

    for v in base:
        if v not in VALID_VERSIONS:
            raise SystemExit(f"지원하지 않는 버전: {v} (허용: {', '.join(sorted(VALID_VERSIONS))}, all)")
    for v in exclude_set:
        if v not in VALID_VERSIONS:
            raise SystemExit(f"exclude에 지원하지 않는 버전 포함: {v}")

    versions = [v for v in base if v not in exclude_set]
    if not versions:
        raise SystemExit("실행 대상 버전이 없습니다. --version/--exclude 값을 확인하세요.")

    dedup, seen = [], set()
    for v in versions:
        if v not in seen:
            seen.add(v)
            dedup.append(v)
    return dedup


def main() -> None:
    p = argparse.ArgumentParser(description="Windows Server 업데이트를 수집/요약")
    p.add_argument("--version", default="all", help="수집 버전 (기본: all). 예: all | 2019 | 2019,2022")
    p.add_argument("--exclude", default="", help="제외 버전(쉼표 구분). 예: 2016,2025")
    p.add_argument("--window-months", type=int, choices=[1, 3, 6], default=1, help="조회 기간(개월): 1|3|6 (기본: 1)")
    p.add_argument("--start-date", default="", help="수집 시작일(YYYY-MM-DD). end-date와 함께 사용")
    p.add_argument("--end-date", default="", help="수집 종료일(YYYY-MM-DD). start-date와 함께 사용")
    p.add_argument("--max-kb", type=int, default=80, help="버전별 최대 KB 검사 개수(기본: 80)")
    p.add_argument("--outdir", default=".")
    args = p.parse_args()

    versions = resolve_versions(args.version, args.exclude)
    start_dt, end_dt = resolve_date_range(args.window_months, args.start_date, args.end_date)

    for v in versions:
        items = collect(v, start_dt, end_dt, max_kb=args.max_kb)
        write_outputs(items, v, Path(args.outdir), start_dt, end_dt)


if __name__ == "__main__":
    main()
