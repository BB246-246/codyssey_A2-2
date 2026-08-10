# AI 뉴스 트렌드 및 종합 분석 CLI

RSS/웹 크롤링으로 뉴스를 수집하고, 원본을 보존한 채 정제하고, AI로 기사별 요약과
기간·카테고리별 종합 분석을 수행한 뒤 차트·리포트·내보내기 파일을 만드는 Python CLI입니다.

## 1. 목적과 데이터 파이프라인

```text
CLI (argparse)
  └─ fetch      → API/RSS 수집 + 정적 HTML 크롤링
                    └─ raw SQLite 저장 (raw_articles)
  └─ clean      → HTML 제거 / 유니코드·공백 정규화 / URL 정규화 / 날짜 ISO 8601 통일
                    └─ clean SQLite 저장 (clean_articles)
  └─ summarize  → 기사별 AI 요약 (한국어 3~5문장)
  └─ analyze    → 기간/카테고리별 AI 인사이트 (구조화 JSON) → analysis_runs
  └─ report     → 통계 + 품질 지표 3종 + TOP N + AI 인사이트 + 차트 2종
                    └─ 콘솔 출력 + TXT/Markdown 파일
  └─ export     → CSV / JSONL / Excel
```

부가 명령: `list`(필터·페이지네이션 조회), `show --id`(상세 보기).

## 2. 요구 사항

- **Python 3.10 이상** (개발·검증은 3.11.15에서 수행)
- Windows / macOS / Linux (셸 종속 코드 없음, matplotlib은 headless `Agg` 백엔드 사용)

## 3. 설치

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### macOS / Linux (bash)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

주요 의존성: `requests`, `feedparser`, `beautifulsoup4`, `matplotlib`, `pandas`,
`openpyxl`, `openai`, `pytest`.

## 4. 설정 파일

`config.example.json`을 복사해 `config.json`을 만듭니다. `config.json`은 `.gitignore` 대상입니다.

```powershell
# Windows
Copy-Item config.example.json config.json
```

```bash
# macOS / Linux
cp config.example.json config.json
```

주요 항목:

| 키 | 설명 |
| --- | --- |
| `database_path` | SQLite 파일 경로 (기본 `data/news.db`) |
| `log_path` | 로그 파일 경로 (기본 `logs/app.log`, 1MB×3 rotating) |
| `request_timeout_seconds` | 모든 HTTP 요청의 timeout |
| `request_delay_seconds` | 크롤링 요청 사이 최소 대기 시간 |
| `duplicate_policy` | `skip` 또는 `upsert` (CLI `--duplicate-policy`로 덮어쓰기 가능) |
| `default_ai_model` | `AI_MODEL`이 없을 때 쓰는 모델명 |
| `user_agent` | 수집 시 사용할 User-Agent (연락처를 넣어두길 권장) |
| `respect_robots_txt` | `true`면 robots.txt를 확인하고 금지된 URL은 요청하지 않음 |
| `max_analysis_articles` / `max_analysis_chars` | analyze 입력 상한(안전장치) |
| `sources.<name>` | 소스 정의. `type`이 `rss`면 `url`, `web`이면 `list_url`과 CSS 선택자들 |

**선택자는 전부 config에서 읽습니다.** 대상 사이트 구조가 바뀌어도
`article_link_selector` / `title_selector` / `body_selector` / `date_selector`만 고치면 되고,
collector 외부 모듈은 손대지 않아도 됩니다.

전역 옵션 `--config`로 다른 설정 파일을 지정할 수 있습니다.

```bash
python main.py --config config.staging.json fetch --method rss --source default_rss
```

## 5. AI 환경변수

API 키는 **설정 파일이 아니라 환경변수에서만** 읽습니다. 코드·로그·Git 어디에도 남지 않습니다.

| 변수 | 필수 | 설명 |
| --- | --- | --- |
| `AI_API_KEY` | ✅ | OpenAI 호환 API 키 |
| `AI_BASE_URL` | ❌ | OpenAI 호환 엔드포인트. 없으면 SDK 기본값 |
| `AI_MODEL` | ❌ | 모델명. 없으면 `config.default_ai_model` |

```powershell
# Windows PowerShell (현재 세션)
$env:AI_API_KEY = "sk-..."
$env:AI_BASE_URL = "https://api.openai.com/v1"   # 선택
$env:AI_MODEL = "gpt-4o-mini"                    # 선택
```

```bash
# macOS / Linux
export AI_API_KEY="sk-..."
export AI_BASE_URL="https://api.openai.com/v1"   # 선택
export AI_MODEL="gpt-4o-mini"                    # 선택
```

모델 결정 우선순위: `--model` 인자 > `AI_MODEL` > `config.default_ai_model`.

## 6. CLI 명령 전체 예시

```bash
python main.py --help
python main.py --version

# 1) 수집
python main.py fetch --method rss   --source default_rss --limit 20
python main.py fetch --method crawl --source default_web --limit 20

# 2) 정제 (재실행해도 clean 레코드가 늘지 않음)
python main.py clean --duplicate-policy skip
python main.py clean --duplicate-policy upsert --limit 100

# 3) 기사별 AI 요약 (--all / --id / --unsummarized 중 하나 필수)
python main.py summarize --unsummarized --limit 10
python main.py summarize --id 42
python main.py summarize --all --force --limit 10

# 4) 기간/카테고리 AI 종합 분석
python main.py analyze --date-from 2026-08-01 --date-to 2026-08-10 --category IT --limit 10

# 5) 리포트 (콘솔 출력 + 파일 저장)
python main.py report --date-from 2026-08-01 --date-to 2026-08-10 --category IT --format md --top-n 5
python main.py report --format txt --top-n 10
python main.py report --format md --output output/reports/weekly.md

# 6) 내보내기
python main.py export --format csv   --status summarized --output output/exports/news.csv
python main.py export --format jsonl --status summarized
python main.py export --format xlsx  --status all

# 부가 명령
python main.py list --category IT --keyword AI --page 1 --page-size 10
python main.py show --id 5

# 전역 옵션
python main.py --config config.json --log-level DEBUG fetch --method rss --source default_rss
```

### 종료 코드

| 코드 | 의미 |
| --- | --- |
| 0 | 성공 |
| 1 | 처리된 오류(설정 누락, 네트워크 실패, AI 실패, 저장 실패 등) |
| 2 | 잘못된 명령행 사용(argparse) |
| 3 | 조건에 맞는 데이터가 없음 (`analyze`, `show`) |

## 7. SQLite 테이블

앱 시작 시 `CREATE TABLE IF NOT EXISTS`로 초기화됩니다. 별도 마이그레이션 도구는 없습니다.

### `raw_articles` — 수집 원본

| 컬럼 | 설명 |
| --- | --- |
| `id` | PK |
| `external_id` | 소스가 주는 식별자(RSS guid 등) |
| `source_name` / `source_url` | config의 소스 이름 / 수집에 쓴 목록·피드 URL |
| `collection_method` | `rss` 또는 `crawl` |
| `collected_at` | 수집 시각 (UTC ISO 8601) |
| `canonical_url` | 정규화된 URL — **중복 판정 1차 키** |
| `raw_payload` | 수집한 원본 항목 전체(JSON 문자열) |
| `status` | `ok` / `partial` / `error` / `invalid` |
| `error_message` | 실패 사유 |
| | `UNIQUE(source_name, canonical_url)` |

### `clean_articles` — 정제 결과 + 요약

| 컬럼 | 설명 |
| --- | --- |
| `id` | PK |
| `raw_id` | `raw_articles.id` (UNIQUE, FK) |
| `title` / `body` / `url` | 정제된 제목 / 본문 / URL (`url` UNIQUE) |
| `source` / `category` | 출처 / 카테고리(없으면 `unknown`) |
| `published_at` / `collected_at` | UTC ISO 8601 |
| `content_hash` | 정규화된 제목+본문의 SHA-256 — **중복 판정 2차 보조 키** |
| `clean_status` | `ok` / `no_body` |
| `summary` / `summary_model` / `summarized_at` | AI 요약 결과와 메타데이터 |
| `original_length` / `summary_length` | 원문·요약 글자 수 |

### `analysis_runs` — AI 종합 분석 기록

`date_from`, `date_to`, `category`, `article_count`, `trends_json`, `keywords_json`,
`commonalities_differences_json`, `implications_json`, `model`, `created_at`.

### `fetch_runs` — 수집 실행 기록

`source_name`, `collection_method`, `attempted_count`, `success_count`,
`failure_count`, `duplicate_count`, `started_at`, `finished_at`.

모든 쓰기는 트랜잭션(`with conn:`)으로 감싸고, 연결은 컨텍스트 매니저로 항상 닫습니다.

## 8. raw / clean을 분리한 이유

1. **원본 보존.** 정제 규칙이 잘못돼도 원본이 남아 있으므로 다시 정제할 수 있습니다.
   재수집(=재요청)이 필요 없어 대상 사이트 부하도 줄어듭니다.
2. **책임 분리.** 수집은 "가져오기"만, 정제는 "정리"만 합니다. 사이트 구조 변경은
   collector와 config에만 영향을 주고, 정제·요약·분석 로직은 그대로입니다.
3. **디버깅.** `raw_payload`를 열어보면 파싱이 틀렸는지 사이트가 바뀐 건지 바로 알 수 있습니다.
4. **멱등성.** `clean`은 몇 번을 다시 돌려도 `clean_articles` 행 수가 늘지 않습니다.
   실패한 배치를 부담 없이 재실행할 수 있습니다.
5. **품질 지표.** 정제 성공률(clean/raw) 같은 지표는 두 계층이 분리돼 있어야 계산됩니다.

## 9. API/RSS vs 웹 크롤링

| | API / RSS | 웹 크롤링 |
| --- | --- | --- |
| 장점 | 구조화된 데이터, 파싱이 안정적, 사이트 변경에 강함, 제공자가 명시적으로 허용 | 제공되는 피드가 없어도 수집 가능, 본문 전문 확보 가능 |
| 단점 | 본문이 없거나 요약만 오는 경우가 많음, 제공 항목 수 제한, 피드가 없는 사이트는 불가 | HTML 구조 변경에 취약, 요청 수가 많고 느림, 정책·저작권 확인 필수 |
| 이 프로젝트 | `default_rss`(Hacker News RSS) — 제목·링크·요약 확보 | `default_web`(English Wikinews) — 본문 전문 확보 |

실무에서는 **RSS를 우선 쓰고, 본문이 필요할 때만 크롤링을 보조로** 쓰는 조합이 안전합니다.
이 프로젝트도 그 구성을 기본값으로 삼았습니다.

## 10. timeout · 오류 처리 · 중복 처리 정책

**timeout**: 모든 HTTP 요청에 `config.request_timeout_seconds`가 적용됩니다.
`requests.Timeout`은 `FetchError`로 변환되어 해당 항목만 실패 처리됩니다.

**오류 처리**: 한 항목의 실패가 전체 실행을 중단시키지 않습니다.

- 수집: 기사 1건이 HTTP 오류/파싱 실패여도 나머지는 계속 수집하고, 실패 건은
  `status='error'`로 raw에 기록됩니다.
- 정제: 필수 필드(title/url)가 없으면 그 건만 `invalid`로 표시하고 계속 진행합니다.
  본문이 없으면 `clean_status='no_body'`로 남기되 실패로 보지 않습니다.
- 요약: 기사별 AI 실패는 ERROR 로깅 후 다음 기사로 넘어갑니다.
- 분석: JSON 파싱 실패 또는 필수 키(`trends`/`keywords`/`implications`) 누락 시
  결과를 저장하지 않고 오류를 반환합니다.

**중복 처리**:

- 1차 키는 정규화된 canonical URL입니다. `UNIQUE(source_name, canonical_url)` 제약으로
  raw 단계에서 중복이 걸러지고 `duplicate` 카운트로 집계됩니다.
- URL 정규화는 fragment 제거, 추적 파라미터(`utm_*`, `fbclid`, `gclid` 등) 제거,
  호스트 소문자화, 기본 포트 제거, query 정렬을 수행합니다.
- 2차 보조 키는 정규화된 제목+본문의 SHA-256(`content_hash`)으로, URL이 달라도
  같은 내용인지 식별할 수 있게 저장해 둡니다.
- clean 단계 정책: `skip`은 기존 레코드를 그대로 두고, `upsert`는 최신 raw 내용으로 갱신합니다.
  **어느 정책이든 행 수는 늘어나지 않습니다.**

## 11. 크롤링 정책과 robots.txt

기본 수집 대상은 정책을 확인한 뒤 선택했습니다.

| 소스 | URL | 확인 결과 |
| --- | --- | --- |
| `default_rss` | `https://hnrss.org/frontpage` | `hnrss.org/robots.txt` → `User-agent: * / Disallow:` (전면 허용). Hacker News가 공개한 콘텐츠를 RSS로 재배포하는 공개 서비스입니다. |
| `ai_rss` | `https://hnrss.org/newest?q=AI&count=25` | 위와 동일 |
| `default_web` | `https://en.wikinews.org/wiki/Category:Science_and_technology` | Wikimedia robots.txt의 `User-agent: *` 블록은 `/w/`, `/api/`, `/wiki/Special:` 등을 금지하고 **`/wiki/` 일반 문서는 허용**합니다. Wikinews 본문은 CC BY 2.5 라이선스로 재사용이 명시적으로 허용됩니다. |

명시적으로 **제외**한 후보: `www.bbc.co.uk`(robots.txt가 스크래핑·요약·AI 학습을 명시적으로 금지),
`text.npr.org`(`Disallow: /`).

구현상 지켜지는 규칙:

- `respect_robots_txt: true`(기본값)이면 도메인별 `robots.txt`를 한 번 읽어 캐시하고,
  금지된 URL은 요청 자체를 하지 않고 `RobotsDisallowed`를 발생시킵니다.
- **차단 우회를 하지 않습니다.** UA 위장, IP 우회, 캡차 우회 코드는 없습니다.
- 기사 상세 요청 사이에 `request_delay_seconds`(기본 1초) 이상 대기합니다.
  robots.txt에 `Crawl-delay`가 있으면 둘 중 큰 값을 사용합니다.
- 식별 가능한 User-Agent를 보냅니다. **실사용 전에 `config.json`의 `user_agent`에
  본인 연락처를 넣으세요.**
- `--limit`은 기본 20이고, 처음에는 3~5 정도의 작은 값으로 시작하길 권장합니다.

수집한 콘텐츠의 저작권은 각 발행처에 있습니다. 재배포 시 각 사이트의 이용약관과
라이선스를 확인하세요.

## 12. 결과물 위치

| 종류 | 경로 |
| --- | --- |
| SQLite DB | `data/news.db` |
| 로그 | `logs/app.log` (1MB 단위 rotating, 백업 3개) |
| 차트 | `output/charts/category_counts.png`, `output/charts/daily_collection_trend.png` |
| 리포트 | `output/reports/report_<UTC timestamp>.md` 또는 `.txt` (`--output`으로 변경 가능) |
| 내보내기 | `output/exports/news_<UTC timestamp>.csv|jsonl|xlsx` (`--output`으로 변경 가능) |

차트는 한글 폰트(Malgun Gothic, NanumGothic, AppleGothic 등)를 자동 탐색해 적용하고,
찾지 못하면 WARNING 로그를 남긴 뒤 matplotlib 기본 폰트로 fallback합니다.
데이터가 0건이어도 예외 없이 "표시할 데이터가 없습니다" 안내가 담긴 PNG를 생성합니다.

CSV는 Excel에서 한글이 깨지지 않도록 `utf-8-sig`(BOM 포함)로 저장하고,
JSONL은 `ensure_ascii=False`로 한글을 그대로 씁니다.

## 13. 테스트

```bash
python -m pytest -v
python -m pytest --cov=news_cli --cov-report=term-missing
```

테스트는 **실제 네트워크와 실제 AI API를 호출하지 않습니다.**

- RSS/HTML은 `tests/fixtures/`의 고정 파일로 파싱을 검증합니다.
- HTTP 계층은 가짜 `Session`으로 timeout·HTTP 오류·robots 차단·요청 간 delay를 검증합니다.
- AI는 `FakeAIClient`로 성공/실패/JSON 파싱 오류를 검증합니다.
- OpenAI SDK 경로는 `127.0.0.1`에 띄운 OpenAI 호환 stub 서버로 왕복을 검증합니다
  (`tests/test_ai_client_local_server.py`).

## 14. 알려진 제한

- **검증한 공급자는 Gemini 하나뿐입니다.** 다른 OpenAI 호환 API는 실행해 보지 않았고,
  `analyze`가 보내는 `response_format: {"type": "json_object"}`를 지원하지 않는
  모델에서는 실패할 수 있습니다.
- rate limit·토큰 한도 초과 같은 이상 경로는 미검증이며, 실패 시 재시도하지 않습니다.
- 요약·분석 품질은 모델과 프롬프트에 좌우됩니다. 프롬프트는 "기사에 없는 사실 추가 금지"를
  명시하지만, 모델의 환각을 코드로 완전히 막지는 못합니다.
- 크롤링은 **정적 HTML만** 지원합니다. JavaScript로 렌더링되는 사이트는 수집할 수 없습니다.
- 날짜 파서는 ISO 8601, RFC 2822, 영문 날짜 등 일반적인 형식만 다룹니다. 해석하지 못하면
  WARNING을 남기고 `published_at`을 비운 뒤, 날짜 필터는 `collected_at`으로 대체합니다.
- 중복 판정의 실질적 기준은 canonical URL입니다. `content_hash`는 저장·조회는 되지만
  자동 병합에는 쓰이지 않습니다.
- `analyze`는 한 번에 최대 `max_analysis_articles`건 / `max_analysis_chars`자까지만
  입력합니다. 그 이상은 잘립니다(WARNING 로그).
- 재시도(backoff) 로직은 없습니다. 실패한 항목은 다음 실행에서 다시 시도됩니다.
- 동시 실행은 고려하지 않았습니다(SQLite 단일 프로세스 가정).

## 15. 정기 실행 예시 (선택)

### Windows 작업 스케줄러

매일 오전 8시에 수집·정제를 실행하는 예시입니다.

```powershell
$py = "C:\Users\me\codyssey_A2-2\.venv\Scripts\python.exe"
$dir = "C:\Users\me\codyssey_A2-2"

schtasks /Create /TN "AI News Fetch" /SC DAILY /ST 08:00 /F `
  /TR "cmd /c cd /d $dir && $py main.py fetch --method rss --source default_rss --limit 20 && $py main.py clean"
```

GUI로 만들 경우: 프로그램은 `.venv\Scripts\python.exe`, 인수는
`main.py fetch --method rss --source default_rss --limit 20`,
시작 위치는 프로젝트 루트로 지정합니다. AI 명령을 걸려면 작업 계정의
시스템 환경변수에 `AI_API_KEY`를 등록해야 합니다.

### cron (macOS / Linux)

```cron
# 매일 08:00 수집 + 정제
0 8 * * * cd /home/me/codyssey_A2-2 && .venv/bin/python main.py fetch --method rss --source default_rss --limit 20 && .venv/bin/python main.py clean

# 매일 08:30 요약 + 분석 + 리포트 (AI_API_KEY 필요)
30 8 * * * cd /home/me/codyssey_A2-2 && AI_API_KEY="sk-..." .venv/bin/python main.py summarize --unsummarized --limit 20 && AI_API_KEY="sk-..." .venv/bin/python main.py analyze --limit 30 && .venv/bin/python main.py report --format md
```

키를 crontab에 직접 쓰는 대신 `/etc/environment`나 별도 env 파일을 읽는 래퍼 스크립트를
쓰는 편이 안전합니다.

## 16. 프로젝트 구조

```text
codyssey_A2-2/
├─ main.py                     # 진입점 (UTF-8 콘솔 설정 후 CLI 위임)
├─ config.example.json         # 커밋되는 예시 설정
├─ config.json                 # 로컬 설정 (.gitignore)
├─ requirements.txt
├─ pytest.ini
├─ README.md
├─ .gitignore
├─ news_cli/
│  ├─ cli.py                   # argparse 정의와 서브커맨드 핸들러
│  ├─ config.py                # 설정 로딩·검증
│  ├─ logging_config.py        # 콘솔 + rotating file 핸들러, 비밀값 redaction
│  ├─ models.py                # 데이터클래스와 시간 유틸
│  ├─ storage.py               # SQLite 스키마·저장·조회·집계
│  ├─ cleaner.py               # 텍스트/URL/날짜 정규화, 정제 파이프라인
│  ├─ ai_client.py             # AI 공급자 추상화 (여기에만 SDK 종속 코드)
│  ├─ summarizer.py            # 기사별 요약
│  ├─ analyzer.py              # 구조화 JSON 종합 분석
│  ├─ charts.py                # matplotlib 차트
│  ├─ reporter.py              # 지표 계산과 리포트 렌더링
│  ├─ exporter.py              # CSV/JSONL/XLSX
│  └─ collectors/
│     ├─ __init__.py           # fetch 오케스트레이션
│     ├─ base.py               # HTTP 계층 (timeout/delay/robots)
│     ├─ rss_collector.py
│     └─ web_collector.py
├─ tests/                      # pytest (fixture/mock 기반)
├─ data/  logs/                # 생성물 (.gitignore)
└─ output/{charts,reports,exports}/
```

## 17. 처음 실행하는 순서

```powershell
# 0) 가상환경 + 의존성
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# 1) 설정
Copy-Item config.example.json config.json

# 2) 테스트로 환경 확인
python -m pytest -v

# 3) 수집 (작은 limit부터)
python main.py fetch --method rss   --source default_rss --limit 3
python main.py fetch --method crawl --source default_web --limit 3

# 4) 정제
python main.py clean --duplicate-policy skip

# 5) AI 키 설정 후 요약 · 분석
$env:AI_API_KEY = "sk-..."
python main.py summarize --unsummarized --limit 2
python main.py analyze --date-from 2026-08-01 --date-to 2026-08-10 --limit 10

# 6) 리포트 · 내보내기
python main.py report --format md --top-n 5
python main.py export --format csv --status summarized
```

3~4단계까지는 AI 키 없이도 동작합니다. 5단계에서 키가 없으면 어떻게 설정하는지
안내하는 메시지와 함께 종료 코드 1로 끝납니다.