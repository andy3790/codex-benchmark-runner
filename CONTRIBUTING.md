# 개발 및 기여

저장소 루트에서 Python 3.11 이상으로 개발합니다. 실행기와 테스트는 Python 표준 라이브러리를 사용합니다. 미로 채점기의 신뢰 가능한 fixture 검증에는 Node.js가 필요합니다.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m unittest discover -v
python tools/check_repository.py
```

Linux에서는 가상환경의 Python 경로를 `.venv/bin/python`으로 바꿉니다. 화면이 없는 환경의 Tkinter GUI 테스트는 skipped가 될 수 있습니다. 실제 모델 호출과 제출 코드의 Docker 실행은 단위 테스트에 포함하지 않습니다.

## 변경과 검증

- 설정 예시는 개인 경로 없이 작성하고 `config.json` 또는 `examples/`에 둡니다. 개인 설정은 `*.local.json`으로 보관합니다.
- 실패·미지원·중단 결과와 누락된 사용량을 보존합니다. 미검증 기능을 성공으로 표시하지 않습니다.
- 후보 코드는 신뢰할 수 있는 호스트 플러그인에서 직접 실행하지 않고 격리된 executor를 사용합니다.
- 실제 Codex 종단 간 시험은 계정 소유자가 명시적으로 요청했을 때만 실행합니다. 일반 PR 검증에는 모의 실행을 사용합니다.

GitHub Actions는 Windows/Ubuntu의 Python 3.11·3.12에서 저장소 검사, 테스트, 합성 보고서 생성과 패키지 빌드를 실행합니다. Codex 설치·로그인·모델 호출 단계는 없습니다. 워크플로는 [GitHub Actions checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python), [setup-node](https://github.com/actions/setup-node)의 공식 사용 예시를 따릅니다.

## 공개 파일 구성

`tools/check_repository.py`는 Git이 추적하는 파일과 Git에서 무시하지 않는 새 파일을 대상으로 개인 홈 경로, 알려진 키 형식, 로컬 설정, 대용량 파일, 문서 링크 및 공개 설정을 검사합니다. Git이 있는 작업 사본에서 실행하세요. 검사에 통과해도 모든 종류의 비밀 정보를 자동으로 판별한다는 뜻은 아니므로 변경 내용은 직접 검토합니다.

소스 배포 구성은 `MANIFEST.in`, Python 패키지 구성은 `pyproject.toml`에 있습니다. 실행 결과·설치 바이너리·인증 파일은 배포하지 않습니다. 실제 결과를 공유하려면 공개할 내용을 확인한 뒤 별도의 정제된 예시로 추가합니다.
