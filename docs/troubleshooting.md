# 환경 진단과 문제 해결

이 문서의 명령은 저장소 루트에서 실행합니다. [프로젝트 소개로 돌아가기](../README.md).

## 결과 폴더에 manifest가 없는 경우

`output_dir contains files without a manifest`는 선택한 출력 폴더에 파일이 있지만 유효한 실험 manifest가 없다는 뜻입니다. 저장소 루트나 기존 파일이 든 폴더를 출력 경로로 지정하지 말고, 새로운 빈 폴더를 선택합니다. 실패 결과도 종료된 실험으로 보존되므로 수정 후 다시 측정할 때는 새 출력 폴더를 사용합니다.

## Windows Codex CLI


`npm install -g @openai/codex`로 설치해 사용할 수 있습니다. Windows npm은 `codex.cmd`/`codex.ps1` 실행 래퍼를 만들지만, 실행기는 이 래퍼를 셸로 실행하지 않고 **같은 npm 패키지에 포함된 실제 `codex.exe`를 자동으로 찾습니다.** GUI의 Codex 실행 파일과 `config.json`의 `codex_executable`은 기본값 `codex`로 두면 됩니다. 직접 지정한 네이티브 실행 파일 경로도 계속 지원합니다.

설치 후 새 터미널에서 다음 순서로 확인하세요. 기존 Codex 로그인은 그대로 사용하므로 이미 로그인했다면 두 번째 명령은 생략해도 됩니다. PowerShell에서는 `.cmd`를 명시하면 npm의 `.ps1` 실행 정책 문제를 피할 수 있습니다.

```powershell
codex.cmd --version
codex.cmd login
python -m codex_bench --doctor
```

GUI가 이미 열려 있었다면 프로그램을 다시 시작하고 **환경 진단**을 누릅니다. 진단의 `resolved_from`은 발견한 npm 래퍼, `executable`은 실제 실행할 네이티브 바이너리, `resolution: npm-native`는 자동 해석 결과입니다. CLI 설치 버전도 기록하며 PATH의 다른 Codex 설치로 조용히 대체하지 않습니다. npm의 관리 패키지 경로를 실행 프로세스에 전달하지만 인증 파일을 복사하거나 `CODEX_HOME`을 변경하지 않습니다.

`configuration_validated: true`는 실제 네이티브 CLI가 공통 권한·승인 설정의 문법과 타입을 파싱했음을 뜻합니다. 계정/모델의 사용 가능 여부나 OS 샌드박스 강제 적용까지 확인한 결과는 아닙니다. 이 검사를 지원하는 `app-server --print-startup-config` 기능이 없는 CLI는 업데이트 안내와 함께 실험 시작 전에 중단합니다. 설정 오류가 발생하면 9개 모델 조합을 연속 실패시키는 대신 진단 단계에서 원문 원인을 보여줍니다.

`FilesystemPermissionToml in permissions` 오류는 예전 실행기가 특수 키와 경로를 CLI의 점으로 구분한 키에 넣을 때 발생하던 문제입니다. 현재는 권한 프로필 전체를 단일 TOML inline table로 전달합니다. 이 오류로 끝난 실험도 종료된 실패 기록이므로, 수정 후에는 새 결과 폴더로 실행하세요. 기존 로그를 삭제하거나 조작할 필요는 없습니다.

Windows의 `unelevated ... cannot enforce split filesystem read restrictions` 오류는 숨겨진 채점 파일의 읽기 제한을 해당 샌드박스가 지원하지 않아 발생합니다. 실행기는 restricted 모드에서 `features.prefer_mxc=true`를 명시합니다. 호환되는 PC에서는 MXC를 사용하며, 지원되지 않는 경우 격리를 완화하지 않고 사전 검사에서 중단합니다. 필요한 PowerShell·Node·Python·rg 설치 폴더만 읽기 전용으로 허용하며, 채점기·실행기·결과 폴더 안의 실행 도구에는 이 예외를 적용하지 않습니다.

2026-10-27 Windows 검증에서 CLI **0.164.0**의 MXC 내부 파일 도우미가 `SYSTEMROOT` 누락으로 세션 초기화에 실패했습니다. 별도 설치한 **0.166.0-alpha.1**에서는 같은 과제를 생성·채점했습니다. 이 버전들은 당시의 검증 기록이며 현재 최신 버전을 뜻하지 않습니다. 셸 사전 검사만으로 내부 파일 도우미의 모든 호환성까지 보장하지 않습니다.

이 문제가 재현되면 전역 설치를 변경하는 대신 별도 폴더의 CLI를 선택할 수 있습니다. 당시 확인한 버전을 설치하는 명령은 다음과 같습니다.

```powershell
npm install --prefix .tools/codex-validation --no-audit --no-fund @openai/codex@0.166.0-alpha.1
```

`config.local.json`의 `codex_executable` 또는 GUI에서 `.tools/codex-validation/node_modules/.bin/codex.cmd`의 **절대 경로**를 선택합니다. 설치한 버전과 실제 네이티브 실행 경로가 결과에 기록되며 자동으로 다른 CLI를 선택하거나 업데이트하지 않습니다. 기존에 열어 둔 GUI는 설정을 다시 열거나 재시작하세요.

`Use the actual codex.exe/native binary, not a shell wrapper`는 이전 실행기의 npm 설치 인식 제한에서 발생하던 메시지이며, 현재 버전에서는 정상 npm 설치를 자동 처리합니다. 네이티브 선택적 의존성이 설치되지 않아 자동 검색에 실패하면 `npm install -g @openai/codex --include=optional`로 복구하거나 GUI에서 실제 `codex.exe`를 선택하세요. 임의의 사용자 정의 `.bat`/`.ps1` 실행은 지원하지 않습니다.

설치·로그인은 [OpenAI 공식 CLI 문서](https://learn.chatgpt.com/docs/cli), Windows npm 설치는 [Windows 배포 문서](https://learn.chatgpt.com/docs/enterprise/windows-deployment)를 참고하세요.
