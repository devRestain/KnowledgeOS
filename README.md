# KnowledgeOS

KnowledgeOS는 KnowledgeHub Vault에 자유롭게 쓴 아이디어와 발상을 다시 쓸 수 있는 형태로 정리·축적하고, 축적된 생각으로 사고를 강화하는 AgentFabric Operation입니다.

동작 기준은 [기능 명세](docs/CAPABILITIES.md)이며, 각 인수 시나리오가 어떤 구현과 테스트로 확인되는지는 [기능 추적표](docs/CAPABILITY_TRACE.md)에 있습니다. 처음 보는 분은 브라우저로 [KnowledgeOS 위키](docs/wiki/index.html)를 먼저 열어 보세요.

## 문서

| 문서 | 내용 |
|---|---|
| [KnowledgeOS 위키](docs/wiki/index.html) | 기능·목적·사용법·현황·후속 과제를 쉽게 풀어 쓴 정적 HTML 위키 (원본 문서에서 생성) |
| [기능 명세](docs/CAPABILITIES.md) | 최상위 기준: 기능별 동작, 인수 시나리오, 구현 순서 |
| [기능 추적표](docs/CAPABILITY_TRACE.md) | 인수 ID마다 구현 위치와 pytest 노드 |
| [사용자 기능 명세](USER_FEATURE_SPEC.md) | 지키는 약속, 다섯 기능, Obsidian·관리·모바일 사용 흐름 |
| [Blueprint](OBSIDIAN_VAULT_BLUEPRINT.md) | 화면 분담, 역할 조직, Vault 계약, 효과 규칙, Core 준수 |
| [온톨로지 공간](Ontology/README.md) | 도메인 용어·관계 작성 공간 |
| [PROJECT_STATE.md](PROJECT_STATE.md) | 진행 상태와 검증 근거 |

## 구성

| 위치 | 내용 |
|---|---|
| `src/knowledgeos/` | Owner(접수·State·효과), ExOps HTTP, Vault 경계, 검색, 규격화, 제안, 역할 실행, Temporal·LangGraph 런타임, Hermes 어댑터, InterOps 송신, CLI |
| `src/knowledgeos/capabilities/` | C1–C11 기능 모듈 |
| `config/` | 기능 카탈로그, 역할·모델 경로(`roles.json`), 예약 작업(`jobs.json`), Core 채택 고정값(`core-adoption.json`) |
| `roles/` | 역할별 Hermes profile의 공통 지침과 SOUL |
| `frontend/` | 관리 콘솔(브라우저) |
| `thin-client/` | Obsidian Thin Client 플러그인 소스 |
| `tests/` | 공개 인터페이스 기준 계약 테스트 |
| `scripts/` | 추적표 검사(`trace_check.py`), 상태 검증(`validate_state.py`), 위키 생성·검사(`build_wiki.py`, `wiki_check.py`) |
| `docs/wiki/` | 생성된 위키(HTML)와 직접 쓴 설명 원본(`_source/`) |

## 소유와 경계

- 이 디렉터리는 제어·소스 경계입니다. Vault, Owner State(`States/Operations/knowledgeos/`), Runtime은 설정에서 각각 따로 지정하며 서로 겹치거나 포함할 수 없습니다.
- State, Runtime, 자격 증명은 Git과 Vault 밖에 둡니다. ExOps는 loopback(`127.0.0.1`)에만 바인딩합니다.
- `Core/`의 계약과 스키마는 수정하지 않고 `config/core-adoption.json`으로 고정해 따릅니다.
- 실행 기록과 결과는 Temporal 서버에 보존되지만, Work의 소유와 판단은 KnowledgeOS가 가집니다.

## 사용

개발 이미지와 검증은 모두 프로젝트 컨테이너에서 실행합니다.

```sh
make image-build                 # 개발 이미지(Python 3.12.8, uv 0.8.14, Node.js, 고정 Temporal CLI 1.5.1)
make trace-check                 # 추적표가 인수 ID와 정확히 일치하는지 검사하고 노드 목록 출력
make test TESTS="<노드 ID들>"     # 지정한 테스트만 실행(네트워크 차단, 읽기 전용 소스)
make state-check                 # PROJECT_STATE.md 검증
make wiki                        # 원본 문서와 docs/wiki/_source/로 위키 HTML 생성
make wiki-check                  # 위키가 원본과 일치하고 링크가 모두 열리는지 검사
```

모든 개발 세션은 바뀐 범위에 맞춰 원본 문서와 위키 설명을 함께 갱신합니다. 끝내기 전에 `make wiki`로 위키를 다시 만들고, `make wiki-check`를 통과시킨 뒤, 바뀐 페이지를 브라우저로 열어 확인합니다. 설명 문장은 `docs/wiki/_source/`에서만 고치고, 생성된 HTML은 직접 고치지 않습니다. 위키를 갱신하다 명세 문서끼리 어긋나는 곳을 발견하면, 위키에 경고만 남기지 말고 같은 세션에서 우선순위가 낮은 문서(Blueprint, 사용자 기능 명세)를 고칩니다.

추적된 테스트 전체 실행:

```sh
make test TESTS="$(make -s trace-check | tail -n 1)"
```

Owner 실행과 유지보수 CLI(`python -m knowledgeos.cli` 또는 설치된 `knowledgeos`):

```sh
knowledgeos serve --config owner.json          # Owner와 ExOps 백엔드, 관리 콘솔
knowledgeos health | vault-check | index rebuild|verify|embed
knowledgeos standardize PATH --dry-run|--apply
knowledgeos job run standardize|embed|curate|digest  # 실행 결과 영수증까지 따라가 보고
knowledgeos reconcile UNKNOWN_ID --resolution applied|not_applied --yes
knowledgeos install profiles --config owner.json [--dry-run]  # Hermes 역할 profile과 발송 전용 kos-ops profile 설치
knowledgeos install service --config owner.json [--dry-run]   # Owner LaunchAgent 등록(--label, --plist 선택)
```

설정 파일 예시(경로는 설정 파일 기준 상대 경로 가능):

```json
{
  "vault": "/path/to/KnowledgeHub",
  "state": "/path/to/States/Operations/knowledgeos",
  "runtime": "/path/to/Runtimes/knowledgeos",
  "settings": {"runtime": {"address": "127.0.0.1:7233", "namespace": "default"}}
}
```

예약 작업은 Owner가 켜져 있는 동안 Owner 안에서만 실행됩니다. Owner가 시작되고 `startup_delay_minutes`(기본 3분)가 지나면 꺼져 있는 동안 놓친 작업을 한 번씩 실행하고, 이후에는 정해진 시각에 실행합니다. `settings.C7`의 `scheduler_enabled`(기본 true)·`startup_delay_minutes`·`tick_seconds`(기본 30)로 조정합니다.

의미 검색은 로컬 Ollama 임베딩과 State의 벡터 색인(`<State>/semantic/`)을 씁니다. Owner가 loopback HTTP로 Ollama를 직접 호출하며(loopback이 아닌 주소는 설정 오류), 게이트웨이나 원격 모델은 쓰지 않습니다. 기본값은 아래와 같고 `settings.C3.semantic`으로 바꿉니다(그 밖에 `batch_size`, `timeout_seconds`, `max_notes`, `related_neighbors`, `related_min_score`). `ollama_url`이나 `model`을 비우면 의미 검색을 쓰지 않습니다.

```json
"settings": {"C3": {"semantic": {"ollama_url": "http://127.0.0.1:11434", "model": "qwen3-embedding:8b-q4_K_M"}}}
```

- 예약 작업 `embed`(매시 30분)와 `knowledgeos index embed`(남은 노트가 없을 때까지 반복)가 같은 `index.embed` 요청을 처리합니다. 새 노트와 내용이 바뀐 노트만 임베딩하고, 삭제·이름 변경으로 사라졌거나 `deny`·제안(`type: proposal`)·`99_System/`·충돌 표시 때문에 대상이 아니게 된 노트의 벡터는 지웁니다. `local_only`·`confidential`·보고서 노트는 로컬 처리이므로 임베딩합니다.
- 아직 임베딩되지 않았거나 수정된 노트는 다음 임베딩까지 의미 검색·관련 노트 결과에서 빠집니다. 모델을 바꾸면 다음 실행에서 모든 벡터를 새로 만들고, 그 전까지 의미 검색은 `unavailable`입니다. Ollama가 꺼져 있으면 작업이 실패로 남고(재시도 없음) 기존 벡터는 그대로 유지됩니다.
- `knowledgeos index verify`의 `semantic`과 `GET /api/health`의 `search_semantic`으로 준비 상태와 범위(대상·임베딩·오래됨·누락 수)를 확인합니다.

Owner를 Mac의 필요할 때 켜지는 사용자 서비스(LaunchAgent)로 등록하고 Obsidian에서 켜고 끕니다.

```sh
knowledgeos install service --config owner.json --dry-run   # plist와 launchctl argv만 출력, 아무것도 쓰지 않음
knowledgeos install service --config owner.json             # ~/Library/LaunchAgents/com.agentfabric.knowledgeos.owner.plist 작성 후 적재(실행은 안 함)
```

- plist는 `RunAtLoad`·`KeepAlive`를 끄고, 로그를 Runtime의 `logs/`에 남기며, `HERMES_HOME`과 Hermes 바이너리 경로가 든 `PATH`를 넣습니다. 이미 적재된 같은 label은 새 정의로 교체됩니다. 라벨과 plist 경로는 `--label`, `--plist`로 바꿀 수 있습니다.
- Thin Client 명령 **Owner 시작**은 `launchctl kickstart`로 켜고 `/api/health`가 준비될 때까지 기다려 결과를 알립니다. **Owner 중지**는 `launchctl kill SIGTERM`을 보낸 뒤 health 응답이 끊겼는지 확인해, 중지됨·아직 실행 중·확인 불가를 구분해 알립니다. 둘 다 데스크톱 전용이며 노트 본문은 보내지 않습니다. Thin Client 설정의 launchd 라벨 기본값은 `com.agentfabric.knowledgeos.owner`입니다.

### Obsidian 사이드바

`thin-client/`의 `main.js`·`manifest.json`·`styles.css`를 Vault 설정 폴더의 `plugins/knowledgeos-thin-client/`에 함께 배치하고 플러그인을 다시 불러옵니다. 기존 `data.json`은 유지합니다. 리본의 KnowledgeOS 아이콘이나 **KnowledgeOS: 사이드바 열기**로 오른쪽 탭을 엽니다.

- **규격화·정리·강화·찾기**를 한 줄로 배치했습니다. 누르면 아래 기능 창이 바뀌며 선택만으로 작업을 실행하지 않습니다.
- 강화의 발전·묻기·돌아보기, 질문·기간 입력, 의미 검색·관련 노트 결과는 모두 사이드바 안에서 처리합니다. 입력 중인 내용은 기능을 전환해도 메모리에만 유지합니다.
- 제안 노트에서만 **적용·거절**이 나타납니다. 더보기 역시 내부 화면에서 자유 요청·자동 변경 되돌리기·관리 콘솔을 제공하며, 전원 버튼은 Owner 시작·중지를 맡습니다.
- 위에 표시된 마지막 활성 노트를 대상으로 합니다. 사이드바로 초점을 옮겨도 유지되며, 다른 파일을 열면 바뀝니다. 노트가 없으면 노트 전용 동작만 비활성화됩니다.
- 규격화 diff와 적용·취소도 내부에 표시하며, 확정 전에 기능이나 노트를 바꾸면 해당 미리보기를 취소합니다. 진행·오류·결과 링크도 팝업 대신 내부에 표시합니다. 기존 명령 팔레트·문맥 메뉴의 입력창과 확인 절차는 유지합니다. 탭을 여는 것만으로 Owner를 켜거나 끄지 않습니다.

실제 Obsidian 인계 시에는 탭 재사용, 노트 대상 전환, 내부 기능 창·입력·검색 결과, 좁은 폭과 테마, 제안 전용 버튼 표시를 확인합니다. 브라우저 대역 렌더링과 실제 플러그인 설치·GUI 관찰은 별개입니다.

## 검증 범위

테스트는 실제 Temporal 개발 서버와 LangGraph를 사용하지만, 모델 역할은 스크립트 대역이고 Hermes와 `launchctl`은 가짜 실행 파일이며 시계는 조작 가능한 가짜입니다. 실제 모델 호출, Hermes profile 설치, 예약 작업의 실제 시각 실행, launchd 등록과 Owner 시작·중지, Discord 전송, 실제 HermesTrace 수신기, Obsidian GUI는 별도로 확인해야 하며, 그 결과는 `PROJECT_STATE.md`에 따로 기록합니다.

## 퇴역 자산

이전 구현의 소스, 내부 상태, Runtime, 역할 profile, 합성 Vault 노트는 복구용 아카이브(`Tmp/archive/knowledgeos-legacy-2026-11-19/`, 0600 권한)에 보관한 뒤 삭제했습니다.
