# Runtime 계약

## 역할

`runtime/`은 control workspace에 보존하는 장치 로컬 실행 상태이며, 구현될 worker의 기본 실행 위치는 Colima VM 위 Docker container다. 현재 구현에서는 worker와 상태 전이가 없고, container는 검증·CLI·테스트 runtime으로 사용한다. S09 mobile recovery outbox는 `runtime/`에 넣지 않고 실제 device-local 경로를 위한 control-side 계약만 정의했다. Vault note corpus와 Git bridge transport에 섞지 않는다.

```text
runtime/
├── staging
├── queue
├── quarantine
├── awaiting_remote_authorization
├── running
├── review
├── approved
├── applying
├── done
├── rejected
├── expired
├── failed
├── conflict
├── runs
├── receipts
├── locks
├── index
├── cache
└── logs
```

이 디렉터리들은 현재 비어 있으며 worker나 상태 전이는 구현되지 않았다.

## S09 mobile recovery outbox와의 경계

S09 outbox는 `On My iPhone/KnowledgeHub-Recovery/Outbox/JOB_ID/` 또는 iPad 대응 장치 경로에 보존되는 device-local payload를 모델링한다. 이는 control workspace의 `runtime/` durable state와 별도이며 현재 장치에 생성되지 않았다. control-side `OutboxStore`는 synthetic temporary root에서만 create-only `input.json`과 append-only event를 검증한다.

- outbox payload는 strict fields, UTF-8 byte count, SHA-256, deterministic target path와 sensitivity choice를 함께 가진다.
- recovery는 durable payload가 먼저 검증된 뒤의 offline/auth/push failure, cancellation, app exit/reboot을 대상으로 하며, 같은 ID·같은 digest는 no-op, 다른 digest는 conflict다.
- `remote_observed` 또는 `local_vault_transferred` receipt와 7일 조건이 없으면 cleanup하지 않고, 30일 초과 pending item은 stale warning만 낸다.
- 실제 device filesystem, Shortcuts, Working Copy, credential, sync topology는 S10에서 사용자 참여와 별도 승인을 받은 뒤 다룬다.

## 물리적 저장·실행 경계

- `runtime/`은 host workspace에서 container로 bind mount해 보존한다. container를 삭제하거나 다시 만들어도 receipt, journal, queue, conflict evidence가 사라지지 않아야 한다.
- Python, `uv`, `vaultctl`, worker와 project dependency는 image layer 또는 container filesystem 안에 둔다. host 전용 `.venv`를 canonical 실행 경로로 만들지 않는다.
- uv/build cache는 Colima 내부 named volume에 두고 `runtime/` durable evidence와 분리한다.
- host Python/`uv` 직접 실행을 허용하는 경우에도 동일 `pyproject.toml`/`uv.lock`과 path policy를 사용하며, 실행 surface를 evidence에 기록한다.
- Docker socket, host secret/keychain, SSH agent, provider network는 기본적으로 runtime container에 전달하지 않는다.

## Bind mount UID/GID

`KNOWLEDGEOS_UID`와 `KNOWLEDGEOS_GID`는 현재 실행 중인 호스트 계정의 숫자형 `id -u`/`id -g`와 같아야 한다. `Makefile`이 이 값을 자동 export하고 `ops/compose.yaml`은 값이 없을 때 `1000:1000`으로 추정하지 않고 fail closed한다. container user와 Colima 내부 uv cache tmpfs mount option이 같은 숫자를 사용해야 host bind mount와 cache에 root 또는 다른 사용자의 파일이 생기지 않는다.

2026-09-09에는 실제 계정이 `501:20`이었고 `1000:1000` fallback 때문에 permission 오류가 확인되었다. 후속 세션은 `make`를 canonical wrapper로 사용하며, 직접 Compose를 호출할 때도 현재 host UID/GID를 명시한다. cache 삭제·재생성은 이 문제의 기본 해결책이 아니다.

## Codex 세션 외부 접근 경계

이 실행 환경에서 직접 접근 가능한 surface는 Codex 앱, 이 workspace, Colima/Docker CLI·Compose·container뿐이다. Obsidian, 브라우저, Finder, Mail, Calendar, Slack, Teams, Working Copy, Shortcuts, `plutil`, `launchctl`과 기타 외부 애플리케이션은 직접 열거나 읽거나 쓰지 않는다. 필요한 실제 앱/device 검증은 사용자에게 가능 여부와 정확한 범위를 확인할 수 있도록 남기고, 사용자 확인 전에는 CUA·앱 CLI·AppleScript·connector를 사용하지 않는다.

## 파일시스템과 sync 경계

- `runtime/`과 모든 하위 디렉터리는 mode `0700`을 사용한다.
- 향후 생성하는 queue, prompt, proposal, diff, receipt 등 payload 파일은 mode `0600`을 사용한다.
- writer와 worker는 기본 `umask 077`로 시작한다.
- `runtime/`은 Git뿐 아니라 iCloud Drive, Obsidian Sync, Dropbox, OneDrive 등 어떤 동기화 root에도 넣지 않는다.
- 문서와 로그에는 secret, 인증 header, Keychain 출력, 실제 민감 source 본문을 기록하지 않는다.

## 상태 분류

| 분류 | 예 | 취급 |
|---|---|---|
| durable execution evidence | job manifest, proposal digest, approval, receipt | backup과 retention 필요; 임의 삭제 금지 |
| recovery state | queue, running, applying, conflict, quarantine | crash/reconcile가 재개하거나 격리할 수 있어야 함 |
| derived state | index, cache | canonical Markdown과 receipt에서 재생성 가능 |
| observability | runs, logs | 민감정보를 최소화하고 retention 적용 |

`runtime/` 전체를 Git ignore한다고 해서 모든 내용이 disposable이라는 뜻은 아니다. 특히 receipt와 remote authorization evidence는 로컬 backup 대상이다.

## 향후 전이 원칙

- immutable job과 mutable state marker를 분리한다.
- 같은 job ID·같은 digest는 idempotent하게 재개한다.
- 같은 ID·다른 digest, malformed input, path traversal, unknown action은 quarantine한다.
- approval 뒤 source, target, policy, schema hash가 바뀌면 apply를 실패시킨다.
- crash 중간 상태를 숨기지 않고 journal/receipt로 reconcile한다.
- worker는 암묵적으로 Git network command를 실행하지 않는다.

## 삭제와 reset

구체적인 retention, backup, reset 명령이 구현되기 전에는 `runtime/`을 일괄 삭제하지 않는다. 향후 reset은 다음을 명시적으로 나눠야 한다.

- 안전하게 재생성 가능한 cache/index
- 보존 기간을 적용할 log
- 복구와 감사에 필요한 receipt/job/proposal/authorization
