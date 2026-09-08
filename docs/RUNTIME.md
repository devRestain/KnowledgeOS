# Runtime 계약

## 역할

`runtime/`은 Mac control workspace의 장치 로컬 실행 상태다. Vault note corpus와 Git bridge transport에 섞지 않는다.

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
