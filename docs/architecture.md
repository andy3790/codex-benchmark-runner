# 실행 구조와 채점기 인터페이스

이 문서의 명령은 저장소 루트에서 실행합니다. [프로젝트 소개로 돌아가기](../README.md).

## 권한 경계

기본 `restricted`는 **workspace 쓰기 정책을 상속**하는 Codex 권한 프로필입니다. 호스트 루트 읽기를 거부하고 최소 런타임, 필요한 Windows 실행 도구 설치 폴더의 읽기, 현재 작업 폴더의 읽기·쓰기만 허용합니다. 채점기·결과·설정 폴더·실행기 소스에는 별도 정확 경로 deny를 적용하고 명령 네트워크와 웹 검색도 끕니다. 이 모드에는 구형 `--sandbox`를 함께 전달하지 않습니다. 공식 문서에 따라 구형 옵션이 권한 프로필보다 우선하기 때문입니다. CLI 0.138.0 이상을 버전상 전제하고, 실제 플랫폼이 정책을 지원하지 못하면 CLI의 실패를 기록하며 **제약을 완화해서 재시도하지 않습니다.**

명시적으로 `"isolation": "workspace-write"`를 선택하면 호환용 `--sandbox workspace-write`를 사용합니다. 이 모드는 작업 폴더 밖의 쓰기와 명령 네트워크를 제한하지만 **외부 파일 읽기·정답 비밀성은 보장하지 않습니다.** 비공개 채점 과제에는 기본 restricted 모드를 사용하세요. 권한 프로필은 beta이며 Windows MXC/elevated/unelevated 구현의 지원 범위가 다릅니다. 최초 Codex 샌드박스 준비는 기존 CLI 설치에서 완료되어 있어야 하며 실행기가 관리자 권한 설정이나 방화벽 변경을 수행하지 않습니다.

독립 채점은 신뢰할 수 있는 호스트 플러그인과 후보 코드 실행을 분리합니다. 예시 플러그인은 후보를 Docker Linux 컨테이너에서만 실행합니다. 제출물 하나만 읽기 전용으로 마운트하고 네트워크, 호스트 디렉터리 접근, 쓰기 가능한 루트 파일시스템을 막습니다. 권한을 제거한 사용자, capability 제거, no-new-privileges, CPU·메모리·프로세스 개수·출력·시간 한도를 적용합니다. 정답은 호스트에서 비교하며 컨테이너 stdin에는 테스트 입력만 전달합니다. 중단·시간 초과 시 컨테이너도 별도로 정리합니다. 호스트 인증 파일이나 환경 변수를 컨테이너에 전달하지 않습니다.

플러그인은 사용자 신뢰 코드이며 그 자체를 악성 코드용 샌드박스로 취급하지 않습니다. 반드시 검토한 플러그인만 사용하세요. 컨테이너·Codex 샌드박스는 OS 구현을 신뢰하는 경계이며 커널 취약점까지 방어하는 VM은 아닙니다. 별도 사용자·VM이 필요한 공격적 평가에서는 그 격리 환경 안에서 실행기를 구동하세요. 작업 사본은 자동 삭제하지 않으므로 사용 후 디스크 정리는 사용자가 선택합니다.

## 토큰·점수·시간의 해석

- 총 토큰은 **input_tokens + output_tokens**입니다. 캐시 입력은 입력에, 추론 출력은 출력에 포함되므로 중복 합산하지 않습니다. 누락 필드는 `null`이며 CSV에서는 빈칸, HTML에서는 `—`입니다.
- 세션 최종 누적 사용량이 있으면 마지막 누적값을 사용합니다. 없으면 서로 다른 `turn.completed`의 최종 사용량을 합칩니다. 이것도 없으면 명시적 `usage.delta`만 합칩니다. 최종값·증분값·원본 rollout의 `last_token_usage`를 섞어 더하지 않습니다. 이벤트 ID와 turn ID가 제공되면 중복을 제거합니다.
- 알려지지 않은 이벤트 형식의 사용량은 추정하지 않고 경고와 원본에 남깁니다. 일부 turn에서 특정 지표가 빠지면 그 세션의 해당 합계도 null입니다. 잘린/깨진 JSONL은 측정이 부분적일 수 있다는 경고를 남깁니다. 이벤트 ID 없이 완전히 같은 이벤트가 재전송되는 경우 등 의미를 판별할 수 없는 CLI 출력은 완전한 중복 제거를 보장하지 않습니다.
- `observed_configuration`은 CLI가 실제 모델/추론 메타데이터 이벤트를 제공한 경우에만 채웁니다. 비어 있으면 적용을 독립 확인할 수 없다는 뜻입니다. 요청 설정은 별도로 항상 보존합니다. 명령 인수만으로 실제 서버 적용을 확인했다고 표시하지 않습니다.
- 캐시 비율 = cached_input/input, 추론 비율 = reasoning_output/output입니다. 효율은 점수/1,000 총 토큰과 점수/생성 초입니다. 0 분모 또는 지표 누락 시 null입니다.
- 생성 시간은 모델 프로세스의 시작부터 종료까지 벽시계 시간이며 채점 시간은 따로 기록합니다. 보고서 `duration_seconds`는 두 시간의 합입니다. 파일 사본·보고서 비용은 manifest의 전체 active_seconds에 포함됩니다.
- 각 조합의 평균·최소·최대·모집단 표준편차는 측정값으로 계산하고 n과 missing을 함께 표시합니다. 실패·시간 초과·미지원·미완료 실험도 전체 표에 포함합니다. 없는 점수를 임의의 0점으로 넣지 않으므로 평균과 실패율을 함께 해석해야 합니다.
- 통과율의 분모는 실제 수행/검증한 테스트입니다. skipped는 제외하고 건수와 계획 만점을 별도로 보존합니다. 성능 테스트 시간에는 Docker 시작 비용이 포함되므로 정밀한 알고리즘 시간만 측정하는 값은 아닙니다. 병렬 모드는 CPU·서비스 사용량 간섭이 있으므로 기본값은 순차입니다.
- 캐시를 강제로 비우거나 서버 샘플링·서비스 부하를 고정하지 않습니다. 캐시 사용량, 반복 편차, 계정/CLI 버전 등의 한계를 함께 해석해야 합니다. 이미지 재현성이 중요하면 `docker_image`에 불변 digest를 지정하세요.

## 채점기 작성

`maze_prompt.md`용 미로 과제 채점기는 `graders/maze`에 있습니다. 기본 공개 설정에 연결했으며, 상세 배점·자동 검증 범위·미검증 UI 항목은 [미로 채점기 설명](../graders/maze/README.md)을 참고하세요. 제출 코드는 준비된 Docker Node 이미지에서만 실행합니다. 기존 결과물만 검사하려면 `python tools/grade_maze.py <프로젝트 폴더> --output <새 결과 폴더>`를 사용하면 됩니다. 이 명령은 모델을 호출하지 않습니다.

인터페이스는 `codex_bench/grading.py`의 `Grader`, `GradeContext`, `TestResult`입니다. 플러그인 폴더의 `plugin.py`는 다음 함수를 내보냅니다. 폴더의 다른 데이터 파일은 `context.plugin_dir`로 읽습니다. 의존하는 보조 파일도 같은 폴더에 두면 실험 시작 전에 모두 고정됩니다.

```python
from codex_bench.grading import TestResult, aggregate

def grade(submission, context):
    # 정적 예시: 실제 후보 코드를 import하거나 호스트에서 실행하지 않음
    present = (submission / "solution.py").is_file()
    return aggregate([
        TestResult("entrypoint", "structure",
                   "passed" if present else "failed",
                   1 if present else 0, 1,
                   detail="Checked file existence only"),
        TestResult("functionality", "functionality", "skipped", None, 0,
                   detail="This plugin does not execute code"),
    ])
```

플러그인은 `schema_version=1`, `source=independent_grader`, `tests`를 포함한 JSON 직렬화 가능 객체를 반환합니다. 각 테스트는 `name`, `category`, `status`, `score`, `max_score`, 선택적인 `duration_seconds`, `detail`을 갖습니다. `status`는 `passed`, `failed`, `error`, `skipped` 중 하나입니다. 미검증 항목은 반드시 `skipped`와 `score=null`을 반환합니다. 실행기는 음수·NaN·초과 점수·잘못된 상태를 거부하고 합계·통과율을 테스트 목록에서 다시 계산합니다.

후보 실행이 필요하면 `context.docker(submission).run(...)`을 사용하세요. API는 JSON 값을 stdin으로 인코딩하며 bytes를 전달하면 원문 입력을 사용합니다. `ok`, `error`, `skipped` 및 원본 출력·종료 코드·시간을 반환합니다. `subprocess` 또는 직접 `import solution`로 호스트에서 후보 코드를 실행하지 마세요. Python 이외의 과제는 적절한 Linux 이미지를 options로 지정하고 컨테이너 명령을 수정할 수 있습니다.

샘플 플러그인은 정렬·합·중앙값 과제에 대해 기능, 음수/중복/경계 알고리즘, 입력 오류 처리, 100,000개 성능을 검증하며 총 100점입니다. 메모리 최고 사용량은 실제 측정하지 않으므로 skipped입니다. 후보가 작성한 자체 테스트는 원본 이벤트/생성 프로젝트에만 보존하며 독립 점수에 합산하지 않습니다.
