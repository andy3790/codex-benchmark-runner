# Codex Benchmark Runner

여러 Codex 모델과 추론 강도의 개발 성능을 같은 프롬프트·시작 파일·독립 채점기로 비교하는 로컬 벤치마크 실행기입니다. CLI와 Tkinter GUI를 제공하며, 기존 Codex CLI 인증을 사용합니다. Python 실행기에는 외부 Python 패키지가 필요하지 않습니다.

## 주요 기능

- 모델·추론 강도·반복 조합 생성과 순차/병렬 실행
- 독립 작업 폴더, 실행 시간 제한, 중단 및 미완료 작업 재개
- JSONL 원본, stdout/stderr, 최종 메시지, 생성 프로젝트 보존
- 캐시·추론 하위 항목을 중복 합산하지 않는 토큰 집계와 누락값 보존
- 별도 플러그인 채점, 오프라인 HTML·CSV·JSON 보고서
- 실제 모델을 호출하지 않는 모의 실행과 dry-run

## 실행 환경

| 항목 | 용도 |
| --- | --- |
| Windows 10/11, Python 3.12 권장 | 주 실행 환경. Python 3.11 이상 지원 |
| Codex CLI와 기존 로그인 | 실제 코드 생성 |
| Python의 Tkinter | 데스크톱 GUI. GUI를 쓰지 않으면 선택 사항 |
| Docker Desktop Linux 엔진 | 제공 채점기의 제출 코드 격리 실행 |
| Node.js | 개발 시 신뢰 가능한 미로 테스트 fixture 검증 |

Docker가 없으면 제공 채점기의 동적 검사는 `skipped`로 남습니다. 독립 채점에 사용하는 Node/Python은 컨테이너 안에서 실행하며, 호스트에서 제출 코드를 실행하는 대체 모드는 제공하지 않습니다. 정적 검사만 하는 사용자 플러그인은 Docker 없이 작성할 수 있습니다.

## 빠른 시작

저장소 루트에서 공개 설정을 로컬 설정으로 복사하고 모델 조합·CLI 경로·출력 경로를 편집합니다.

```powershell
Copy-Item config.json config.local.json
python -m codex_bench --gui
```

Windows에서는 `launch_gui.pyw`를 더블클릭해도 됩니다. GUI를 여는 것만으로 모델이 호출되지는 않습니다. **모의 실행 · 토큰 0** 버튼으로 기록·채점 프로토콜·보고서 흐름을 먼저 확인할 수 있습니다. 모의 점수와 토큰은 합성 데이터입니다.

기본 과제는 [미로 생성·탐색 웹 앱](maze_prompt.md)입니다. 제공 채점기를 실행하기 전에 이미지를 준비합니다.

```powershell
docker pull node:22-bookworm-slim
```

CLI로 실제 실행하거나 재개하는 명령입니다. 실제 Codex 계정 사용량이 발생합니다.

```powershell
python -m codex_bench
```

설정을 자동 선택할 때는 `config.local.json`이 우선이며, 없으면 공개 `config.json`을 사용합니다. `--config`로 지정한 파일은 항상 우선합니다. 예를 들어 **한 조합만** 실행하려면 다음 공개 예시를 선택합니다.

```powershell
python -m codex_bench --config examples/config.maze-sol-low.json
```

모든 상대 경로는 설정 파일의 폴더를 기준으로 해석합니다. 예시 모델의 계정별 사용 가능 여부는 실제 CLI가 판정합니다. Windows 샌드박스와 npm CLI 설치 문제는 [문제 해결 문서](docs/troubleshooting.md)를 참고하세요.

## 결과 확인과 개발 검증

결과는 설정의 `output_dir`에 저장됩니다. `report.html`은 인터넷 없이 열 수 있으며 정렬·검색 표, 비교 그래프, 반복 통계와 원본 기록 링크를 제공합니다. 실패·미완성 실행도 포함합니다.

```powershell
python -m codex_bench --doctor
python -m codex_bench --dry-run
python -m unittest discover -v
python tools/make_sample_report.py
python tools/check_repository.py
```

이 명령들은 실제 모델을 호출하지 않습니다. doctor/dry-run은 네이티브 CLI와 임시 샌드박스 파일을 검사하며, 테스트와 샘플 보고서는 모의 데이터를 사용합니다. [샘플 HTML](examples/sample-report/report.html)은 합성 데이터로 만든 결과 형식 예시입니다.

선택적으로 `python -m pip install -e .`를 실행하면 `codex-bench`, `codex-bench-gui` 명령을 사용할 수 있습니다. 채점기·프롬프트·설정 예시는 저장소에 함께 있으므로 저장소 루트에서 시작하거나 `--config`로 해당 파일을 지정하세요.

## 프로젝트 구성

```text
codex_bench/             실행기·GUI·토큰 파서·채점 인터페이스·보고서
config.json             개인 경로가 없는 공개 미로 설정
maze_prompt.md          미로 과제의 공통 프롬프트
prompt.md, starter/     Python 예제 프롬프트와 공통 시작 파일
examples/               과제별 설정 예시와 합성 보고서
graders/maze/           미로 과제 독립 채점기
graders/sample/         Python 과제 독립 예시 채점기
tests/                  오프라인 단위·모의 통합 테스트와 fixtures
tools/                  샘플 보고서·저장된 미로 채점·저장소 검사
docs/                   설정·사용법·구조·문제 해결·검증 기록
.github/workflows/      모델 호출 없는 자동 테스트
```

`*.local.json`, `benchmark-results*/`, `.tools/`, `.test-data/`, 가상환경과 캐시는 로컬 데이터이며 Git과 배포 소스에서 제외합니다. 기존 실행 결과와 개인 설정은 이 폴더들에 보존할 수 있습니다.

## 문서

- [GUI·CLI 사용 및 재개](docs/usage.md)
- [설정 파일과 과제별 예시](docs/configuration.md)
- [격리·토큰 집계·채점기 인터페이스](docs/architecture.md)
- [미로 채점 배점과 미검증 범위](graders/maze/README.md)
- [Windows 환경 진단과 문제 해결](docs/troubleshooting.md)
- [검증 방법과 기존 실제 실행 기록](docs/validation.md)
- [개발 및 기여 안내](CONTRIBUTING.md)
