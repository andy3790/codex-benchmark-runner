# 설정 가이드

이 문서의 명령은 저장소 루트에서 실행합니다. [프로젝트 소개로 돌아가기](../README.md).

## 공개 설정과 로컬 설정

`config.json`은 미로 과제의 공개 예시입니다. 개인 PC의 CLI 경로, 작업 경로, 실험 조합은 `config.local.json`에 저장합니다. 로컬 설정과 실행 결과는 Git에서 제외됩니다.

```powershell
Copy-Item config.json config.local.json
python -m codex_bench --gui
```

CLI·GUI·`launch_gui.pyw`는 `--config`를 생략하면 현재 폴더의 `config.local.json`을 우선 사용하고, 없으면 `config.json`을 사용합니다. 더블클릭 실행은 런처가 있는 폴더를 기준으로 선택합니다. 파일 내용을 병합하는 방식이 아니므로 로컬 설정에는 전체 설정을 넣으세요. 명시적인 `--config`는 자동 선택보다 우선합니다.

| 파일 | 용도 |
| --- | --- |
| `config.json` | 미로 과제, 모델 3개 × 추론 강도 3개, 순차 실행 |
| `examples/config.maze-sol-low.json` | 미로 과제, `gpt-6.1-sol / low` 한 조합 |
| `examples/config.sample.json` | Python 정렬·합·중앙값 과제와 예시 채점기 |
| `*.local.json` | Git에 포함하지 않는 개인 설정 |

예시의 모델명은 계정별 사용 가능 여부를 보장하지 않습니다. 공개 설정의 `codex_executable`은 `codex`이며, 개인 PC에 설치한 정확한 CLI 경로는 로컬 설정에서 지정합니다. `scratch_dir`를 생략하면 저장소 밖의 시스템 임시 폴더를 사용합니다.

설정을 바꾸면 새 `output_dir`를 지정하세요. 기존 출력은 동결된 설정 지문으로 보호되므로 다른 과제나 설정으로 덮어쓰지 않습니다. 이전 실험을 보고서로 확인하려면 당시의 로컬 설정과 `--report-only`를 사용하세요.

## 전체 설정 항목

모든 상대 경로는 **설정 파일이 있는 폴더**를 기준으로 해석합니다. JSON은 UTF-8, UTF-8 BOM을 모두 읽습니다. 프롬프트는 UTF-8로 읽어 BOM을 제거하고 줄바꿈을 LF로 고정한 공통 사본을 모든 프로세스의 stdin에 동일하게 전달합니다.

| 키 | 기본값 / 의미 |
| --- | --- |
| `models` | 필수. 모델명 문자열 또는 `{"name":"...","reasoning_efforts":["low","high"]}` 목록 |
| `reasoning_efforts` | 모델별 값이 없을 때 `low`, `medium`, `high` |
| `repetitions` | 조합별 1회. 전체 카테시안 곱을 생성 |
| `mode` | `sequential`. `parallel`이면 `max_workers` 적용 |
| `max_workers` | 2. 최대 16, 순차 모드에서는 1 |
| `max_experiments` | 20. 전체 조합 수가 초과하면 부분 실행 없이 시작을 거부 |
| `run_timeout_seconds` | 모델 프로세스당 900초 |
| `total_timeout_seconds` | **명령 1회 실행**의 전체 벽시계 예산 7200초. 재개 시 새 예산을 사용하며 누적 active_seconds도 별도 기록 |
| `prompt_file` / `starter_dir` | `prompt.md` / `starter` |
| `output_dir` | `benchmark-results`. 과제·설정이 바뀌면 새 폴더 사용 |
| `scratch_dir` | 시스템 임시 디렉터리의 `codex-benchmark-workspaces`. 어떤 Git 저장소 아래에도 둘 수 없음 |
| `codex_executable` | `codex`. Windows npm 래퍼는 패키지 내부 네이티브 실행 파일로 자동 해석. `codex.exe` 직접 지정도 가능 |
| `isolation` | `restricted`. 아래의 권한 경계 참고 |
| `max_output_bytes` | 프로세스 stdout+stderr 감시 한도 16 MiB. 폴링 사이 출력으로 소폭 초과 가능 |
| `max_project_bytes` | 시작 파일/제출물 사본의 크기 한도 128 MiB. OS 디스크 할당량은 아님 |
| `grader.plugin_dir` | `graders/sample`. `plugin.py`와 필요한 모든 비공개 보조 파일을 이 폴더에 넣음 |
| `grader.test_timeout_seconds` | 테스트당 5초. Docker 시작 비용 포함 |
| `grader.timeout_seconds` | 플러그인 전체 120초 |
| `grader.options` | 플러그인 전용 객체. 예시는 `docker_image` 지원 |

지원하지 않는 모델명·추론 강도를 정적 목록으로 추정하지 않습니다. 각 실제 실행의 CLI 오류를 `unsupported_combination`으로 분류하고 원문 오류와 종료 코드를 보존합니다. 계정 인증·사용량 제한·기타 CLI 오류도 각각 기록합니다. 오류 분류는 메시지 기반 휴리스틱이므로 판단 근거는 원문에서 확인해야 합니다. 정확한 토큰 상한은 설정하거나 보장하지 않습니다.
