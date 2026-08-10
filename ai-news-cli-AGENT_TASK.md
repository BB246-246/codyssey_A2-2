# AI 뉴스 트렌드 및 종합 분석 CLI — Coding Agent 작업 명세

## 0. 에이전트에게 주는 최상위 지시

이 문서를 프로젝트의 단일 요구사항 원본으로 사용하라.

- 질문을 최소화하고 합리적인 기본값으로 구현을 진행하라.
- 계획만 작성하고 멈추지 말고, 실제 파일을 생성하고 명령을 실행하여 검증하라.
- 각 단계에서 테스트를 실행하고 실패하면 원인을 수정한 뒤 다시 실행하라.
- 가짜 성공 로그나 실행하지 않은 결과를 보고하지 마라.
- 필수 기능을 모두 완성하기 전에는 보너스 기능을 구현하지 마라.
- API 키, 토큰, 개인 정보는 코드·로그·Git에 기록하지 마라.
- 크롤링 대상의 robots.txt 및 이용 정책을 확인하고, 우회·차단 회피를 하지 마라.
- 모든 코드를 단일 파일에 넣지 마라.
- Windows에서도 실행 가능해야 한다. 셸에 종속적인 구현은 피하라.
- 기존 저장소에 코드가 있다면 먼저 구조와 규칙을 읽고 호환되게 작업하라. 빈 디렉터리라면 아래 구조로 새 프로젝트를 만들어라.

---

## 1. 프로젝트 목표

Python 3.10+ 기반 CLI 애플리케이션을 완성한다. 사용자는 서브커맨드로 뉴스를 수집하고, 원본을 저장하고, 정제하고, AI로 기사별 요약 및 다건 종합 분석을 수행하고, 차트·리포트·내보내기 파일을 생성할 수 있어야 한다.

전체 파이프라인:

```text
CLI
→ API/RSS 수집 및 웹 크롤링
→ raw SQLite 저장
→ 정제 및 중복 처리
→ clean SQLite 저장
→ 기사별 AI 요약
→ 기간/카테고리별 AI 인사이트 분석
→ 통계·품질 지표·차트
→ 콘솔/Markdown 리포트
→ CSV/JSONL/Excel 내보내기
```

---

## 2. 고정 기술 결정

모호한 부분은 다음 선택으로 고정한다.

- Python: 3.10 이상
- CLI: 표준 라이브러리 `argparse`
- HTTP: `requests`
- RSS: `feedparser`
- 크롤링: `BeautifulSoup4`
- 영구 저장소: SQLite (`sqlite3`)
- AI: OpenAI Python SDK의 OpenAI-compatible API 방식
- AI 환경변수:
  - `AI_API_KEY`: 필수
  - `AI_BASE_URL`: 선택, 없으면 SDK 기본값
  - `AI_MODEL`: 선택, 없으면 config의 모델명
- 시각화: `matplotlib`
- 집계/내보내기: `pandas`, `openpyxl`
- 테스트: `pytest`
- 기본 내보내기 포맷: CSV, JSONL, Excel 세 가지 모두
- 시간은 ISO 8601 문자열로 저장
- DB 날짜는 UTC 또는 timezone-aware ISO 8601로 일관되게 처리
- 기사 중복 1차 키: 정규화된 canonical URL
- 기사 중복 2차 보조 키: 정규화된 제목+본문의 SHA-256 해시

AI 공급자 종속 코드는 `news_cli/ai_client.py`에만 둔다. 테스트에서는 실제 AI API를 호출하지 않고 fake/mock client를 사용한다.

---

## 3. 필수 프로젝트 구조

빈 프로젝트라면 다음 구조를 생성한다.

```text
ai-news-cli/
├─ main.py
├─ config.example.json
├─ config.json
├─ requirements.txt
├─ README.md
├─ .gitignore
├─ news_cli/
│  ├─ __init__.py
│  ├─ cli.py
│  ├─ config.py
│  ├─ logging_config.py
│  ├─ models.py
│  ├─ storage.py
│  ├─ cleaner.py
│  ├─ ai_client.py
│  ├─ summarizer.py
│  ├─ analyzer.py
│  ├─ charts.py
│  ├─ reporter.py
│  ├─ exporter.py
│  └─ collectors/
│     ├─ __init__.py
│     ├─ rss_collector.py
│     └─ web_collector.py
├─ tests/
│  ├─ fixtures/
│  │  ├─ sample_feed.xml
│  │  └─ sample_article.html
│  ├─ test_cli.py
│  ├─ test_config.py
│  ├─ test_storage.py
│  ├─ test_collectors.py
│  ├─ test_cleaner.py
│  ├─ test_summarizer.py
│  ├─ test_analyzer.py
│  ├─ test_charts.py
│  ├─ test_reporter.py
│  └─ test_exporter.py
├─ data/
├─ logs/
└─ output/
   ├─ charts/
   ├─ reports/
   └─ exports/
```

`config.json`, DB, 로그, 생성 출력물, `.env`, 가상환경은 `.gitignore` 처리한다. `config.example.json`은 커밋 가능한 예시 설정이어야 한다.

---

## 4. 설정 파일 요구사항

`config.example.json` 예시 구조:

```json
{
  "database_path": "data/news.db",
  "log_path": "logs/app.log",
  "request_timeout_seconds": 10,
  "request_delay_seconds": 1.0,
  "duplicate_policy": "skip",
  "default_ai_model": "gpt-4o-mini",
  "sources": {
    "default_rss": {
      "type": "rss",
      "url": "REPLACE_WITH_ALLOWED_RSS_URL",
      "category": "IT"
    },
    "default_web": {
      "type": "web",
      "list_url": "REPLACE_WITH_ALLOWED_LIST_URL",
      "base_url": "REPLACE_WITH_BASE_URL",
      "article_link_selector": "REPLACE_WITH_SELECTOR",
      "title_selector": "REPLACE_WITH_SELECTOR",
      "body_selector": "REPLACE_WITH_SELECTOR",
      "date_selector": "REPLACE_WITH_SELECTOR",
      "category": "IT"
    }
  }
}
```

구현 요구:

- config 파일 경로를 전역 `--config` 옵션으로 변경 가능하게 한다.
- 필수 설정 누락 시 사람이 이해할 수 있는 오류를 출력한다.
- API 키는 config가 아니라 환경변수에서 읽는다.
- source 이름으로 설정을 선택한다.
- 실제 기본 뉴스 소스는 정책을 확인한 뒤 허용된 공개 RSS와 정적 HTML 사이트를 선택한다.
- 특정 사이트 선택자가 깨져도 collector 외부 모듈은 수정하지 않도록 선택자를 config에서 읽는다.

---

## 5. CLI 명세

필수 서브커맨드:

```text
fetch
clean
summarize
analyze
report
export
```

보너스는 필수 기능 완료 후에만 `list`, `show`를 추가한다.

### 공통 호출

```bash
python main.py --config config.json <subcommand> [options]
```

### 5.1 fetch

```bash
python main.py fetch --method rss --source default_rss --limit 20
python main.py fetch --method crawl --source default_web --limit 20
```

옵션:

- `--method {rss,crawl}`: 필수
- `--source SOURCE_NAME`: 필수
- `--limit N`: 기본 20, 양의 정수

동작:

- RSS 방식과 웹 크롤링 방식을 모두 실제 구현한다.
- HTTP timeout을 적용한다.
- 적절한 User-Agent를 사용한다.
- 크롤링 요청 사이에 config의 delay를 적용한다.
- 한 항목이 실패해도 나머지 항목은 계속 처리한다.
- 원본 데이터, 수집 시각, 소스, URL, 수집 방법, 상태, 오류를 raw 저장소에 남긴다.
- 완료 로그에 시도/성공/실패/중복 수를 표시한다.

### 5.2 clean

```bash
python main.py clean --duplicate-policy skip
python main.py clean --duplicate-policy upsert --limit 100
```

옵션:

- `--duplicate-policy {skip,upsert}`: 없으면 config 값
- `--limit N`: 선택

정제 규칙:

- 필수 필드: title, url
- HTML 태그 및 엔티티 정리
- Unicode/공백/개행 정규화
- URL 정규화: fragment 제거, 추적 query parameter 제거 가능
- 날짜 ISO 8601 통일
- 본문 누락은 상태로 표시하되 프로그램 전체를 중단하지 않음
- category 누락은 `unknown`
- content hash 생성
- 같은 명령을 재실행해도 clean 레코드가 계속 증가하지 않는 멱등성 보장

### 5.3 summarize

```bash
python main.py summarize --unsummarized --limit 10
python main.py summarize --id 42
python main.py summarize --all --force --limit 10
```

옵션:

- `--all`, `--id ID`, `--unsummarized`: mutually exclusive, 셋 중 하나 필수
- `--limit N`: 선택
- `--force`: 기존 요약 덮어쓰기

동작:

- clean 기사의 본문을 AI에 전달한다.
- 기본 요약 언어는 한국어다.
- 사실을 추가하지 않고 핵심 3~5문장으로 요약하도록 프롬프트를 작성한다.
- 이미 요약된 뉴스는 기본 skip한다.
- 기사별 API 실패는 ERROR 로깅 후 다음 기사로 진행한다.
- 원문 길이, 요약 길이, 모델, 요약 시각을 저장한다.
- API 키와 기사 전문은 로그에 남기지 않는다.

### 5.4 analyze

```bash
python main.py analyze --date-from 2026-08-01 --date-to 2026-08-10 --category IT
```

옵션:

- `--date-from YYYY-MM-DD`: 선택
- `--date-to YYYY-MM-DD`: 선택
- `--category CATEGORY`: 선택
- `--limit N`: 안전한 최대 기사 수

동작:

- 조건에 맞는 clean 기사들을 조회한다.
- 입력은 제목+요약을 우선 사용하고 요약이 없으면 잘린 본문을 사용한다.
- 입력 총 글자 수 또는 기사 수 제한을 둔다.
- AI에 구조화 JSON을 요구한다.
- 최소 출력 필드:

```json
{
  "trends": ["..."],
  "keywords": ["..."],
  "commonalities_differences": ["..."],
  "implications": ["..."]
}
```

- 위 네 항목 중 적어도 trends, keywords, implications는 항상 요구하고 검증한다.
- JSON 파싱 또는 필수 키 검증 실패를 처리한다.
- 필터 조건, 기사 수, 모델, 결과, 생성 시각을 별도 analysis 테이블에 저장한다.
- 조건에 맞는 기사가 0개면 명확한 안내와 비정상 또는 의미 있는 종료 코드를 반환한다.

### 5.5 report

```bash
python main.py report --date-from 2026-08-01 --date-to 2026-08-10 --category IT --format md --top-n 5
python main.py report --format txt --top-n 10
```

옵션:

- 기간/카테고리 필터
- `--format {txt,md}`
- `--top-n N`
- `--output PATH`: 선택

리포트 포함 항목:

- 생성 시각과 분석 조건
- raw 기사 수, clean 기사 수, 요약 완료 수
- 품질 지표 최소 3개:
  - 정제 성공률 = clean/raw
  - 본문 보유율 = body 보유 clean/clean
  - 요약 완료율 = summarized/clean
- TOP N 집계 최소 1개: 출처별 또는 카테고리별 뉴스 수
- 최신 또는 조건이 일치하는 AI 인사이트
- 생성된 차트 2개의 경로
- 콘솔 출력
- TXT 또는 Markdown 파일 저장
- 데이터 0건에서도 ZeroDivisionError 없이 의미 있는 0%와 안내 표시

### 5.6 export

```bash
python main.py export --format csv --status summarized --output output/exports/news.csv
python main.py export --format jsonl --status summarized
python main.py export --format xlsx --status all
```

옵션:

- `--format {csv,jsonl,xlsx}`: 필수
- `--status {all,summarized,unsummarized}`: 기본 all
- 기간/카테고리 필터
- `--output PATH`: 선택

동작:

- CSV는 Excel 한글 호환을 위해 `utf-8-sig` 사용
- JSONL은 한 줄에 유효한 JSON 객체 하나
- Excel은 openpyxl 엔진 사용
- 내보낸 행 수를 로그와 콘솔에 표시
- 내보낸 파일을 테스트에서 다시 읽어 행 수와 주요 필드를 검증

---

## 6. SQLite 스키마 요구사항

마이그레이션 도구까지 만들 필요는 없다. 앱 시작 시 `CREATE TABLE IF NOT EXISTS`로 초기화한다.

### raw_articles

최소 필드:

```text
id INTEGER PRIMARY KEY
external_id TEXT
source_name TEXT NOT NULL
source_url TEXT
collection_method TEXT NOT NULL
collected_at TEXT NOT NULL
canonical_url TEXT NOT NULL
raw_payload TEXT NOT NULL
status TEXT NOT NULL
error_message TEXT
UNIQUE(source_name, canonical_url)
```

### clean_articles

최소 필드:

```text
id INTEGER PRIMARY KEY
raw_id INTEGER NOT NULL UNIQUE
title TEXT NOT NULL
body TEXT
url TEXT NOT NULL UNIQUE
source TEXT NOT NULL
category TEXT NOT NULL
published_at TEXT
collected_at TEXT NOT NULL
content_hash TEXT
clean_status TEXT NOT NULL
summary TEXT
summary_model TEXT
summarized_at TEXT
original_length INTEGER
summary_length INTEGER
FOREIGN KEY(raw_id) REFERENCES raw_articles(id)
```

### analysis_runs

최소 필드:

```text
id INTEGER PRIMARY KEY
date_from TEXT
date_to TEXT
category TEXT
article_count INTEGER NOT NULL
trends_json TEXT NOT NULL
keywords_json TEXT NOT NULL
commonalities_differences_json TEXT
implications_json TEXT NOT NULL
model TEXT
created_at TEXT NOT NULL
```

### fetch_runs

품질 지표와 실행 기록을 위해 구현 권장:

```text
id INTEGER PRIMARY KEY
source_name TEXT
collection_method TEXT
attempted_count INTEGER
success_count INTEGER
failure_count INTEGER
duplicate_count INTEGER
started_at TEXT
finished_at TEXT
```

DB 쓰기는 트랜잭션을 사용하고 연결이 항상 닫히게 한다.

---

## 7. 로깅 요구사항

Python `logging` 모듈을 사용한다.

- 콘솔 핸들러 + rotating file handler 권장
- INFO: 시작, 대상 수, 성공, 저장 경로, 최종 통계
- WARNING: 결측값, 중복 skip, 파싱 불완전
- ERROR: HTTP 실패, AI 실패, 저장 실패
- 로그 포맷에 시간, 레벨, 모듈, 메시지 포함
- API 키, Authorization 헤더, 기사 전문은 로그 금지

---

## 8. 차트 요구사항

`matplotlib`으로 PNG 최소 2개를 생성한다.

1. `output/charts/category_counts.png`
   - 카테고리별 뉴스 수 막대그래프
2. `output/charts/daily_collection_trend.png`
   - 일자별 수집 추이 선그래프

요구:

- 제목, 축 라벨, 값이 식별 가능해야 함
- 한글 폰트를 적용하고 폰트가 없을 때 경고 후 사용 가능한 fallback 사용
- headless 환경에서 동작하도록 적절한 backend 사용
- 빈 데이터에서도 예외 없이 안내 또는 빈 차트를 생성
- 저장 후 figure를 close하여 리소스 해제

---

## 9. 테스트 요구사항

실제 네트워크와 실제 AI API에 의존하지 않는 자동 테스트를 우선 작성한다.

### 반드시 검증할 테스트

- CLI 도움말에 필수 6개 명령이 존재
- 음수 `--limit` 거부
- summarize 대상 옵션 상호 배타성
- config 필수 필드 검증
- SQLite 종료/재연결 후 데이터 지속
- raw 중복 skip
- clean upsert
- clean 재실행 멱등성
- RSS fixture 파싱
- HTML fixture 파싱
- timeout/HTTP 오류 mock 처리
- 필수 필드 누락 처리
- 텍스트·날짜 정규화
- fake AI 요약 성공/실패 및 다음 기사 계속
- 기존 요약 기본 skip와 force 덮어쓰기
- 분석 JSON 성공 및 잘못된 JSON 처리
- 빈 분석 대상 처리
- 품질 지표 0 나누기 방지
- PNG 2개 생성
- CSV/JSONL/XLSX round-trip과 한글 보존
- `--status summarized` 필터 정확성

테스트 명령:

```bash
python -m pytest -v
```

가능하면 coverage도 실행한다.

```bash
python -m pytest --cov=news_cli --cov-report=term-missing
```

테스트가 모두 통과하기 전에는 완료로 보고하지 않는다.

---

## 10. 구현 순서

아래 순서를 지킨다.

1. 기존 저장소 및 Python 환경 조사
2. 프로젝트 구조, requirements, config, logging 생성
3. argparse 6개 서브커맨드와 옵션 골격
4. SQLite 스키마·저장·조회·skip/upsert
5. fixture 기반 RSS 파서
6. fixture 기반 HTML 파서
7. 실제 HTTP 계층 및 오류/timeout/delay
8. `fetch` 통합
9. 정제 함수와 `clean` 통합
10. AI 추상화와 fake 기반 요약 테스트
11. 실제 AI SDK 연결 및 `summarize`
12. 구조화된 AI 분석 및 `analyze`
13. 집계·차트 생성
14. 리포트 생성
15. CSV/JSONL/XLSX export
16. 전체 자동 테스트
17. 실제 네트워크/AI 키가 있을 때 소량 smoke test
18. README 및 최종 검증

각 단계에서 테스트를 실행하고 회귀가 없는지 확인한다.

---

## 11. 실제 실행 검증 시나리오

테스트 통과 후 가능한 범위에서 아래를 실제 실행한다. 비용과 정책을 고려해 limit를 작게 시작한다.

```bash
python main.py --help
python main.py fetch --method rss --source default_rss --limit 3
python main.py fetch --method crawl --source default_web --limit 3
python main.py clean --duplicate-policy skip --limit 10
python main.py summarize --unsummarized --limit 2
python main.py analyze --date-from 2026-08-01 --date-to 2026-08-10 --category IT --limit 10
python main.py report --format md --top-n 5
python main.py export --format csv --status summarized
python main.py export --format jsonl --status summarized
python main.py export --format xlsx --status summarized
```

네트워크 또는 API 키가 없어 실제 smoke test가 불가능하면:

- 자동 테스트는 fixture/mock으로 완전히 검증한다.
- 무엇을 실제로 실행했고 무엇이 외부 조건 때문에 실행되지 않았는지 명확히 보고한다.
- 실행하지 않은 실제 결과를 꾸며내지 않는다.

---

## 12. README 요구사항

README에는 다음을 포함한다.

1. 프로젝트 목적과 데이터 파이프라인 그림
2. Python 버전과 설치 방법
3. 가상환경 생성 및 의존성 설치
4. `config.example.json` 복사 방법
5. `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL` 설정 방법
6. 모든 CLI 명령 예시
7. SQLite 테이블 설명
8. raw/clean 분리 이유
9. API/RSS와 크롤링의 장단점
10. timeout, 오류 처리, 중복 처리 정책
11. 크롤링 대상 정책, robots.txt, 요청 간격
12. 차트·리포트·export 결과 위치
13. 테스트 실행 방법
14. 알려진 제한
15. 선택적으로 Windows 작업 스케줄러와 cron 정기 실행 예시

---

## 13. 보너스 기능

필수 요구사항과 테스트가 모두 완료된 경우에만 다음 순서로 추가한다.

1. `list` 서브커맨드
   - category/date/keyword 필터
   - page/page-size 페이지네이션
2. `show --id ID`
3. AI 감성 분석: positive/negative/neutral
4. 감성 분포 차트
5. Windows 작업 스케줄러와 cron 안내

보너스 때문에 필수 기능의 안정성을 훼손하지 마라.

---

## 14. 완료 조건(Definition of Done)

다음 항목을 모두 만족해야 완료다.

- [ ] `fetch`, `clean`, `summarize`, `analyze`, `report`, `export`가 argparse 서브커맨드로 존재
- [ ] RSS/API 계열 수집과 웹 크롤링이 모두 구현됨
- [ ] HTTP timeout, 오류 처리, 요청 간 delay가 구현됨
- [ ] raw와 clean이 SQLite에 분리되어 영구 저장됨
- [ ] skip/upsert 정책이 테스트됨
- [ ] 기사별 AI 요약과 대상 옵션이 동작함
- [ ] AI 실패 시 해당 기사만 건너뛰고 계속 진행함
- [ ] 조건별 AI 종합 분석 결과가 DB에 저장되고 조회됨
- [ ] 주요 트렌드, 키워드, 시사점이 분석 결과에 포함됨
- [ ] 카테고리별 건수와 일자별 수집 추이 PNG가 생성됨
- [ ] 한글 폰트 처리 또는 명확한 fallback이 있음
- [ ] 품질 지표 3개와 TOP N, AI 인사이트가 리포트에 포함됨
- [ ] 콘솔 및 TXT/MD 리포트가 지원됨
- [ ] CSV, JSONL, Excel 내보내기가 지원됨
- [ ] `--status summarized` 필터가 검증됨
- [ ] config와 logging 요구사항 충족
- [ ] 최소 4개 이상 모듈로 분리됨
- [ ] `python -m pytest -v`가 성공함
- [ ] README 설치 및 실행 절차가 실제 명령과 일치함
- [ ] 비밀값과 생성 데이터가 Git에서 제외됨

---

## 15. 최종 보고 형식

작업 종료 시 다음만 명확하게 보고한다.

1. 구현한 기능 요약
2. 생성·수정한 핵심 파일
3. 선택한 실제 뉴스 소스와 정책 확인 내용
4. 실행한 테스트 명령과 실제 통과 결과
5. 실행한 smoke test 명령과 실제 결과
6. 외부 API 키/네트워크 문제로 검증하지 못한 항목
7. 사용자가 처음 실행할 정확한 명령 순서
8. 남아 있는 알려진 제한 또는 보너스 미구현 사항

계획만 제시하지 말고 실제 구현·테스트·검증까지 수행한 뒤 보고하라.
