# KnowledgeOS

KnowledgeOS는 KnowledgeHub Vault에 자유롭게 쓴 아이디어와 발상을 다시 쓸 수 있는 형태로 정리·축적하고, 축적된 생각으로 사고를 강화하는 AgentFabric Operation입니다.

동작 기준은 [기능 명세](docs/CAPABILITIES.md)이며, 각 인수 시나리오가 어떤 구현과 테스트로 확인되는지는 [기능 추적표](docs/CAPABILITY_TRACE.md)에 있습니다. 처음 보는 분은 별도 Sites 모노레포의 [KnowledgeOS 위키](../../Sites/knowledgeos/wiki/index.html)를 먼저 읽어 보세요.

## 문서

| 문서 | 내용 |
|---|---|
| [KnowledgeOS 위키](../../Sites/knowledgeos/wiki/index.html) | 코드를 읽지 않고 실제 기능의 처리 흐름·Hermes Profile·Graph·Temporal 연결을 이해하는 필수 한국어 기술 위키 |
| [기능 명세](docs/CAPABILITIES.md) | 최상위 기준: 기능별 동작, 인수 시나리오, 구현 순서 |
| [기능 추적표](docs/CAPABILITY_TRACE.md) | 인수 ID마다 구현 위치와 pytest 노드 |
| [사용자 기능 명세](USER_FEATURE_SPEC.md) | 지키는 약속, 여섯 기능, Obsidian·관리·모바일 사용 흐름 |
| [Blueprint](OBSIDIAN_VAULT_BLUEPRINT.md) | 화면 분담, 역할 조직, Vault 계약, 효과 규칙, Core 준수 |
| [온톨로지 공간](Ontology/README.md) | 도메인 용어·관계 작성 공간 |
| [PROJECT_STATE.md](PROJECT_STATE.md) | 진행 상태와 검증 근거 |

## 구성

| 위치 | 내용 |
|---|---|
| `src/knowledgeos/` | Owner(접수·State·효과), ExOps HTTP, Vault 경계, 검색, 규격화, 제안, 역할 실행, Temporal·LangGraph 런타임, Hermes 어댑터, InterOps 송신, CLI |
| `src/knowledgeos/capabilities/` | C1–C12 기능 모듈 |
| `config/` | 기능 카탈로그, 역할·모델 경로(`roles.json`), 예약 작업(`jobs.json`), Core 채택 고정값(`core-adoption.json`) |
| `roles/` | 역할별 Hermes profile의 공통 지침과 SOUL |
| `frontend/` | 관리 콘솔(브라우저) |
| `thin-client/` | Obsidian Thin Client 플러그인 소스 |
| `tests/` | 공개 인터페이스 기준 계약 테스트 |
| `scripts/` | 추적표 검사(`trace_check.py`), 상태 검증(`validate_state.py`), Sites의 KnowledgeOS 범위 생성·검사 호출(`build_wiki.py`, `wiki_check.py`) |
| `../../Sites/knowledgeos/` | 소개·위키·선별 공개 근거 사본과 유일한 설명 원고(`_source/`); 별도 Git 모노레포 |

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
make wiki                        # Sites/knowledgeos/_source/로 Sites/knowledgeos/wiki/ 생성
make wiki-check                  # KnowledgeOS 범위의 신선도·인수/상태 연결·링크 검사
```

바뀐 기능에 맞춰 명세와 Sites의 `knowledgeos/_source/` 설명을 함께 갱신합니다. 설명 원고와 생성 결과는 하나의 Sites 저장소에만 두며, 예전 `docs/wiki/` HTML 별칭은 만들지 않습니다. Operation별 독립 Sites worktree에서는 `make wiki SITES_ROOT=/path/to/sites-worktree`와 대응하는 `make wiki-check`로 자신의 범위만 생성·검사합니다. 선택한 재사용 구현 worktree는 `OPERATIONKIT_ROOT=/path/to/OperationKit`로 지정할 수 있습니다. 공통 허브나 등록을 바꿀 때만 Sites에서 전체를 생성합니다.

위키는 [작성 철학](../../Sites/knowledgeos/wiki/philosophy.html)에 따라 실제 입력에서 결과까지의 구현 선택을 자연어로 설명합니다. 처리 순서와 기본값은 현재 코드·설정으로 확인하며, 구현 근거와 실제 실행 관찰을 구별합니다. Sites의 문서 변경은 독립 worktree의 GitHub PR로 리뷰·병합하며, 소유 Operation의 명세·코드 근거는 명시한 AgentFabric 소스 경로에서만 읽습니다.

### 모바일에서 위키 읽기

위키는 휴대폰 브라우저에서도 읽을 수 있습니다. 좁은 화면에서는 검색창이 별도 줄을 쓰고 본문은 화면 폭에 맞춥니다. 넓은 표·코드는 영역 안에서 가로 스크롤하며, 그림은 **그림 크게 보기 / 그림 전체 보기**로 전환합니다.

접속은 Sites의 ExOps 정적 서버와 서버 측 **Tailscale Serve의 비공개 tailnet HTTPS**를 사용합니다. 정확한 컨테이너 실행·Host 설정·`tailscale serve --bg http://127.0.0.1:8766` 안내는 [Sites README](../../Sites/README.md)에 있습니다. 이 작업은 Serve나 네트워크를 활성화하지 않으며 Funnel·공개 인터넷·Owner API 노출을 포함하지 않습니다. 원문 근거는 Sites 안의 명시적 선별 사본으로 연결하므로 저장소 전체를 제공할 필요가 없습니다.

### 기능 검증과 Owner 실행

추적된 테스트 전체 실행:

```sh
make test TESTS="$(make -s trace-check | tail -n 1)"
```

Owner 실행과 유지보수 CLI(`python -m knowledgeos.cli` 또는 설치된 `knowledgeos`):

```sh
knowledgeos serve --config owner.json          # Owner와 ExOps 백엔드, 관리 콘솔
knowledgeos health | vault-check | index rebuild|verify|embed
knowledgeos standardize PATH --dry-run|--apply
knowledgeos job run standardize|embed|curate|daily|digest  # 실행 결과 영수증까지 따라가 보고
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

`ai_policy`는 `approve`(기본)와 `deny` 두 값입니다. 빠졌거나 비면 `approve`, 퇴역값을 포함한 알 수 없는 값은 `deny`입니다. `approve`만 모든 자동화·모델·예약 작업에 쓰며, 보고서가 아닌 허용 노트만 승인된 수신자에게 Gateway로 내보냅니다. `sensitivity: confidential`은 이 허용 여부를 바꾸지 않습니다. 정책 결정은 사용자만 하며, KnowledgeOS는 C2 규칙 6의 빠진 값 기본 `approve`와 새로 생성하는 노트의 기본·상속 정책만 씁니다.

역할의 모델 설정은 `config/roles.json`에서 관리합니다. `classifier`·`linker`·`connector`는 `openai-codex/gpt-6.1-terra`, Director·Manager·EvalOfficer·`challenger`·`synthesizer`는 `openai-codex/gpt-6.1-sol`을 씁니다. 모든 모델 역할의 `reasoning_effort`는 `high`이며, profile 생성 시 `agent` 아래에 씁니다. `classifier`에는 로컬 `base_url`·`ollama_num_ctx`나 대체 모델을 두지 않습니다.

모델 단계 하나는 Temporal Activity 하나 안에서 끝나야 합니다. 기본 Activity 시간(`settings.runtime.activity_timeout_seconds`)은 120초이며, 실제 호스트 `owner.json`은 이 값을 600초로 설정합니다.

예약 작업은 Owner가 켜져 있는 동안 Owner 안에서만 실행됩니다. Owner가 시작되고 `startup_delay_minutes`(기본 3분)가 지나면 꺼져 있는 동안 놓친 작업을 한 번씩 실행하고, 이후에는 정해진 시각에 실행합니다. `settings.C7`의 `scheduler_enabled`(기본 true)·`startup_delay_minutes`·`tick_seconds`(기본 30)로 조정합니다.

`daily`는 매일 07:00 이전 날짜의 허용된 미완료 일정·할 일을 오늘의 `10_Journal/Daily/YYYY-MM-DD.md`로 인계하고, 원본 항목의 체크박스만 `[>]`로 표시합니다. 오늘 노트의 기존 줄은 보존하며, 없는 날만 `ai_policy: approve`로 메모·할 일·일정·하루 정리 네 구역의 노트를 만듭니다. 데일리 노트에는 AI 제안을 만들지 않습니다. 알릴 할 일이 있으면 하루 한 번 Discord 할 일 알림을 보내며, 현재 `approve` 노트의 할 일 이름만 날짜·메타데이터·링크 없이 공개합니다.

Discord 즉시 알림은 충돌·해결되지 않은 효과의 결과 불명만 보냅니다. 실패·차단·놓친 작업은 즉시 알리지 않습니다. 08:00 `digest`는 제안·새 보고서·오래된 포착·확인 대기·주의 항목 등 확인할 것이 있을 때만 보내며, 자동 규격화 내역이나 다시 볼 노트·실패 작업만으로는 보내지 않습니다. 실패 작업 이름은 다른 확인거리가 있는 요약에만 한 줄로 덧붙입니다.

의미 검색은 로컬 Ollama 임베딩과 State의 벡터 색인(`<State>/semantic/`)을 씁니다. Owner가 loopback HTTP로 Ollama를 직접 호출하며(loopback이 아닌 주소는 설정 오류), 게이트웨이나 원격 모델은 쓰지 않습니다. 기본값은 아래와 같고 `settings.C3.semantic`으로 바꿉니다(그 밖에 `batch_size`, `timeout_seconds`, `max_notes`, `related_neighbors`, `related_min_score`, System One 관련 노트 거르기 기준 `related_min_probability`). `ollama_url`이나 `model`을 비우면 의미 검색을 쓰지 않습니다.

```json
"settings": {"C3": {"semantic": {"ollama_url": "http://127.0.0.1:11434", "model": "qwen3-embedding:8b-q4_K_M"}}}
```

- 예약 작업 `embed`(매시 30분)와 `knowledgeos index embed`(남은 노트가 없을 때까지 반복)가 같은 `index.embed` 요청을 처리합니다. 새 노트와 내용이 바뀐 `approve` 노트만 임베딩하고, 삭제·이름 변경으로 사라졌거나 정책 거부·제안(`type: proposal`)·`99_System/`·충돌 표시 때문에 대상이 아니게 된 노트의 벡터는 지웁니다. `approve`이면 `confidential`·보고서 노트도 임베딩합니다.
- 아직 임베딩되지 않았거나 수정된 노트는 다음 임베딩까지 의미 검색·관련 노트 결과에서 빠집니다. 모델을 바꾸면 다음 실행에서 모든 벡터를 새로 만들고, 그 전까지 의미 검색은 `unavailable`입니다. Ollama가 꺼져 있으면 작업이 실패로 남고(재시도 없음) 기존 벡터는 그대로 유지됩니다.
- `knowledgeos index verify`의 `semantic`과 `GET /api/health`의 `search_semantic`으로 준비 상태와 범위(대상·임베딩·오래됨·누락 수)를 확인합니다.

System One은 OpenAI Decisions API(`gpt-6-luna`)에 선택형 질문을 보내는 Owner 서비스입니다. 규격화 규칙 9(폴더로 못 정하는 `type`, 잘못된 `status`·`confidence`, 빠지거나 잘못된 `capture_kind`), 모델 teammate(`classifier`·`linker`·`connector`·`challenger`·`synthesizer`)의 `system_one(handle, question, choices?)` 도구, 관련 노트 거르기, 문서 관리 상태 제안, 자유 요청 사전 분류에 씁니다. 도구는 그 Work에서 읽은 노트에 대해 선택지 하나를 고르거나 예/아니오 확률을 묻고, step마다 10번까지 보냅니다. `classifier`가 `question` 없이 대상 핸들로 부르면 type/status 제안이며 대상마다 Work에 기록해 재사용합니다. Director·Manager·EvalOfficer에는 이 도구가 없습니다. 답은 조언일 뿐이고 Owner가 어디에도 쓰지 않습니다. `ai_policy`·`sensitivity`는 묻지 않고, 출처와 관계없이 현재 `approve` 노트만 보냅니다. API 키는 Git·Vault 밖의 0600 파일에 두고 경로만 설정합니다.

```json
"settings": {"system_one": {"api_key_file": "/path/to/States/System/OpenAI/knowledgeos-decisions.key"}}
```

- 그 밖의 설정: `model`(기본 `gpt-6-luna`), `min_confidence`(기본 0.7), `timeout_seconds`(기본 20), `max_input_chars`(기본 8000), `url`.
- `GET /api/health`의 `system_one`이 `ready`·`unavailable`·`unconfigured`를 보여 줍니다. 꺼져 있거나 실패해도 각 기능은 System One 없이 동작합니다. 작업에 묶인 호출은 내용 없이 State `system_one.jsonl`에 기록됩니다.

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
