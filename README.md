# Codex Benchmark Runner

Windows 10/11과 Python 3.12를 대상으로 만든 재사용 가능한 로컬 Codex CLI 벤치마크 실행기입니다. Python 런타임 의존성은 표준 라이브러리뿐이며 Python 3.11에서도 동작합니다. 별도 API 호출·LLM 채점·API 키 설정 없이 현재 사용자의 Codex CLI 로그인을 사용합니다.

## 데스크톱 GUI

Windows에서 `launch_gui.pyw`를 더블클릭하거나 프로젝트 폴더에서 다음 명령을 실행하세요. GUI를 여는 것만으로 모델이 호출되지는 않습니다.

```powershell
python -m codex_bench --gui
```

다른 설정을 열려면 `python -m codex_bench --gui --config C:\bench\config.json`을 사용하거나 창의 **설정 열기**를 누릅니다. `python -m codex_bench.gui`도 지원합니다. 선택적으로 패키지를 설치하면 콘솔 창 없이 실행하는 `codex-bench-gui` 명령을 사용할 수 있습니다. Python 기본 배포판에 포함된 Tkinter를 사용하며 별도 GUI 패키지는 필요하지 않습니다. Tkinter를 제외한 Python 설치나 화면이 없는 서버에서는 기존 CLI를 사용하세요.

왼쪽 탭에서 모델별 추론 강도, 반복/병렬 실행, 경로와 채점기, 시간/출력 한도, 공통 프롬프트를 편집합니다. 모델의 **추가·수정·삭제**와 전체 조합 수 미리보기를 지원합니다. 오른쪽 표에는 완료/실패/실행 중 개수, 각 실행의 점수·토큰·시간을 표시하고, 하단에는 실시간 로그와 선택한 실험의 상세 기록을 표시합니다. 실행 중에도 창은 응답하며, 좁은 창에서는 설정을 스크롤하고 결과·로그 구분선을 조절할 수 있습니다.

| 버튼 | 동작 |
| --- | --- |
| 설정·프롬프트 저장 | 입력을 검증한 뒤 저장. 외부에서 바뀐 파일은 덮어쓰지 않음 |
| 환경 진단 | 로컬 Codex 버전·도움말을 확인. 모델 호출 없음 |
| dry-run | 현재 편집 내용으로 예정 조합과 명령을 표시. 설정/결과 파일을 쓰거나 모델을 호출하지 않음 |
| 모의 실행 · 토큰 0 | 가짜 Python 프로세스와 합성 채점기로 전체 실행·기록·보고서 흐름을 검증. Codex/Docker/후보 코드 실행 없음 |
| 실제 실행 / 재개 | 설정과 프롬프트를 저장하고 기존 실행기로 실행. 이 버튼은 실제 Codex 사용량을 발생시킴 |
| 중단 | 새 작업을 멈추고 실행 중인 프로세스 트리를 종료한 뒤 기록 보존 |
| HTML 보고서 열기 | 최근 생성한 로컬 HTML을 기본 브라우저로 열기 |

모의 결과는 실제 `output_dir`의 형제 폴더 `<output_dir 이름>-mock/demo-<ID>/output/`에 저장됩니다. 기본 설정에서는 `benchmark-results-mock/`입니다. 실제 실험의 입력 지문이나 재개 상태를 변경하지 않습니다. 점수·토큰은 합성 데이터이고 시간은 모의 프로세스의 소요 시간이므로 실제 모델 성능을 나타내지 않습니다. 추론 강도에 `unsupported`를 추가하면 미지원 오류 기록도 모의할 수 있습니다. 모의 실행의 점수는 어떤 실제 기능도 검증했다고 주장하지 않습니다.

실제 실행은 기존 CLI와 같은 안전 정책, 독립 채점, 완료 작업 건너뛰기를 사용합니다. 설정을 바꾼 뒤 기존 결과 폴더로 재개하면 일관성 검사에서 거부되므로 새 결과 폴더를 지정하세요. 창을 실행 중에 닫으면 먼저 중단·저장을 완료하고 종료하며, 저장하지 않은 편집 내용은 별도로 확인합니다. 이미 완료된 실행 실패/미지원은 재개 시에도 건너뜁니다.

## 빠른 시작

프로젝트 폴더에서 `config.json`의 모델과 추론 강도를 계정에서 사용할 조합으로 수정합니다. 예시 모델 이름은 계정별 사용 가능 여부를 보장하지 않습니다. `prompt.md`와 `starter/`를 원하는 과제에 맞게 수정한 후 한 명령으로 실행합니다.

```powershell
python -m codex_bench --config config.json
```

같은 명령을 다시 실행하면 이어서 처리합니다. 결과는 `benchmark-results/report.html`, `results.csv`, `results.json`에 저장됩니다. HTML을 더블클릭하면 인터넷 연결 없이 그래프, 정렬·검색 표, 범주별 점수, 반복 통계, 원본 기록 링크를 볼 수 있습니다.

샘플 채점기로 실제 기능 검증을 하려면 Docker Desktop의 **Linux 컨테이너 엔진**과 미리 준비한 Python 이미지가 필요합니다. 이미지는 실행기가 자동으로 내려받지 않습니다. 최초 준비는 사용자가 다음 명령으로 할 수 있습니다.

```powershell
docker pull python:3.12-slim
```

Docker가 없거나 이미지가 준비되지 않으면 **후보 코드를 호스트에서 실행하지 않습니다.** 실행 가능한 테스트는 `skipped`, 점수와 통과율은 `null`로 남습니다. `solution.py`가 없는 경우처럼 실제로 확인한 실패는 0점입니다. 직접 정적 검증만 하는 플러그인을 작성하면 Docker 없이 채점할 수 있습니다.

모델 사용량 없이 검증하는 명령:

```powershell
python -m codex_bench --doctor
python -m codex_bench --dry-run
python -m unittest discover -v
python tools/make_sample_report.py
```

`--doctor`와 `--dry-run`은 로컬 `--version`·`--help`만 호출합니다. 실제 `codex exec` 작업, 로그인 변경, 모델 가용성 조회는 하지 않습니다. dry-run은 결과·작업 폴더도 생성하지 않습니다. 샘플 생성기는 합성 데이터를 사용하며 실제 모델 성능을 나타내지 않습니다. 미리 생성된 예시는 `examples/sample-report/report.html`에 있습니다.

설치 없이 `python -m codex_bench`로 사용할 수 있습니다. 선택적으로 `python -m pip install -e .`를 실행하면 `codex-bench` 명령도 사용할 수 있습니다. Python 3.12가 여러 버전 중 하나로 설치된 Windows에서는 `py -3.12 -m codex_bench`를 사용해도 됩니다.

### Windows에서 npm으로 설치한 Codex CLI

`npm install -g @openai/codex`로 설치해 사용할 수 있습니다. Windows npm은 `codex.cmd`/`codex.ps1` 실행 래퍼를 만들지만, 실행기는 이 래퍼를 셸로 실행하지 않고 **같은 npm 패키지에 포함된 실제 `codex.exe`를 자동으로 찾습니다.** GUI의 Codex 실행 파일과 `config.json`의 `codex_executable`은 기본값 `codex`로 두면 됩니다. 직접 지정한 네이티브 실행 파일 경로도 계속 지원합니다.

설치 후 새 터미널에서 다음 순서로 확인하세요. 기존 Codex 로그인은 그대로 사용하므로 이미 로그인했다면 두 번째 명령은 생략해도 됩니다. PowerShell에서는 `.cmd`를 명시하면 npm의 `.ps1` 실행 정책 문제를 피할 수 있습니다.

```powershell
codex.cmd --version
codex.cmd login
python -m codex_bench --doctor
```

GUI가 이미 열려 있었다면 프로그램을 다시 시작하고 **환경 진단**을 누릅니다. 진단의 `resolved_from`은 발견한 npm 래퍼, `executable`은 실제 실행할 네이티브 바이너리, `resolution: npm-native`는 자동 해석 결과입니다. CLI 설치 버전도 기록하며 PATH의 다른 Codex 설치로 조용히 대체하지 않습니다. npm의 관리 패키지 경로를 실행 프로세스에 전달하지만 인증 파일을 복사하거나 `CODEX_HOME`을 변경하지 않습니다.

`Use the actual codex.exe/native binary, not a shell wrapper`는 이전 실행기의 npm 설치 인식 제한에서 발생하던 메시지이며, 현재 버전에서는 정상 npm 설치를 자동 처리합니다. 네이티브 선택적 의존성이 설치되지 않아 자동 검색에 실패하면 `npm install -g @openai/codex --include=optional`로 복구하거나 GUI에서 실제 `codex.exe`를 선택하세요. 임의의 사용자 정의 `.bat`/`.ps1` 실행은 지원하지 않습니다.

설치·로그인은 [OpenAI 공식 CLI 문서](https://learn.chatgpt.com/docs/cli), Windows npm 설치는 [Windows 배포 문서](https://learn.chatgpt.com/docs/enterprise/windows-deployment)를 참고하세요.

## 설정

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

## 실행과 재개

각 조합·반복에 안정적인 `job-...` ID를 부여하고, 실제 시도마다 UUID를 새로 발급합니다. 새 프로세스에서 `codex exec --json`을 실행하며 모델, 추론 강도, 승인 정책 `never`를 명시합니다. `resume`·`fork`·과거 대화 ID는 전달하지 않습니다. `--ephemeral`로 CLI 세션 기록 저장을 끄며, 공유 데몬을 끄는 옵션이 있으면 사용합니다. 사용자 설정 파일을 무시하는 `--ignore-user-config`는 인증 저장소 위치를 바꾸거나 인증 파일을 복사하지 않습니다.

처음 시작할 때 프롬프트, 시작 파일, 채점 플러그인 전체를 고정하고 SHA-256 해시와 설정 지문을 기록합니다. 모든 후보는 이 시작 파일 사본에서 시작합니다. 시작 파일의 `.git`, `.codex`와 심볼릭 링크·Windows junction·reparse point는 거부합니다. 후보 작업 폴더는 실행기·채점기·결과와 별도로 생성합니다.

`Ctrl+C`를 누르면 새 작업 예약을 중지하고 실행 중인 프로세스 트리를 종료한 후 보고서를 작성합니다. Windows Job Object의 kill-on-close를 사용하며 프로세스를 Job에 넣지 못하면 실행을 실패시킵니다. Unix에서는 별도 프로세스 그룹을 종료합니다. 프로세스 종료·컨테이너 정리·사본 작성·보고서 저장에는 설정 시간 외의 짧은 정리 시간이 필요할 수 있습니다. 전원 차단·프로세스 강제 종료에서는 마지막 저장 시점 이후의 메타데이터가 없을 수 있지만 이미 기록된 원본 로그는 남습니다.

같은 명령으로 재개할 때:

- `succeeded`, `failed`, `unsupported`, `timed_out`은 종료된 실험이므로 건너뜁니다. 종료된 실패를 다시 측정하려면 새 `output_dir`를 사용합니다.
- `pending`, `running`, `interrupted`, `grading`은 미완료 상태입니다. 생성이 완료되어 제출물 해시까지 저장되었으면 모델을 다시 호출하지 않고 채점만 이어갑니다. 변경된 제출물은 재채점하지 않습니다.
- 생성 도중 중단되었으면 이전 로그·시도를 보존하고 동일 시작 파일에서 새 시도를 만듭니다. 모든 시도 이력은 JSON과 HTML에 남고, 조합 통계는 현재 시도 기준입니다.
- 설정·프롬프트·시작 파일·채점기 또는 고정 사본이 달라지면 기존 실험과 섞지 않고 재개를 거부합니다.
- 결과 폴더에 OS 파일 잠금을 걸어 동시에 두 실행기가 덮어쓰지 못하게 합니다. 저장은 임시 파일 교체로 수행합니다.

`--report-only`는 저장된 결과에서 보고서만 다시 생성합니다. 종료 코드 0은 모든 실행과 채점 프로토콜의 정상 종료, 1은 종료된 실행 실패 포함, 2는 설정·환경 오류, 130은 중단/전체 시간 제한을 뜻합니다. **정상 종료는 기능 테스트 만점을 뜻하지 않습니다.** 기능 검증은 `grade.tests`, `score`, `pass_rate`로 판단합니다.

## 권한 경계

기본 `restricted`는 **workspace 쓰기 정책을 상속**하는 Codex 권한 프로필입니다. 호스트 루트 읽기를 거부하고 최소 런타임 경로와 현재 작업 폴더만 허용합니다. 채점기·결과·설정 폴더·실행기 소스에는 별도 정확 경로 deny를 적용하고 명령 네트워크와 웹 검색도 끕니다. 이 모드에는 구형 `--sandbox`를 함께 전달하지 않습니다. 공식 문서에 따라 구형 옵션이 권한 프로필보다 우선하기 때문입니다. CLI 0.138.0 이상을 버전상 전제하고, 실제 플랫폼이 정책을 지원하지 못하면 CLI의 실패를 기록하며 **제약을 완화해서 재시도하지 않습니다.**

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

`maze_prompt.md`용 미로 과제 채점기는 `graders/maze`에 있습니다. 현재 설정 파일에 연결했으며, 상세 배점·자동 검증 범위·미검증 UI 항목은 [미로 채점기 설명](graders/maze/README.md)을 참고하세요. 제출 코드는 준비된 Docker Node 이미지에서만 실행합니다. 기존 결과물만 검사하려면 `python tools/grade_maze.py <프로젝트 폴더> --output <새 결과 폴더>`를 사용하면 됩니다. 이 명령은 모델을 호출하지 않습니다.

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

## 파일 구조

```text
codex_bench/            설정·CLI·프로세스·토큰 파서·채점 인터페이스·보고서
graders/sample/         독립 예시 채점기와 시작 전에 고정하는 비공개 테스트
tests/fixtures/        JSONL 이벤트, 네트워크 없는 가짜 Codex
tests/                 표준 unittest 단위/통합 테스트
tools/make_sample_report.py
examples/sample-report/ 합성 HTML/CSV/JSON 및 예시 원본 기록
config.json, prompt.md, starter/

benchmark-results/
  manifest.json, diagnostics.json, report.html, results.csv, results.json
  private/             고정한 config/prompt/starter/grader (후보에 제공하지 않음)
  runs/<job-id>/result.json
  runs/<job-id>/attempts/<uuid>/
    command.json, result.json, stdout.log, stderr.log, events.jsonl
    final_message.md, submission/, grading/
```

## 검증 범위

단위/통합 테스트는 가짜 Codex 프로세스와 고정 JSONL로 수행합니다. 순차/병렬 실행, stdout/stderr, 실패·미지원, 한글 프롬프트, 중복 없는 토큰, 누락값, 프로세스 자식 종료, 시간 초과·중단, 고정 입력 변경 탐지, 채점 중 재개, 오프라인 보고서/HTML escaping/Docker 제한 명령을 검사합니다. 실사용 모델 또는 Docker 후보 컨테이너를 테스트에서 호출하지 않습니다. 보고서 생성기와 테스트의 합성 점수는 제품 기능 검증 점수가 아닙니다.

개발 환경에서는 Windows에서 Python 3.11.9로 GUI·미로 채점기를 포함한 104개 테스트를 통과했고, 설치된 Codex CLI `0.163.0-alpha.11.2`의 버전/도움말 및 6개 조합 dry-run을 확인했습니다. GUI는 실제 Tk 위젯을 생성해 설정 편집·저장, 1060×720 창에서 주요 버튼/결과/로그 표시, 이벤트 루프를 통한 모의 실행·결과 갱신, 중단·보고서 보존을 검사했습니다. 미로 채점기는 신뢰 가능한 Node fixture와 의도적인 오류 변형으로 검증했으며, Docker 엔진이 실행되지 않는 환경에서 동적 검사가 skipped로 남는 경로도 확인했습니다. 보고서의 정렬·검색 스크립트는 브라우저/네트워크 없이 Node로 검사했고, 합성 결과의 로컬 링크를 확인했습니다. 브라우저 도구의 로컬 접근 제한 때문에 HTML 화면 렌더링 확인은 완료하지 못했습니다. Python 3.12, 실제 모델 호출, 실제 Docker 컨테이너 실행, 플랫폼 샌드박스 정책의 종단 간 적용 여부는 이 개발 세션에서 실행하지 않았습니다. 명시적으로 요청한 경우에만 유료/계정 사용량이 발생하는 종단 간 시험을 수행해야 합니다.

Codex 실행 옵션과 토큰 예시는 [OpenAI 공식 비대화형 실행 문서](https://learn.chatgpt.com/docs/non-interactive-mode), [CLI 명령 문서](https://learn.chatgpt.com/docs/developer-commands?surface=cli)를 따릅니다. 권한 프로필의 상속·우선순위·플랫폼 제한은 [공식 Permissions](https://learn.chatgpt.com/docs/permissions), [Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)에서 확인할 수 있습니다.
