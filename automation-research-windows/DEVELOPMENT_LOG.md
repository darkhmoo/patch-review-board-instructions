# Windows Server Update Monitor 설계 문서 (v2)

## 개요
`windows_server_update_monitor.py`는 Microsoft 공식 사이트에서 Windows Server 업데이트 정보를 수집하고,
업데이트별 주요 변경 사항/알려진 이슈/위험도를 정리해 리포트를 생성하는 자동화 스크립트입니다.

---

## 목표
- Windows Server 업데이트를 **월 단위로 자동 수집** (2016/2019/2022/2025 전체 지원)
- 각 KB의 **상세 변경 사항**과 **Known issues** 정리
- **최근 1/3/6개월 기간 선택** 수집
- 운영 관점에서 **위험도(HIGH/MEDIUM/LOW)** 분류
- 사람이 읽기 좋은 **Markdown**, 후처리 가능한 **JSON** 출력
- 반복 실행 시 중복 제거되는 **누적 패치 DB(JSON)** 유지

---

## 아키텍처
1. **수집 단계**
   - Microsoft Learn의 Windows Server release info 페이지(Trusted Feed)에서 버전별 KB 링크 추출
   - 기간 밖 데이터가 연속 감지되면 조기 종료(Early termination)
2. **상세 파싱 단계**
   - 각 KB Support 문서를 열어 주요 변경사항/Known issues 파싱
3. **분석 단계**
   - 키워드 기반 위험도 분류
   - 주요 변경 요약 생성
4. **출력 단계**
   - `ws<version>_updates_<date>.md`
   - `ws<version>_updates_<date>.json`
   - `ws<version>_updates_latest.md`
   - `ws<version>_patch_db.json` (중복 제거 누적 DB)
   - `batch_data/ws<version>/KB<번호>_<날짜>.json` (단건 advisory 산출물)

---

## 시퀀스 다이어그램 (텍스트)
```text
[cron]
  -> python windows_server_update_monitor.py
    -> fetch(release-info page)
      -> extract KB urls (by version)
        -> for each KB:
             fetch(KB page)
             parse highlights
             parse known issues
             classify risk
        -> write JSON
        -> write Markdown(date)
        -> write Markdown(latest)
  -> (선택) 요약 메시지 전송
```

---

## 주요 데이터 모델
`UpdateInfo` (dataclass)
- `kb`: KB 번호
- `title`: 문서 제목
- `release_date`: 릴리스 날짜
- `url`: KB URL
- `highlights`: 상세 변경 항목 목록
- `known_issues`: 알려진 이슈 텍스트
- `risk_level`: LOW/MEDIUM/HIGH
- `major_summary`: 핵심 요약

---

## 핵심 함수 설계
- `fetch(url)`
  - URL의 HTML을 가져옴 (urllib 사용)
- `extract_kb_urls_for_version(release_html, version, limit)`
  - 버전 섹션에서 KB 링크 추출/중복제거/정규화
- `resolve_date_range(window_months, start_date, end_date)`
  - 최근 N개월 또는 명시적 날짜 범위를 수집 범위로 확정
- `extract_highlights(html)`
  - 요약 구간의 list 항목 기반 주요 변경 추출
- `extract_known_issues(html)`
  - Known issues 섹션 파싱(헤더 기반 + 폴백)
- `classify_risk(highlights, known_issues)`
  - 규칙 기반 위험도 분류
- `write_outputs(items, version, outdir)`
  - MD/JSON 파일 저장, latest 파일 동기화

---

## 위험도 분류 규칙
- **HIGH**
  - 장애/재부팅/인증 실패/RCE 등 서비스 중단 가능성 키워드 포함
- **MEDIUM**
  - 보안/드라이버/호환성 영향 가능성이 있으나 즉시 장애 가능성은 낮음
- **LOW**
  - 알려진 문제 없음, 위험 키워드 미약

> 주의: 규칙 기반 분류이므로 실제 운영 적용 전 테스트 환경 검증 필요

---

## 자동 실행 설계
월 1회 cron 실행:
- 스케줄: `0 10 1 * *` (Asia/Seoul)
- 기본 실행: `--version all` (전체 대상)
- 필요 시 `--exclude`로 일부 버전 제외 가능
- 수행:
  1) 스크립트 실행
  2) 최신 리포트 생성
  3) 요약 메시지 작성

---

## 실패/재시도 정책
- 기본 타임아웃: 요청당 25초
- 재시도 정책(권장): 최대 3회, 지수 백오프(1s → 2s → 4s)
- 부분 실패 처리:
  - 특정 KB fetch 실패 시 해당 KB만 `risk_level=UNKNOWN`으로 기록 후 계속 진행
  - 전체 실패 시 이전 `latest` 파일 보존
- 로깅 정책:
  - `stdout`: 성공/실패 건수
  - `stderr`: 실패 URL, 예외 메시지

---

## 예외 처리/한계
- HTML 구조 변경 시 파싱 실패 가능 → 정규식/마커 업데이트 필요
- 네트워크 장애 시 수집 실패 가능 → 재시도 로직 강화 필요
- 현재는 룰 기반 분석이며 CVE 심각도 매핑은 미포함

---

## 테스트 케이스
| ID | 시나리오 | 입력/조건 | 기대 결과 |
|---|---|---|---|
| TC-01 | 정상 수집 | `--version 2019 --window-months 1` | MD/JSON/latest + patch_db + batch_data 생성 |
| TC-02 | 버전 변경 | `--version 2022` | 2022 섹션 KB만 수집 |
| TC-03 | 날짜 범위 수집 | `--start-date 2025-11-01 --end-date 2026-02-29` | 지정 범위 내 KB만 수집 |
| TC-04 | 네트워크 실패 | KB URL 1개 타임아웃 | 나머지 KB는 계속 처리 |
| TC-05 | 잘못된 인자 | `--version 2030` | argparse 에러로 종료 |
| TC-06 | 출력 경로 변경 | `--outdir ./reports` | 지정 경로에 파일 생성 |

---

## 실행 방법
```bash
# 기본값: 전체 버전 + 최근 1개월
python3 windows_server_update_monitor.py --outdir .

# 최근 3개월
python3 windows_server_update_monitor.py --window-months 3 --outdir .

# 최근 6개월 + 일부 버전만
python3 windows_server_update_monitor.py --window-months 6 --version 2019,2022 --outdir .

# 전체에서 일부 제외(가감)
python3 windows_server_update_monitor.py --window-months 3 --version all --exclude 2016 --outdir .

# 명시적 날짜 범위 수집
python3 windows_server_update_monitor.py --start-date 2025-11-01 --end-date 2026-02-29 --version all --outdir .
```

---

## 향후 개선
- BeautifulSoup 기반 정밀 파서 옵션 추가
- CVE 추출 및 심각도 연동
- Server 2019/2022/2025 통합 대시보드
- 엑셀 출력 및 월간 diff 리포트 추가
- 위험도 산정 시 서비스 영향도(AD/RDP/SMB/WSUS) 가중치 적용

---

작성일: 2026-02-13
파일: `windows_server_update_monitor_design.md`
