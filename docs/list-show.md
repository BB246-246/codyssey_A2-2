# 데이터 조회 CLI — `list` / `show`

저장된 뉴스를 조건으로 걸러 보고(`list`), 한 건을 자세히 보는(`show`) 기능이다.
카테고리·날짜·키워드 필터와 페이지네이션을 지원한다.

---

## 1. 사용법

```bash
# 목록 조회
python main.py list
python main.py list --category IT
python main.py list --date-from 2026-08-01 --date-to 2026-08-10
python main.py list --keyword 반도체
python main.py list --status summarized
python main.py list --page 2 --page-size 20

# 필터는 조합할 수 있다 (AND 조건)
python main.py list --category IT --keyword AI --date-from 2026-08-01 --page 1 --page-size 5

# 상세 조회
python main.py show --id 5
```

### 옵션

| 명령 | 옵션 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `list` | `--category` | 전체 | 카테고리 완전 일치 |
| `list` | `--date-from`, `--date-to` | 전체 | `YYYY-MM-DD`. 양끝 포함 |
| `list` | `--keyword` | 없음 | 제목 **또는** 본문 부분 일치 |
| `list` | `--status` | `all` | `all` / `summarized` / `unsummarized` |
| `list` | `--page` | 1 | 1부터 시작 |
| `list` | `--page-size` | 10 | 한 페이지 건수 |
| `show` | `--id` | (필수) | `clean_articles.id` |

`--page`, `--page-size`는 `positive_int` 타입이라 0이나 음수를 주면 argparse가
종료 코드 2로 거부한다.

### 출력 예시

```text
$ python main.py list --keyword 반도체 --category IT --page-size 3
[list] 총 4건 / 1페이지 (전체 2페이지, 페이지당 3건)
  #   1 [요약O] 2026-08-09 (IT/default_web) 모노리식3D 특허공세...美ITC, SK하이닉스 2번째 특허침해조사 착수
  #   3 [요약O] 2026-08-09 (IT/default_web) "SK하이닉스, 4조원대 中 충칭공장 지분 매각 검토"
  #   4 [요약O] 2026-07-20 (IT/default_web) "기존 D램 갉아먹을라"...메모리 3사, CXL 자체 컨트롤러 개발 않기로
```

각 줄은 `#id`, 요약 여부(`요약O`/`요약X`), 날짜, `(카테고리/출처)`, 제목 순이다.

조건에 맞는 기사가 없거나 범위를 벗어난 페이지를 요청한 경우:

```text
$ python main.py list --page 99 --page-size 3
[list] 총 5건 / 99페이지 (전체 2페이지, 페이지당 3건)
  표시할 기사가 없습니다.
```

```text
$ python main.py show --id 1
제목      : 모노리식3D 특허공세...美ITC, SK하이닉스 2번째 특허침해조사 착수
URL       : https://zdnet.co.kr/view?no=20260810002022
출처/분류 : default_web / IT
발행/수집 : 2026-08-09T15:41:12+00:00 / 2026-08-10T05:12:03.114592+00:00
상태      : ok
모델/요약 : gemini-3.5-flash / 284자

[요약]
(AI가 생성한 한국어 요약 3~5문장)

[본문]
(정제된 본문. 2000자를 넘으면 잘라서 표시)
```

### 종료 코드

| 코드 | 상황 |
| --- | --- |
| 0 | 정상. **조회 결과가 0건이어도 0** (조회 실패가 아니라 "없음"이므로) |
| 2 | 잘못된 옵션 (`--page 0`, 잘못된 날짜 형식 등) |
| 3 | `show --id`로 지정한 기사가 없음 |

---

## 2. 구현 위치

CLI 계층과 조회 계층을 분리했다. CLI는 인자 해석과 출력만 하고, SQL은 전부
`storage.py`에 있다.

### CLI 계층 — `news_cli/cli.py`

| 역할 | 함수 / 위치 |
| --- | --- |
| `list` 옵션 정의 | `build_parser()` 내 `list_p` (약 174행) |
| `show` 옵션 정의 | `build_parser()` 내 `show_p` (약 182행) |
| `list` 핸들러 | `cmd_list()` (약 322행) |
| `show` 핸들러 | `cmd_show()` (약 347행) |
| 디스패치 등록 | `COMMANDS` 딕셔너리 (약 376행) |

`--date-from` / `--date-to` / `--category`는 `_add_filter_args()`(약 88행)로
정의되며 `analyze`, `report`, `export`와 공유한다. `list`는 여기에
`--keyword`, `--status`, `--page`, `--page-size`를 추가한다.

### 조회 계층 — `news_cli/storage.py`

| 역할 | 메서드 / 위치 |
| --- | --- |
| 필터 + 페이지네이션 쿼리 | `query_clean_articles()` (약 309행) |
| 전체 건수 (페이지 수 계산) | `count_clean_articles()` (약 374행) |
| 단건 상세 조회 | `get_clean_article()` (약 304행) |

> 행 번호는 작성 시점 기준이라 코드가 바뀌면 어긋날 수 있다. 함수 이름으로 찾는 편이 안전하다.

---

## 3. 동작 방식

### 3.1 날짜 필터

발행일이 없는 기사가 필터에서 통째로 빠지는 걸 막기 위해, 비교 기준을
`COALESCE(published_at, collected_at)`으로 잡는다.

```sql
WHERE COALESCE(published_at, collected_at) >= ?
  AND COALESCE(published_at, collected_at) <= ?
```

정렬도 같은 식을 쓰고, 같은 시각일 때는 `id`로 순서를 확정해 페이지 간
결과가 겹치거나 누락되지 않게 한다.

```sql
ORDER BY COALESCE(published_at, collected_at) DESC, id DESC
```

사용자가 넣는 `YYYY-MM-DD`는 `cli.date_bounds()`가 DB에 저장된 ISO 8601
문자열과 비교 가능한 경계값으로 바꾼다.

| 입력 | 변환 결과 |
| --- | --- |
| `--date-from 2026-08-01` | `2026-08-01T00:00:00+00:00` |
| `--date-to 2026-08-10` | `2026-08-10T23:59:59.999999+00:00` |

`--date-to`를 하루의 끝으로 밀어야 그날 오후에 발행된 기사가 포함된다.
DB의 모든 시각이 UTC ISO 8601로 통일돼 있어서 문자열 비교만으로 정확히 동작한다.

### 3.2 키워드 필터

제목과 본문을 함께 본다. 본문이 `NULL`인 기사에서 조건이 통째로 `NULL`이 되지
않도록 `COALESCE`로 감쌌다.

```sql
WHERE (title LIKE ? OR COALESCE(body, '') LIKE ?)   -- 둘 다 %keyword%
```

값은 파라미터 바인딩으로 넘긴다. 문자열을 SQL에 직접 이어붙이지 않으므로
따옴표가 든 검색어를 넣어도 인젝션이 되지 않는다.

### 3.3 페이지네이션

CLI가 페이지 번호를 offset으로 바꿔서 넘긴다.

```python
total  = storage.count_clean_articles(**filters)
offset = (args.page - 1) * args.page_size
rows   = storage.query_clean_articles(limit=args.page_size, offset=offset, order="DESC", **filters)
pages  = max(1, -(-total // args.page_size))   # 올림 나눗셈
```

- `count_clean_articles()`는 `limit`/`offset`만 제거하고 **나머지 필터는 그대로**
  적용해 센다. 그래서 헤더의 "총 N건"이 필터 조건과 항상 일치한다.
- 전체 페이지 수는 올림 나눗셈으로 구하고, 0건일 때도 `max(1, ...)`로 1페이지라고
  표시한다.
- 범위를 벗어난 페이지를 요청하면 빈 목록과 함께 "표시할 기사가 없습니다"를
  출력하고 정상 종료한다.

SQLite는 `OFFSET`만 단독으로 쓸 수 없어서, `limit`이 없을 때는
`LIMIT -1 OFFSET ?`(제한 없음 + 건너뛰기)로 처리한다.

### 3.4 상세 조회

`show`는 `id`로 한 건을 찾고, 없으면 종료 코드 3을 돌려준다. 본문은 콘솔이
넘치지 않도록 2000자에서 자르고 `...(생략)`을 붙인다.

요약이 없으면 `(요약 없음)`, 본문이 없으면 `(본문 없음)`처럼 빈 값을 그대로
비워두지 않고 표시한다.

---

## 4. 설계 판단

**SQL을 CLI에 두지 않았다.** `cmd_list`는 인자를 dict로 묶어 넘기고 결과를
찍는 일만 한다. 덕분에 `query_clean_articles()`를 `export`, `report`,
`analyze`, `summarize`가 그대로 재사용한다. `--status summarized` 같은 필터가
`list`와 `export`에서 다르게 동작할 여지가 없다.

**0건은 오류가 아니다.** 필터에 걸리는 게 없는 건 정상적인 조회 결과이므로
종료 코드 0을 준다. 반면 `show --id`로 콕 집어 지정한 기사가 없는 건 사용자의
착오이므로 3을 준다.

**정렬은 최신순 고정.** `list`는 `order="DESC"`로 호출한다. 목록을 볼 때는
최신 기사가 위에 오는 게 자연스럽다. `export`는 기본값인 오름차순을 쓴다.

---

## 5. 테스트

```bash
python -m pytest tests/test_cli.py tests/test_storage.py -v
```

| 테스트 | 파일 | 검증 내용 |
| --- | --- | --- |
| `test_cli_list_and_show` | `tests/test_cli.py` | 5건 중 `--page-size 2`로 2건만 출력, `--keyword` 적용, `show`가 본문 출력 |
| `test_pagination_offset` | `tests/test_storage.py` | 1페이지와 2페이지 결과가 겹치지 않음 |
| `test_date_and_category_filters` | `tests/test_storage.py` | 날짜 상·하한, 카테고리, 키워드 필터 |
| `test_show_missing_article_returns_no_data` | `tests/test_cli.py` | 없는 id → 종료 코드 3 |
| `test_negative_or_zero_limit_is_rejected` | `tests/test_cli.py` | 0·음수 페이지 값 거부 |
| `test_unknown_status_filter_raises` | `tests/test_storage.py` | 잘못된 status 값 거부 |

---

## 6. 알려진 제한

- 키워드 검색은 `LIKE '%...%'`라서 인덱스를 타지 못한다. 기사 수가 수만 건을
  넘어가면 느려진다. 그때는 SQLite FTS5 가상 테이블로 바꾸는 게 맞다.
- `--category`는 완전 일치다. 부분 일치나 다중 선택은 지원하지 않는다.
- `count_clean_articles()`는 같은 조건으로 행을 전부 가져와 파이썬에서
  길이를 센다. 지금 데이터 규모에서는 문제가 없지만, `SELECT COUNT(*)`로
  바꾸는 편이 정석이다.
- offset 기반 페이지네이션이라 뒤쪽 페이지로 갈수록 느려지고, 조회 중에
  새 기사가 들어오면 페이지 경계가 밀릴 수 있다.
