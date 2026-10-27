# 상담 분석 인터페이스 사용법

## 1. 파일 구성

다음 파일을 같은 디렉터리에 둡니다.

```text
backend/
├─ counseling_analysis_interface.py
├─ prompt_maker_succeed_3_span_experiment.py
├─ case1_school_20turn.json
└─ 사용하는_백엔드_코드.py
```

분석 인터페이스는 `prompt_maker_succeed_3_span_experiment.py`를 내부적으로
호출합니다. 두 Python 파일의 위치를 분리하려면 Python import 경로를 별도로
설정해야 합니다.

## 2. 입력 형식

입력은 기존 테스트 케이스와 동일한 리스트입니다.

```python
conversations = [
    {"text": "Q:어릴 때 어디에서 자라셨어요?"},
    {"text": "A:시골에서 자랐어. 산도 가깝고 논도 많았지."},
    {"text": "Q:학교는 어떻게 다니셨어요?"},
    {"text": "A:친구들이랑 같이 걸어서 다녔어."},
]
```

입력 규칙은 다음과 같습니다.

- 최상위 값은 비어 있지 않은 `list`여야 합니다.
- 각 항목은 `{"text": "..."}` 형식의 `dict`여야 합니다.
- Q와 A가 한 쌍이 되도록 항목 수가 짝수여야 합니다.
- 홀수 번째 항목은 `Q:`로 시작해야 합니다.
- 짝수 번째 항목은 `A:`로 시작해야 합니다.

## 3. 메모리에 있는 상담 분석

```python
from counseling_analysis_interface import analyze


conversations = [
    {"text": "Q:무엇을 드셨어요?"},
    {"text": "A:빨간 사과를 먹었어."},
]

result = analyze(conversations)

nouns = result["nouns"]
relations = result["relations"]
```

`analyze()`의 반환값은 JSON으로 바로 변환할 수 있는 Python `dict`입니다.
원본 `imagine`, `Relation` 클래스 인스턴스가 아니라 직렬화된 객체 정보가
들어 있습니다.

## 4. JSON 파일 분석

```python
from counseling_analysis_interface import analyze_file


result = analyze_file("case1_school_20turn.json")
```

JSON 파일의 내용도 2절에서 설명한 Q/A 리스트 형식이어야 합니다.

## 5. 반환 구조

최상위 반환값에는 `nouns`와 `relations`만 포함됩니다.

```json
{
  "nouns": [],
  "relations": []
}
```

### 명사 객체

```json
{
  "noun": "사과",
  "adjectives": ["빨갛다"],
  "positions": [],
  "moderators": [],
  "target": null,
  "relative_clauses": []
}
```

### 관계 객체

```json
{
  "subjects": ["나"],
  "verb": "먹다",
  "object": "사과",
  "direct_objects": ["사과"],
  "indirect_objects": [],
  "object_complements": [],
  "context": [],
  "reason_clauses": [],
  "polarity_bit": 1,
  "negation": null,
  "display": "(나 먹다 사과)"
}
```

`polarity_bit`의 의미는 다음과 같습니다.

- `1`: 긍정
- `0`: 부정

## 6. 결과를 JSON 파일로 저장

```python
import json
from pathlib import Path

from counseling_analysis_interface import analyze


result = analyze(conversations)

Path("analysis_result.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

`ensure_ascii=False`를 사용해야 한글이 `\uXXXX` 형태로 바뀌지 않고 그대로
저장됩니다.

## 7. 외부에서 받은 JSON 문자열 처리

```python
import json

from counseling_analysis_interface import analyze


received_json = """
[
  {"text": "Q:무엇을 드셨어요?"},
  {"text": "A:빨간 사과를 먹었어."}
]
"""

conversations = json.loads(received_json)
result = analyze(conversations)

response_json = json.dumps(
    result,
    ensure_ascii=False,
)
```

## 8. FastAPI 적용 예시

```python
from typing import Any

from fastapi import FastAPI

from counseling_analysis_interface import analyze


app = FastAPI()


@app.post("/analyze")
def analyze_counseling(conversations: list[dict[str, Any]]):
    return analyze(conversations)
```

요청 본문:

```json
[
  {"text": "Q:무엇을 드셨어요?"},
  {"text": "A:빨간 사과를 먹었어."}
]
```

FastAPI는 반환된 `dict`를 자동으로 JSON 응답으로 변환하므로 별도로
`json.dumps()`를 호출할 필요가 없습니다.

## 9. 입력 오류 처리

형식이 잘못되면 `CounselingInputError`가 발생합니다.

```python
from counseling_analysis_interface import (
    CounselingInputError,
    analyze,
)


try:
    result = analyze(conversations)
except CounselingInputError as error:
    print(f"잘못된 상담 입력: {error}")
```

## 10. 실행 및 상태 관리 주의사항

`counseling_analysis_interface`는 백엔드 프로세스가 시작될 때 한 번 import하고,
요청마다 `analyze()`만 호출하는 방식이 효율적입니다.

```python
from counseling_analysis_interface import analyze


def handle_request(conversations):
    return analyze(conversations)
```

현재 분석기는 전역 Kkma/Okt 인스턴스를 사용합니다. 상담을 딱 한 건만
분석하거나 상담마다 프로세스를 종료한다면 추가 처리가 필요하지 않습니다.

동일한 프로세스에서 서로 다른 상담을 계속 분석할 경우에는 이전 상담에서
추론한 사람 이름이 `INFERRED_PERSON_NAMES`에 남을 수 있습니다. 여러 상담을
연속 처리하는 구조로 확장할 때는 상담별 상태 초기화 또는 요청별 분석 상태
분리가 필요합니다.

동시 요청, 작업 큐 및 멀티프로세스 처리는 현재 인터페이스의 필수 조건이
아니며 필요할 때 별도로 추가할 수 있습니다.

## 11. 간단한 동작 확인

```python
from counseling_analysis_interface import analyze_file


result = analyze_file("case1_school_20turn.json")

print(type(result))
print(type(result["nouns"]))
print(type(result["relations"]))
print(len(result["nouns"]))
print(len(result["relations"]))
```

예상 타입:

```text
dict
list
list
```
