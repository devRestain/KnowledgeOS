# KnowledgeOS capabilities

This is the user-designated top-level behavior and acceptance specification. [USER_FEATURE_SPEC.md](../USER_FEATURE_SPEC.md) supplies intent, and the [Blueprint](../OBSIDIAN_VAULT_BLUEPRINT.md) supplies system constraints, surfaces, execution organization, data model and effect rules; they supplement this contract without weakening its acceptance coverage or changing Core contracts or schemas. Conflicts are corrected together under Blueprint §0. Every acceptance example is an executable test through a public interface. Model steps use scripted role responses, except in the real-environment smokes.

The mandatory beginner-readable technical wiki is owned by KnowledgeOS and authored only in the Sites monorepo's `knowledgeos/_source/`, with generated presentation at `/knowledgeos/wiki/`. Its purpose-to-code explanations and generated acceptance/state references supplement this specification without replacing it or creating a second state ledger. Sites publishes only explicit source/document copies through ExOps; optional usage/status/console surfaces do not become universal Operation requirements.

## Shared terms

- **Request**: a Thin Client command, console action, CLI command, scheduled job run or Gateway submission, identified by a stable request ID.
- **Work**: one Director-admitted request. It records its actor, target (Officer or Team), snapshotted inputs, segments, role invocations, effects and assessment.
- **Step**: one role invocation inside a segment. It is either `static` (Owner code) or `model` (a Hermes role profile).
- **Handoff**: a teammate's intermediate artifact, readable by later teammates in the same Work.
- **Report**: a create-only Markdown note in `01_AI_Review/Reports/`.
- **Proposal**: a change set for one target note in `01_AI_Review/Proposals/`, applied only by the user. It is create-only except for exact-preimage regeneration of an unchanged Owner-written proposal (Blueprint §5.6, §6.2).
- **Autofix**: a standardization change written directly under capability C2 rules: deterministic rules 1-8 and rule-9 values System One decided with sufficient confidence.
- **Attention item**: something that needs the user's eye. It is shown either as a Bases view over Vault metadata or as a computed entry in the console and the digest. It is never stored as a note.
- **System One**: the Owner's fast closed-set judgment service (below). It answers typed questions; Owner code acts on the answers.

## System One

System One is an Owner service, not a role. Owner code sends it one input and typed questions through the OpenAI Decisions API (`POST /v1/decisions`, model `gpt-6-luna`): a `choice` from a fixed set of values or a `predicate` probability. It never writes, never authors free text and never decides `ai_policy` or `sensitivity`.

- **Settings** (`settings.system_one`): `api_key_file` (absolute path of a private 0600 file outside Git and the Vault; absent means unconfigured), `url` (default the OpenAI Decisions endpoint; plain `http` only on loopback), `model` (default `gpt-6-luna`), `timeout_seconds` (default 20), `min_confidence` (default 0.7) and `max_input_chars` (default 8000, the body excerpt sent per note). `GET /api/health` reports `system_one` as `ready`, `unavailable` (unusable key file) or `unconfigured` without sending a request.
- **Disclosure**: only `approve` notes are sent, with current policy checked immediately before sending (Blueprint §5.4). This applies to user, scheduled and Gateway requests and every model, regardless of `sensitivity`. Missing or empty `ai_policy` means `approve`; unknown and retired values fail closed to `deny`.
- **Answers**: every `choice` question offers `none_of_these`. A choice is used only when it is an offered value other than `none_of_these` with confidence at least `min_confidence`; a predicate holds at its stated threshold. Refusals, `none_of_these` and low confidence change nothing.
- **Failure**: an unconfigured or failing System One is reported and never retried within the request; every capability then behaves as it would without System One. No capability fails because of it.
- **Records**: a call made for a Work or request appends question names, an input digest, model and token usage, never content, to the State stream `system_one`. Read surfaces (`POST /api/search`) write no State and only log.
- **Touchpoints**: C2 rule 9 values, the `system_one` role tool (C1 `classifier` and `linker`, C4 `connector`, `challenger` and `synthesizer`), C3 related-note vetting, C5 status proposals and the C11 free-text prefilter.
- **Role tool** `system_one(handle, question, choices?)`: a model teammate asks one closed question about a note read in this Work: a `choice` over its 2-10 `choices` plus `none_of_these`, or without `choices` a `predicate`. The disclosed note version is sent only when that version and the current file are `approve`; an unread handle, an invalid question, a denied note or the eleventh request in one step sends nothing. Answers are advisory: Owner code never writes them to a note, handoff or report. In C1, a call without `question` on a target handle is the `classifier` type/status suggestion (ORG-7). Director, Managers and the EvalOfficer have no such tool.

## Capability summary

| ID | Capability | Target | Roles | Writes |
|---|---|---|---|---|
| C1 | Organize | Curation Team | CurationManager; `scout`, `classifier`, `linker`; EvalOfficer | Proposals |
| C2 | Standardize | Officer | StandardizationOfficer (asks System One for rule 9); EvalOfficer (static) | Autofix or confirmed manual changes |
| C3 | Search | Owner service; Officer for embedding | EmbeddingOfficer (static); EvalOfficer (static) | Vector index in State |
| C4 | Enhance | Thinking Team | ThinkingManager; `retriever`, `connector`, `challenger`, `synthesizer`; EvalOfficer | Reports |
| C5 | Document management | Officer | StewardshipOfficer (asks System One for status judgments); EvalOfficer (static) | Lifecycle proposals |
| C6 | Review | Owner effect from a human action | — | Applies, rejects, undoes |
| C7 | Scheduled jobs | Triggers C1, C2, C3, C5, C12 | Scheduler | Through those capabilities |
| C8 | Notifications | Owner effect | StewardshipOfficer content; Notifier | Discord notices |
| C9 | Learning and Experience | Owner and role profiles | All model roles | Feedback views, local SourceEvents, eligible Experience outbox |
| C10 | Gateway | Owner | Director for intake | Capture notes, bounded results |
| C11 | Invocation and management surfaces | — | — | Through the capabilities above |
| C12 | Daily notes | Officer | DailyOfficer (static); EvalOfficer (static) | Today's daily note and `[>]` marks on carried items |

Every Work passes the Director. Structured requests (every Thin Client command, job and CLI command) are admitted by static code. Only free-text requests use the `kos-director` model.

## C1 Organize

The Curation Team places notes into the knowledge structure: classification, naming, tags, links and location.

- **Triggers**: Thin Client "정리 요청" on the current note, a selection of files or a folder; the scheduled `curate` job (C7); a Gateway intake capture (C10).
- **Input**: up to 10 target notes per Work. Only `approve` notes are admitted or disclosed, regardless of request origin or model.
- **Team pool**:
  - `scout` (static): collects the target notes, candidate related notes (semantic neighbors vetted as in C3) and the tag vocabulary. It neither asks System One nor records classification hints.
  - `classifier` (model): decides type, status, title, tags and conventional folder per note. Its `system_one(handle)` tool asks System One for a `type` choice and, for a known type, a `status` choice for an admitted target. Accepted answers include confidence and top probabilities and are advisory only; Owner code never writes a suggestion to a note or proposal. Outcomes are recorded per target on the Work; repeated calls return the recorded outcome without another request. A non-target returns `not_a_target`; an unconfigured service returns `unconfigured`; a target whose admitted or current policy is not `approve` returns `denied` with nothing sent; failure returns `unavailable`. Hints are not in `work_context`.
  - `linker` (model): decides links and typed relations to existing notes, and whether a capture should attach to an existing note.
- **Manager**: CurationManager picks the teammates the Work needs. For example, a capture with no plausible related notes may skip `linker`.
- **Output**: the Owner renders one proposal per target from the handoffs. Each change is a checklist item:
  - `type` and its valid `status` together as one atomic checklist item.
  - `title`.
  - `tags`, chosen from the existing vocabulary unless marked new.
  - Links (`related`, `projects`, `sources`, or a typed relation).
  - A move to the conventional folder.
  - For a capture, either conversion to a typed note with that destination type's valid status (for example, `idea` with `seed`), or retention as `capture` with `status: processed`. Attaching to an existing note adds a `related` link on the same capture and leaves it `capture`/`processed`; it never edits the other note's body. Every proposal still changes only its single target.
- **Criteria**:
  - Static: every enum value is valid; every link target was read in this Work; new tags are marked; there is at most one open proposal per target.
  - Semantic, judged by the model EvalOfficer: each classification and link is supported by the cited note content.
- **Refuses**: `deny` notes; daily notes (`type: daily`, reason `daily note (never a proposal target)`); notes with conflict markers; ambiguous-ID targets. Explicit daily-note targets are refused, folder members are skipped with that reason, and `curate` never selects them. A target with an open or stale proposal never gains a second one. Regeneration may replace only the exact bytes still recorded as Owner-written; user-edited or State-unrecorded proposals are preserved (Blueprint §5.6).

Acceptance:

- **ORG-1** Given an untyped note in `00_Inbox/`, when organize runs, then one proposal lists a type, a title and at least one existing related note, and the note itself is unchanged.
- **ORG-2** Given the `linker` cites a note it never read in this Work, then that handoff fails validation, the `linker` gets one corrective turn, and a second failure leaves the link criterion unmet.
- **ORG-3** Given a note with `ai_policy: deny` or a retired value such as `ask`, then organize (user or scheduled) never admits it and no model role receives its content.
- **ORG-4** Given a proposal where the user unchecked the move, when it is applied, then all other checked changes apply and the file stays in place.
- **ORG-5** Given the CurationManager's Graph proposal contains an instruction for the EvalOfficer, then the Owner rejects the proposal and no segment starts.
- **ORG-6** Given the EvalOfficer judges one classification unsupported, then the CurationManager composes one successor segment with only `classifier` for that target, within budget.
- **ORG-7** Given System One is configured and a user organize request targets an untyped `approve` note in `00_Inbox/` that System One classifies as `idea` at 0.9 confidence, when the `classifier` calls `system_one` with the target handle, then it receives that advisory suggestion, a second call for the same target returns the recorded answer without a new request, and if the `classifier` submits `question` the proposal says `question` and the note is unchanged. A target whose policy became `deny` after admission gets `denied` with nothing sent, and with System One unavailable the tool answers `unavailable` and the Work completes.
- **ORG-8** Given an `approve` daily note, then an explicit organize request is refused, a folder request skips it, and `curate` never selects it; no proposal exists.

## C2 Standardize

The StandardizationOfficer makes notes conform to the Vault contract with minimal edits. Rules 1-8 are deterministic. Rule 9 lets System One map closed-set (enum) values that need judgment; every other judgment, such as title, tags, links, folder or reclassifying a valid type, belongs to capability C1.

**Three paths:**

| Path | Who | How | Writes |
|---|---|---|---|
| Authoring | The user, through plugins | QuickAdd choices and Templater templates in `99_System/Templates/` create notes with conforming frontmatter | The new note (by the plugins) |
| Manual | The user, through KnowledgeOS | Thin Client "규격화 미리보기" on a note, selection or folder shows the exact diff and applies after confirmation. The CLI batch does the same for maintenance with `--dry-run` and `--apply` | Confirmed changes |
| Automatic | The `standardize` job (C7) | Applies the rules to notes changed since the last run, honoring the quiet period | Autofix |

**Rules.** Each rule applies only where the value is missing or malformed.

1. Create a frontmatter block if absent.
2. Add `id`: a new UUIDv4 when missing or malformed. UUID validity is deterministic, not a classification judgment.
3. Add `type` derived from folder:
   - `00_Inbox/` → `capture`
   - `40_Knowledge/Ideas/` → `idea`
   - `40_Knowledge/Notes/` → `knowledge`
   - `40_Knowledge/Questions/` → `question`
   - `40_Knowledge/Sources/` → `source`
   - `20_Projects/<name>/<name>.md` → `project`
   - Anywhere else, rule 9.
4. Add `created`: the first time KnowledgeOS observed the file.
5. Add the type's initial `status`, except for `daily`, which has no status.
6. Add `ai_policy: approve` when missing. Existing policy decisions belong to the user and are never rewritten.
7. Normalize shapes:
   - `tags` and `aliases` become lists.
   - A leading `#` is dropped from tags.
   - Dates become ISO-8601 with an offset.
   - Enum values use their canonical case.
8. Move trailing body lines that are frontmatter keys of known list properties (such as `tags:` or `related:`) into frontmatter. Merge them with existing values without duplicates, and remove only those lines.
9. Ask System One for closed-set values that need judgment, one request per note with one `choice` question per field over the contract's values (Blueprint §5.2, §5.3) plus `none_of_these`:
   - `type` when it is missing and rule 3 derives none;
   - `status` when it is neither a valid value of the note's type (or `archived`) nor one up to case;
   - `confidence` of a `knowledge` note when present but invalid;
   - `capture_kind` of a `capture` when missing or invalid.

   A `type` decided this way gets the initial `status` by rule 5, or a second request when its existing `status` is invalid for that type. An accepted answer (System One section) becomes an ordinary planned change with rule `system_one`, recorded with its confidence; anything else leaves the field unchanged and is listed as undecided with its reason (`unconfigured`, `unavailable`, `deferred` near the Activity window's end, `policy`, `refused`, `none_of_these` or `low_confidence`). Every path sends only `approve` notes, rechecking current policy immediately before sending. An existing unknown `type`, a missing `confidence`, `ai_policy`, `sensitivity` and every non-enum field are never judged.

Outside the explicit system, review, reserved and ignored-path exclusions below, a folder alone never excludes ordinary Markdown, including a note at the Vault root; only navigation notes identified as `home` or `system` are excluded as navigation.

**Never**:

- Change other body text.
- Delete, rename or reorder properties.
- Rename or move files.
- Touch `deny` notes, `99_System/`, `01_AI_Review/`, the reserved `00_Inbox/Requests/`, navigation notes, attachments or ignored paths.

**Skips**:

- The automatic path skips notes modified within the quiet period (default 10 minutes).
- All paths skip notes with conflict markers, unparseable YAML or a duplicate ID. These become attention items.
- The automatic path detects changes by content hash, not by Git history or the mere occurrence of a prior run. A note whose rule-9 question was `unavailable` or `deferred` is not settled, so the next run asks again; other outcomes settle it until its content changes.

**Write**: an exact-preimage check, an atomic replace and a minimal diff (Blueprint §5.7, §6). A manual preview waits for a recorded human confirmation or cancellation and shows each System One value with its confidence above the diff. Confirmation applies unchanged targets independently; a changed target is not written and is reported unmet. Each write records its preimage and result digests and the exact delta, including System One decisions, in the change log, which supports undo (C6).

**Static criteria** (EvalOfficer): skips respected, result valid, properties preserved, minimal diff, and decisions grounded: every `system_one` change is an offered value of a rule-9 field, equals an accepted answer at or above the recorded threshold, and belongs to a note whose admitted policy is `approve`.

Acceptance:

- **STD-1** Given a frontmatter-less note in `40_Knowledge/Ideas/`, when standardize runs, then it gains `id`, `type: idea`, `created`, `status: seed` and `ai_policy: approve`, and its body bytes are unchanged.
- **STD-2** Given a note whose body ends with `tags: [a, b]` and whose frontmatter has `tags: [b]`, then the frontmatter becomes `[b, a]`, the trailing line is removed and nothing else changes.
- **STD-3** Given the note changes between preview or read and write, then nothing is written. The manual path reports the change; the automatic path reconsiders the note on its next run.
- **STD-4** Given a note edited 2 minutes ago, then the automatic path skips it, while a manual preview and confirmation still standardizes it.
- **STD-5** Given an already conforming note, then every path writes nothing.
- **STD-6** Given frontmatter with custom keys and quoted values, then only missing keys are appended, and existing lines stay byte-identical.
- **STD-7** Given a note created from each type template, then standardization proposes zero changes.
- **STD-8** Given a manual preview that the user cancels, then nothing is written and no change-log entry exists.
- **STD-9** Given a note at the Vault root with `status: 초안` and no `type`, when a manual preview runs with System One answering `knowledge` (0.92) and then `developing` (0.81), then two requests are sent (type, then status), the preview shows both values with their confidence, nothing is written until confirmation, `sensitivity` and `ai_policy` are never asked, and the `system_one` State records carry the Work ID but no note text.
- **STD-10** Given `approve` notes with invalid statuses or a missing `capture_kind`, a `deny` note and a low-confidence answer, when the automatic path runs, then only confident answers are written as autofix, the `deny` note's text is never sent, the low-confidence field is left unchanged, and a second run sends nothing for the settled notes.
- **STD-11** Given System One is down, when the automatic path runs, then rules 1-8 still apply, the rule-9 field stays unchanged with reason `unavailable`, and the next run after recovery asks again and applies the answer.

## C3 Search

- **People search by keyword** with Obsidian core Search, which needs no KnowledgeOS.
- **KnowledgeOS search** is an Owner service for:
  - Thin Client "관련 노트": links, backlinks, shared tags and semantic neighbors, each labeled by reason.
  - Thin Client "의미 검색", when the local semantic index is ready.
  - The read tools of Team roles inside a Work.
- **Keyword mode** matches Korean and English partial words. **Semantic mode** ranks notes by the cosine similarity of local embeddings between the query and each note's best-matching chunk. It is available only when the index is ready and the query can be embedded. If not, the client says so and never silently substitutes keyword results.
- **Results** carry title, path, type, status, an excerpt and a line locator. Semantic results also carry the matching chunk's last line and its similarity score. The client lists them in a picker that opens the note at that line.
- **Corpus**: all `approve` notes outside `99_System/`.
  - Proposals are excluded.
  - Reports are included for people and excluded for roles, unless the user changed their type.
- **Filters**: every result passes its surface's audience and policy filters. Semantic search and semantic neighbors return only `approve` notes and never proposals. Role tools never return reports, regardless of request origin or model. Gateway queries export only `approve` non-report notes to authorized recipients.
- **Index**: rebuildable derived data, refreshed per changed document. The keyword index lives in memory; the vector index lives in State.

**Semantic index.**

- **Embedding service**: a local Ollama server, called directly by the Owner over loopback HTTP; no gateway or remote model is involved. `settings.C3.semantic` holds `ollama_url` (default `http://127.0.0.1:11434`; a non-loopback URL is a configuration error), `model` (default `qwen3-embedding:8b-q4_K_M`), `batch_size`, `timeout_seconds`, `max_notes`, `related_neighbors`, `related_min_score` and `related_min_probability`. An empty `ollama_url` or `model` leaves semantic search unconfigured.
- **Chunks**: a body is split at headings and paragraph breaks into windows of about 1,500 characters, each with its line range. The embedding input is the note title and the chunk text. Frontmatter is never embedded.
- **Eligibility**: only `approve` notes are embedded; reports are eligible and `sensitivity` does not change eligibility. Never embedded: `deny` or unknown-policy notes; notes under `99_System/`; proposals, decided by `type: proposal` or the proposals folder, because their application is not decided; notes with merge-conflict markers. Policy is checked against the note's current content immediately before anything is sent; a note that has become ineligible is dropped, not sent.
- **Embedding job**: the request `index.embed` is admitted by static code as one Work for the static EmbeddingOfficer. The C7 `embed` job triggers it with actor `schedule/embed`, and `knowledgeos index embed` runs it for the user until nothing is pending.
  - It embeds each eligible note with no vectors for the configured model or whose content SHA-256 differs from the embedded one, and drops the vectors of notes that were deleted, renamed or became ineligible. Unchanged notes cost nothing.
  - A run with nothing to do settles at admission with no Work. Otherwise a Work embeds at most `max_notes` notes, most recently modified first, and stops starting notes before its Activity window closes. The outcome reports `embedded`, `unchanged`, `removed`, `failed` and `pending`; the next run continues.
  - Static criteria: every planned note is an admitted eligible snapshot, and its chunk ranges are exactly those derived from that snapshot.
  - If the embedding service is unreachable or rejects a request, the Work fails visibly with the service's error, sends no further request, keeps every vector already stored and is not retried before the next run.
- **Readiness**: the index is ready when the configured model has vectors for at least one note. A note without current vectors (new, edited since its last embedding, or not yet reached) is absent from semantic results and neighbors until it is embedded. Vectors made by another model are discarded when the first note is embedded with the configured one, and the index is not ready until then. `GET /api/health` reports `search_semantic`, and `index verify` reports counts of eligible, embedded, stale, missing and extra (no longer eligible) notes without naming paths.
- **Neighbors**: the semantic neighbors of a note are at most `related_neighbors` notes (default 5) whose best chunk has cosine similarity of at least `related_min_score` (default 0.5) to the note's mean chunk vector, so unrelated notes are never labeled. The default threshold is a starting value to tune against the configured model.
- **Neighbor vetting**: when System One is configured, related notes (the people surface, role `related` tools and the C1 `scout` and C4 `retriever` candidates) vet semantic neighbors before labeling them. One request carries the focal note and, per neighbor, a predicate asking whether that neighbor (title and best-matching chunk excerpt) is substantively related, not merely sharing generic words. A neighbor keeps its `semantic` reason, marked `vetted: true`, only when the probability is at least `related_min_probability` (default 0.5); a rejected neighbor loses `semantic` and disappears unless it also has a link, backlink or tag reason. The focal note and each neighbor must still be `approve` immediately before sending, regardless of surface or origin. An unconfigured, failing or refusing System One keeps the eligible cosine-only result without `vetted`. Verdicts are cached in memory per focal content, neighbor content and model, so repeated calls on unchanged notes send nothing, and the read writes no State.
- **Stored data**: `<State>/semantic/`, derived and never in the Vault or Git. Semantic neighbors of a note need no embedding request; a semantic query embeds only the query text.

Acceptance:

- **SRC-1** Given a note containing "사고실험", when the keyword search tool is asked for "사고", then the note is returned with the matching line's locator.
- **SRC-2** Given a `deny` note containing the query term, then no mode and no surface returns it.
- **SRC-3** Given the semantic index is unavailable (unconfigured, without vectors for the configured model, or the query cannot be embedded), when "의미 검색" is used, then the client shows "unavailable" and no results.
- **SRC-4** Given a note is edited and saved, when it is searched again, then the result reflects the new text.
- **SRC-5** Given an embedded Vault, when one note is edited and the embedding job runs twice, then only that note's text is sent to the embedding service, once; the edited note is absent from semantic results until it is embedded, and the vectors of deleted or renamed notes are dropped.
- **SRC-6** Given `deny` or unknown-policy notes, notes under `99_System/`, proposals (in or outside the proposals folder) and conflict-marked notes, when the embedding job runs, then none of their text reaches the embedding service and no semantic mode returns them, while `approve` notes including confidential and report notes are embedded; a note that becomes ineligible has its vectors dropped by the next run.
- **SRC-7** Given embedded notes, when "의미 검색" asks a question, then results are ranked by similarity with the best chunk's line range and an excerpt. Role tools receive only `approve` non-report semantic neighbors regardless of request origin or model. Gateway queries return only `approve` non-report notes to authorized recipients.
- **SRC-8** Given the embedding service is down, when the job runs, then the Work fails visibly, existing vectors are kept and still give semantic neighbors, "의미 검색" shows "unavailable", and the next run embeds only what is still missing.
- **SRC-9** Given a focal `approve` note with two `approve` semantic neighbors, one of which System One judges unrelated, and a `deny` neighbor, when "관련 노트" runs, then the related neighbor is returned marked vetted, the `deny` neighbor is absent and its text is never sent, the unrelated one is omitted, a repeated call sends no new request and writes no State, and with System One unavailable the result is the eligible cosine-only neighbor list.

## C4 Enhance

The Thinking Team uses the accumulated Vault to strengthen the user's thinking about one note or question. The output is a cited report that separates what the user already wrote from inference.

- **Trigger**: Thin Client "강화" on the current note in a chosen mode, or a free-text request routed by the Director.
- **Modes**:

| Mode | Input | Report sections |
|---|---|---|
| `develop` | An idea, knowledge or question note | Core restated in the user's own words (cited); related things already written; connections and tensions; gaps and open questions; possible next steps |
| `ask` | A question | Answer; supporting notes; conflicting notes; what the Vault does not cover |
| `review` | A project note and an interpretation window (dates) | What moved; what stalled; open decisions; next actions |

- **Team pool**:
  - `retriever` (static): evidence candidates through C3, limited to `approve` notes regardless of request origin or model.
  - `connector` (model): related prior thinking, connections and tensions.
  - `challenger` (model): gaps, counterpoints and open questions.
  - `synthesizer` (model): the report.
- **Manager**: ThinkingManager composes the Graph. The `synthesizer` is always the last producer.
- **Report**: rendered by the Owner from the `synthesizer` submission.
  - Its language follows the source note.
  - Citations render as wikilinks with line locators and short content hashes.
  - Every claim is labeled cited, inference or not found.
  - Its inputs and inherited policy cover the focal note and every note read in the Work, not only the notes ultimately cited.
  - It states when any version of an input read in the Work differs from the current note.
- **Review window**: in `review` mode, notes linked to the project or in its folder are selected when their creation or modification time falls in the window. It is not a task-completion filter.
- **Criteria**:
  - Static: every citation handle was read in this Work; every claim is labeled; the mode's sections are present.
  - Semantic: the report addresses the focal note or question, and its stated tensions are supported by the cited notes.
- **Refuses**: a `deny`, unknown-policy or missing input. A question with no readable notes completes at admission as "not covered" with no Work and no report.

Acceptance:

- **ENH-1** Given an idea note and three related notes, when `develop` runs, then a report exists in `01_AI_Review/Reports/` whose every citation resolves to bytes read in this Work, and the idea note is unchanged.
- **ENH-2** Given the `synthesizer` submits an unlabeled, uncited claim, then the submission fails validation once, and if it fails again the Work ends unmet with no report written.
- **ENH-3** Given the user edits the idea note while the Work runs, then the report cites the snapshot and states that the input changed.
- **ENH-4** Given input notes with `ai_policy: approve` and with no `ai_policy`, then the report's `ai_policy` is `approve`.
- **ENH-5** Given an earlier report in `01_AI_Review/Reports/`, then no role's search returns it.
- **ENH-6** Given the EvalOfficer finds the tensions unsupported, then the ThinkingManager composes one successor with `connector` and `synthesizer` addressing only that gap, and the final report comes from that successor.
- **ENH-7** Given a completed Work, then a TeamReview exists that rates each producing teammate and changes no role, catalog entry or assessment.
- **ENH-8** Given System One is configured and a `develop` Work, when the `connector` calls `system_one` with choices and then without choices on a related note it read, then it gets a choice and a predicate answer computed from that note's content, an unread handle answers `not_read` with nothing sent, the eleventh request in the step answers `limit_reached`, a note that turned `deny` answers `denied` with nothing sent, and no answer text appears in the report.

## C5 Document management

The StewardshipOfficer keeps the Vault healthy and surfaces what deserves attention. Its only output is lifecycle proposals.

- **Bases views** (Vault assets in `99_System/Bases/`, maintained with KnowledgeOS conventions):
  - Unprocessed captures older than 3 days.
  - Ideas untouched for 30 days.
  - Open questions.
  - Proposals waiting.
  - Reports untouched for 14 days.
  - Projects by status.
- **Computed attention items** (console and digest), for what Bases cannot express:
  - Duplicate IDs, conflict markers and invalid YAML.
  - Stale proposals.
  - Recent autofix changes (console only, never the digest).
  - Unknown effect outcomes and failed or blocked jobs. Blocked jobs remain console-only; the digest includes latest-run failures only as C8's supplemental line.
  - A small rotating "revisit" set of older ideas and open questions for the digest.
- **Lifecycle proposals** come from static rules. For example, a `done` project or a `dropped` idea gets a proposal to become `archived` in `90_Archive/`.
- **System One status proposals**: when System One is configured, the digest run also asks one predicate per candidate note and proposes a status change only when it holds at `min_confidence`: an `open` question that already contains its answer becomes `answered`; a `planned`, `active` or `paused` project that states it is finished becomes `done`; a `seed`, `developing` or `mature` idea whose author abandoned it becomes `dropped`. Each is an ordinary single-target lifecycle proposal whose checklist item names System One and the probability; the note itself is never written. Only notes whose current policy is `approve` are sent, never `deny`, conflict, duplicate-ID, system or review notes, and a target with an open or stale proposal is neither asked about nor proposed again. A note is asked again only when its content changed since it was last asked (State `lifecycle_seen`, written after the Work's effects); a failed call records nothing, so the next run asks again.
- **Ordinary note operations** (create, move, rename, delete) stay in Obsidian. KnowledgeOS follows moves by ID and treats a deleted target's proposals as stale.

Acceptance:

- **DOC-1** Given a capture created 4 days ago with status `unprocessed`, then it appears in the inbox Bases view and in the digest count, and it disappears once its status changes.
- **DOC-2** Given two notes with the same `id`, then both stay searchable, a computed item names both, and an organize request targeting that ID is refused.
- **DOC-3** Given a note with merge-conflict markers, then all automation skips it and a computed item lists it.
- **DOC-4** Given a project with status `done`, then exactly one archive proposal exists for it until the user decides.
- **DOC-5** Given an `approve` open question whose body answers it, a `deny` open question and an `approve` idea judged not abandoned, when the digest runs with System One configured, then exactly one proposal sets `status: answered` for the `approve` question, the `deny` note's text is never sent, the idea gets no proposal, no note changes, and a second run sends nothing for unchanged notes; with System One down the run completes with only the static proposals.

## C6 Review

The user decides on KnowledgeOS output inside Obsidian.

- **Queue**: the review Bases view lists proposals and reports. A proposal note shows its checklist and diff, and Note Toolbar offers the "적용" and "거절" commands on notes with `type: proposal`.
- **Apply** is one explicit action. The Owner records the decision first. It then applies the checked changes only if the target's current SHA-256 equals the recorded preimage, and deletes the proposal file. On a preimage mismatch, the decision stays recorded as accepted-not-applied, the proposal is marked stale, and the notice offers to request it again.
- **Reject** records the decision and deletes the proposal file.
- **Undo** ("자동 변경 되돌리기" on the current note) restores the recorded preimage of the last autofix or manual standardization, but only if the note still equals that change's result. Otherwise it refuses and points to Git history.
- **Expiry**: stale proposals are deleted after 7 days.
- **Preservation**: an open or stale proposal reserves its target until the user decides or stale expiry occurs. Regeneration is the sole create-only exception: it requires the exact bytes still recorded as Owner-written and preserves user edits, including checklist edits. A State-unrecorded proposal is never overwritten or duplicated. An explicit new user request may refresh an unchanged stale proposal under the same checks.
- **Recorded outcomes**: every decision is a `HumanActionResponse`. Outcomes distinguish received, applied, not applied and unknown. A lost response is resolved by reading the request ID's recorded outcome, never by resending.

Acceptance:

- **REV-1** Given a proposal and an unchanged target, when the user applies it, then exactly the checked changes appear in the target and the proposal file is gone.
- **REV-2** Given the target was edited after the proposal was created, when the user applies it, then the target is unchanged, the proposal is stale and the decision is recorded.
- **REV-3** Given an autofix followed by a user edit to that note, when undo is requested, then nothing is written and the notice points to Git history.
- **REV-4** Given the client times out after apply, when it reconnects, then it shows the recorded outcome for that request ID and nothing is applied twice.

## C7 Scheduled jobs

The Owner runs scheduled jobs itself, and only while it runs: there is no host scheduler, no Hermes cron and no per-minute process. One sleeping timer wakes at the next slot and re-reads the Owner clock at least every `tick_seconds`. KnowledgeOS source declares the jobs in `config/jobs.json`. Each run is a request with actor `schedule/<job>`, admitted by the Director like any other.

| Job | Default schedule | Admits |
|---|---|---|
| `standardize` | every hour at :00 | C2 automatic path over notes changed since the last run |
| `embed` | every hour at :30 | C3 `index.embed`: embeds notes that are new or changed since their last vectors |
| `curate` | daily 06:00 | C1 over new captures and untyped `approve` notes, at most 10 targets |
| `daily` | daily 07:00 | C12 handoff and reminder |
| `digest` | daily 08:00 | C5 computation and the C8 digest |

- Jobs are idempotent. A run where nothing changed writes nothing; the C8 digest is sent only when actionable, and the C12 reminder is sent at most once per day and only when it has items.
- **Schedule**: five-field cron expressions in Asia/Seoul time. Only the minute and hour fields vary (`*`, numbers, lists, ranges, steps); day, month and weekday are `*`. An unsupported schedule stops the Owner at start. A job's slots are its scheduled times.
- **Only while the Owner runs**: nothing triggers while the Owner is stopped, and nothing is lost. A job is due when its latest slot is later than its last run (including a run by `knowledgeos job run`) and than any slot the Owner already consumed. A job the Owner sees for the first time has no missed slot.
- **Startup catch-up**: nothing triggers during the first `startup_delay_minutes` after the Owner starts. Then each due job runs once, however many of its slots passed while the Owner was stopped, and afterwards runs at its slots. A slot reached while the job's previous run is still active is refused and consumed.
- **One run per slot**: the trigger request ID of a slot is `schedule-<job>-<YYYYMMDDTHHMM>` (Seoul time), so a restart never triggers the same slot twice, and a trigger already committed for the slot is the slot's run.
- **Pause**: `job.pause` and `job.resume` (console). A paused job's slots pass without a trigger, and resuming does not catch them up. A manual run of a paused job is refused.
- **Settings**: `scheduler_enabled` (default true; false triggers nothing automatically, while `knowledgeos job run` still works), `startup_delay_minutes` (default 3) and `tick_seconds` (default 30). The timer only compares the clock; it creates a request only when a slot is due.
- **Attention**: a failed job run, or a job with two slots passed and no run (the startup delay is pending or the Owner could not run it), becomes an attention item.
- **Receipts**: the job-trigger receipt records that the declared request was triggered, not that its execution succeeded. The triggered request is admitted separately and has its own execution receipt; CLI job runs follow that receipt to report the result.

Acceptance:

- **JOB-1** Given two new notes since the last run, when `standardize` runs twice, then the second run writes nothing.
- **JOB-2** Given 15 eligible notes, then one `curate` run targets at most 10 of them, and the rest are eligible for the next run.
- **JOB-3** Given a model route is unavailable, then the `curate` Work fails visibly, creates no proposals and is not retried automatically.
- **JOB-4** Given the Owner was stopped while three hourly slots of `standardize` passed, when it starts, then nothing runs until the startup delay has elapsed, then `standardize` runs exactly once with actor `schedule/standardize`, and while the Owner keeps running its next slot runs at its time.
- **JOB-5** Given a slot whose run was triggered, when the Owner restarts, then that slot does not run again.
- **JOB-6** Given a paused job, or `scheduler_enabled` false, when slots pass, then no run is triggered, and resuming the paused job runs it at its next slot, not at once.
- **JOB-7** Given the declared jobs, then `embed` runs hourly at :30 and admits C3 `index.embed`, and the scheduler settings default to `scheduler_enabled` true, `startup_delay_minutes` 3 and `tick_seconds` 30.

## C8 Notifications

Outbound Discord notices through the KnowledgeOS bot, only when the user has something to act on. The StewardshipOfficer composes digest content and the Notifier delivers it. The morning task reminder belongs to C12.

- **Digest**: the `digest` job runs at 08:00 after C5 stewardship, with title `KnowledgeOS 검토 요약`. Its sections and outcome `counts` keys are:
  - `proposals` (`검토할 제안`): open or stale proposals, with titles.
  - `stale_proposals` (`오래된 제안`): stale proposals.
  - `reports` (`새 보고서`): reports whose `created` is after the previous digest, or all reports on the first digest.
  - `inbox_older_3_days` (`3일 넘은 포착`): unprocessed captures older than 3 days.
  - `pending` (`확인 대기 작업`): Works with status `awaiting_confirmation`, counted with the console link `#works`.
  - `attention` (`확인 필요`): non-job items of kinds `conflict`, `duplicate_id`, `invalid_yaml` and unresolved `unknown` effect outcomes.
  - `revisit` (`다시 볼 노트`): the rotating set of older ideas and open questions.
  Only non-zero sections get a line. There is no `autofix` section; recent autofix remains in console attention. A notice is posted only when at least one of `proposals`, `reports`, `inbox_older_3_days`, `pending` or non-job `attention` is non-zero; a revisit set or failed jobs alone send nothing. Record a separate `failed_jobs` count on every run, counting only jobs whose latest run failed. Only when another actionable section triggers posting, append those failures as one supplemental line, `자동 작업 점검 필요: <job names>`. A quiet run still returns `counts` and `notice_id: null` and advances the digest cursor.
- **Alert** (immediate, deduplicated by item ID): only a Vault `conflict` or an unresolved `unknown` outcome of a Vault write (`effect`), human `action`, `cancellation` or commit (`runtime_phase` with `ref.phase` or any `ref.phases` entry starting with `commit`). Model-step, request and delivery unknowns remain in console/API attention but never alert. Failed, blocked and missed jobs never alert. The Owner observes changes independently of reads; no GET, console visit or digest is needed to trigger an alert.
- **Content**: titles and counts only, `obsidian://` links to eligible notes and console links for management items. Only `approve` note titles and links may be disclosed. No body text or identifying information from `deny` or unknown-policy notes appears in either digests or immediate alerts. C12 separately permits open-item text from `approve` notes in its reminder.
- **Delivery**: the Owner records each notice intent before delivery. An unknown delivery is never re-sent. The digest reflects current state, so the next actionable digest carries anything still relevant.

Acceptance:

- **NTF-1** Given an attention item from a `deny` note, then no notice mentions it.
- **NTF-2** Given the same unresolved conflict on two days, then one alert was sent and both digests list it.
- **NTF-3** Given a notice with an unknown delivery outcome, then it is not re-sent.
- **NTF-4** Given a failed scheduled job and nothing to review, then no alert is sent and the digest posts nothing, while `failed_jobs` is counted separately from non-job `attention`; given an open proposal next, the digest lists it with the supplemental failed-job line.
- **NTF-5** Given a classifier model-step `runtime_phase` unknown and a `role_invocation` unknown, then both remain in `/api/attention` without alerting; given an unknown Vault write effect, then the observer alerts exactly once.

## C9 Learning and Experience

- **Role level**: each model role learns in its own Hermes profile (Blueprint §4.5). Its `feedback` view reports outcomes of its own earlier outputs:
  - Applied, partially applied or rejected changes, for the `classifier` and `linker`.
  - Report edits, promotion or deletion, for the `synthesizer`.
  - Assessments and TeamReview history, for the Managers.
  - User rejection of results it rated satisfied, for the EvalOfficer.
  - Successful admissions and clarification failures, for the Director.
- **Team level**: TeamReview (C4, ENH-7) records teammate performance and proposals in TeamReview, role feedback and the console. Each proposal also creates a local `ExperienceSourceEvent` with `category=routine_progress` and `evaluation_state=not_evaluated` for the proposal, regardless of the Work's assessment.
- **Local-only proposals**: ordinary TeamReview proposals do not create an `ExperienceCandidate` or outbox item and are not sent to HermesTrace. Local observations are bounded and expire under C9 retention; they are not a permanent promotion queue.
- **Fabric level**: actual eligible events create an `ExperienceSourceEvent` and an `ExperienceCandidate` for delivery to HermesTrace through InterOps. The Owner uses the existing Core categories and the event's evidence, not the presence of a suggestion or a successful Work, to establish eligibility. Signals include:
  - A Work that ended unmet, failed or unknown.
  - A proposal that was rejected or partially applied.
  - An undo.
  - A blocked job.
  - A privacy refusal.
  These signals must evidence the applicable Core category: `failure`, `timeout`, `crash_recovery`, `cancellation`, `human_correction`, `policy_denial` or `verified_unusual_success`. An unknown outcome alone is not proof of failure, and an unevaluated proposal is not a verified success.
- **Later events**: a real user correction or other eligible event creates its own evidenced event and Candidate. It does not automatically promote an earlier proposal; no future promotion or export of that proposal is promised.
- **Payloads and retention**: SourceEvents and Candidates carry references, categories and digests, never note text. Local observations and the outbox are bounded. If HermesTrace is unavailable, eligible candidates wait and expire under Core retention without blocking KnowledgeOS. Neither local observations nor Candidates widen source-note disclosure policy.
- **Adoption** of a HermesTrace-promoted capability is an explicit, pinned change to KnowledgeOS configuration; Core contracts and schemas remain unchanged.

Acceptance:

- **LRN-1** Given a rejected curation proposal, then the next `classifier` invocation's `feedback` includes it, and an Experience candidate exists without note text.
- **LRN-2** Given a `classifier` skill update, then other role profiles' skills are unchanged.
- **LRN-3** Given HermesTrace is unreachable, then user capabilities are unaffected and the candidate stays pending.

## C10 Gateway

KnowledgeOS is a foundational Operation: other Operations may submit information to it, and ExecutiveOffice may query it.

- **Intake**: the Director admits a submission from an authorized Operation. The Owner creates a `capture` note in `00_Inbox/` with `ai_policy: approve`, `captured_from: operation/<id>` and flat provenance fields `kos_provenance` (message ID), `kos_provenance_sender` and `kos_provenance_received_at`; C2 and C1 then apply to it as to any capture. Intake is an Owner effect, not a new Officer. A submission whose export policy forbids storage in a Git-synchronized Vault is refused with an explicit error record.
- **Query**: bounded C3 results over `approve` non-report notes only, to authorized recipients. The request supplies sender, purpose and query, and may narrow configured grants with result and excerpt limits, never widen them (at most 100 results and 160 excerpt characters per result). Results are titles, locators and short excerpts within those limits.
- **Receipts**: committed before acknowledgment. A duplicate submission returns the original receipt.

Acceptance:

- **GW-1** Given a valid submission, then exactly one capture note exists, and resubmitting it returns the same receipt.
- **GW-2** Given a query matching a `deny` note, then that note is not in the result.

## C11 Invocation and management surfaces

**Thin Client commands.** These remain the client's entire functional surface, available from the command palette, context menus and one native Obsidian sidebar tab.

| Command | Context | Capability |
|---|---|---|
| 규격화 미리보기 | Note, selection, folder | C2 manual |
| 정리 요청 | Note, selection, folder | C1 |
| 강화: 발전 / 묻기 / 돌아보기 | Note; question input; date window | C4 |
| 자유 요청 | Free text | Director routing |
| 관련 노트 · 의미 검색 | Note; query input | C3 |
| 적용 · 거절 | Proposal note | C6 |
| 자동 변경 되돌리기 | Note | C6 |
| 관리 콘솔 열기 | Any | Opens the browser console |
| Owner 시작 · 중지 | Any (desktop) | Owner lifecycle |

**Sidebar.** A ribbon icon or `사이드바 열기` reveals the reusable right-sidebar tab. Four compact buttons (규격화, 정리, 강화, 찾기) select one inline function region below them; navigation alone never submits work. Secondary choices, question and date inputs, semantic search, linked search results, standardization diffs and explicit confirm/cancel controls stay inside this region, never in popup menus or modals. More selects an inline free-request form with undo and console actions. Applying or rejecting appears only for a proposal note; one power button invokes Owner start or stop according to the observed connection state. It never starts the Owner on open. Focusing the sidebar retains the displayed target; opening another file updates it, and deleting the target clears it. With no Markdown target, note-only execution is disabled, while 묻기, 의미 검색 and 자유 요청 remain available. Unsubmitted input drafts remain only in memory. Changing the function or note cancels an undecided preview, including a late preview response; unrelated late results cannot replace the new function region. Request feedback and result links stay inline. Existing palette and context-menu commands retain their modal presentation and share the same request and confirmation paths. The tab adds no Work browser, persistent document lists, settings, authority or persistent navigation state.

**Console and CLI** behave as in Blueprint §3.2 and §3.3.

- **Free text**: when System One is configured, the Owner first sends only the request text to it as one `free_text_command` choice over the offered free-text commands plus `none_of_these`. A `none_of_these` answer at or above `min_confidence` refuses the request at once with no Director session and no Work, and the notice says System One found no KnowledgeOS command for it. Any other answer, or an unconfigured, failing, refusing or low-confidence System One, continues to the Director unchanged; the Director never sees the hint. The receipt outcome records the prefilter as `system_one` (`{choice, confidence}` or `unavailable`). The model Director may map only to catalog-allowed standardization preview, organize or enhance commands, not job, review or Work-management actions. It sees policy-filtered references to notes the user named, not note bodies; the Owner supplies paths and digests and performs static admission. Attempts to set criteria, budget, target authority, WorkSpec, input bindings or routes are authority violations: no corrective turn and no Work. Ordinary invalid drafts get one corrective turn; refusal, unavailable route or unknown outcome admits no Work and is reported explicitly.
- **Read safety and recovery**: GETs never cause effects. After a lost response, the client looks up the original receipt; it may resend the identical request only after the Owner reports that the request was never recorded. A recorded unknown is not permission to replay.
- **Console actions**: cancellation reports the observed application outcome after a bounded observation window, otherwise unknown. Reconciliation records an explicit human observation of applied or not applied without rewriting historical Work, receipts or unknown evidence.
- **Owner lifecycle**: the Owner runs on the host as an on-demand macOS user service, so it outlives Obsidian. `knowledgeos install service --config FILE` writes its LaunchAgent plist (`RunAtLoad` and `KeepAlive` false, logs under Runtime) and loads it without starting it; `--dry-run` prints the plist and `launchctl` argv and changes nothing. `Owner 시작` kickstarts that launchd label and `Owner 중지` sends it SIGTERM; each reports only what `GET /api/health` then shows: started once health is ready, stopped once it stops answering, otherwise still running or unknown. They act only when the user invokes them and send no note text. `serve` closes the Owner and ExOps on SIGTERM.

Acceptance:

- **SUR-1** Given any Thin Client command, then the request carries the note path and last-seen digest, never note body text.
- **SUR-2** Given the user opens notes, switches files and reconnects, then the client writes nothing to its settings.
- **SUR-3** Given a free-text request the Director cannot map to a capability, then no Work is admitted, and the notice says why.
- **SUR-4** Given the console's "cancel Work" action, then it goes through ExOps admission and the outcome is shown as received, then applied or unknown.
- **SUR-5** Given `standardize --dry-run` on a folder from the CLI, then the planned changes are printed and nothing is written.
- **SUR-6** Given the Owner's LaunchAgent, then `Owner 시작` kickstarts its launchd label and reports started only after `/api/health` is ready, `Owner 중지` sends SIGTERM and reports stopped only after health stops answering (otherwise still running or unknown), neither sends anything but health GETs, and `serve` exits cleanly on SIGTERM.
- **SUR-7** Given `install service --dry-run` with a config, then the plist and `launchctl` argv are printed and nothing is written; given a real install, the plist is written and the service is loaded but not running.
- **SUR-8** Given the user opens the sidebar repeatedly and switches between notes and the command tab, then one tab retains the visible note target, requests use that target's current path and digest, proposal decisions follow its current frontmatter, unavailable note actions cannot submit requests, and merely opening the tab never starts or stops the Owner.
- **SUR-9** Given sidebar navigation, then function selection opens only inline controls without submitting requests or opening menus or modals; input validation, search results, outcome links and standardization confirmation remain inline, repeated confirmation submits once, and leaving a pending preview cancels its exact identity without applying it even if its response arrives late.
- **SUR-10** Given System One answers `none_of_these` with confidence 0.9 for "오늘 날씨 알려줘", then the free-text request settles `refused` with no Work, the Director is never invoked and only the request text was sent; given it chooses a command, answers below the threshold or is down, then the Director is invoked and the outcome is as without System One.

## C12 Daily notes

The static DailyOfficer hands unfinished items forward into today's daily note; the static EvalOfficer checks `day.carry_exact`. Daily notes (`type: daily`) have no `status` and are never AI proposal targets.

- **Triggers and scope**: `daily.run` with empty parameters, from the user or the C7 `daily` job at 07:00. `settings.C12.folder` defaults to `10_Journal/Daily`. Today is the Owner clock's date in Asia/Seoul; the destination is exactly `<folder>/YYYY-MM-DD.md`. One segment and one producer invocation.
- **Sources**: `approve` notes directly in that folder whose stems are valid `YYYY-MM-DD` dates earlier than today, in ascending date order. Skip `deny` or unknown-policy notes, conflict markers, invalid YAML, undecodable files and paths with a skip reason.
- **Open items**: non-empty top-level checkbox lines matching `^([-*+]|\d+[.)]) \[( |/)\] +\S`, together with immediately following indented, non-blank continuation lines. A blank or non-indented line ends the item. Fenced code blocks (triple backticks) and HTML comments are not scanned. Done (`[x]`), cancelled (`[-]`), forwarded (`[>]`) items and plain bullets never carry.
- **Classification and order**: an item belongs to schedule when the nearest preceding Markdown heading text starts with `일정`; every other open item is a task, including items before a heading or under `할 일`, `작업` or any other heading. Carry all open items in source-date order, then document order. If an item's first line already appears verbatim as a line in today's note, do not insert it again, but still mark its source.
- **New-note layout**: create exactly the destination path, never a suffixed name, with LF bytes and the same four-section layout as `T10_Daily.md` below. `<uuid4>` is a new UUIDv4 and `<now>` is the Owner clock in ISO-8601 with offset. Insert carried tasks after the comment in `할 일` and schedules after the last comment in `일정`; empty sections retain the exact template bytes, with one final newline. The result needs zero C2 changes.

  ```markdown
  ---
  id: "<uuid4>"
  type: daily
  created: <now>
  tags:
    - journal/daily
  ai_policy: approve
  sensitivity: personal
  ---
  ## 메모
  <!-- - 떠오른 생각, 들은 말, 나중에 볼 링크를 한 줄씩 -->

  ## 할 일
  <!-- - [ ] #task 할 일 이름 📅 YYYY-MM-DD -->

  ## 일정
  <!-- - HH:MM 일정 제목, 위치 -->
  <!-- - [ ] YYYY-MM-DD HH:MM 일정 제목, 위치 (끝날 때까지 다음 날로 넘어감) -->

  ## 하루 정리
  - 오늘 가장 잘한 일:
  - 아쉬웠던 점과 그 이유:
  - 새로 알게 된 것:
  - 고마웠던 사람이나 순간:
  - 내일 가장 먼저 할 일:
  ```

- **Existing today**: preserve every existing line in order. Insert schedule items in the first section whose heading text starts with `일정`; tasks in the first section starting with `할 일`, otherwise `작업`. Insert immediately after that section's last non-blank line, or directly after its heading if empty. Append a missing section at EOF as `\n## 일정\n` or `\n## 할 일\n` plus items, first ensuring a final newline.
- **Effects and refusals**: only the Owner's `EffectContext` writes, with recorded intent before each effect. Write today first, using exact creation when absent or an exact-preimage replacement when present. If the path appeared meanwhile, write nothing and mark no source; retry on the next run. A `deny`, conflict-marked or invalid-YAML destination receives nothing and no source is marked. Only after today's write applied, or no insertion was needed, mark carried items in each source with its own exact-preimage replacement: change only the checkbox character from space or `/` to `>`, leaving every other byte unchanged. A mismatch is `not_applied`, never overwritten, and is reconsidered next run.
- **Static criterion** (`day.carry_exact`): every carried item is an open top-level checkbox item from an admitted earlier daily note, today's note retains every existing line in order and only gains carried items, and each source changes only the carried items' checkbox marks to `[>]`.
- **Reminder**: after effects, `owner.post_notice` posts kind `daily`, item ID `daily/YYYY-MM-DD/<today's note id>`, at most once for that ID, so a daily note deleted and recreated for the same day gets its own reminder. Read open top-level `#task` items from today's current on-disk note in document order, then open `#task` items carrying `📅 YYYY-MM-DD` due today or earlier from other notes outside the daily folder, `99_System/` and `01_AI_Review/`. A note may disclose tasks only when its current effective policy is not `deny`; never mention denied notes, even as counts. Show task names only: remove the bullet, checkbox, `#task`, Tasks emoji date fields (`📅`, `⏳`, `🛫`, `➕`, `✅`, `❌` and their dates), priority fields (`⏫`, `🔼`, `🔽`, `🔺`, `⏬`) and recurrence fields (`🔁` and its rule), collapse whitespace and skip empty names; keep other tags. Use editable `settings.reminder` in `config/capabilities/C12.json`: a warm Korean greeting as the title (month, day, Korean weekday and total task-name count), task lines with due-today `(오늘까지)` or overdue `(기한이 지났어요)` marks, then a blank line and a reassuring closing. Include at most 20 task lines, followed by the configured `more` line for remaining task names. Include no links (`links=[]`) or Obsidian URLs. No `#task` item means no notice.
- **Outcome**: `date`, `path`, `created` (boolean), `carried` (count), `marked` (source paths), `not_applied` (paths) and `notice_id` (string or null).

Acceptance:

- **DAY-1** Given 2026-11-19 and 2026-11-20 daily notes with open tasks, a done task and an open schedule item and no note for today, when `daily` runs, then today's note is created with the schedule item under 일정 and the open tasks under 할 일, the carried lines in the sources become `[>]`, done items stay, and no other byte changes.
- **DAY-2** Given today's note already exists with user text and one of the open items, then the other items are inserted at the end of the matching sections, every existing line keeps its order, the duplicate is not inserted but its source is marked, and a second run writes nothing.
- **DAY-3** Given a source changes between admission and write, then that source is not written and is reported not applied, and the next run carries the remaining open items without duplicating ones already carried.
- **DAY-4** Given today's note has open `#task` items and a non-task schedule item, another note has a `#task` due yesterday and a `deny` note has a due task, then one notice greets the user, lists only the task names without `#task`, dates or links, marks the overdue one, never mentions the deny note or the schedule item, and a second run the same day sends no notice.
- **DAY-5** Owner-created daily note and the Vault template `T10_Daily.md` both get zero C2 changes.

## Follow-up: mobile

These are reserved and specified when started. Processing always happens in the Mac KnowledgeOS. Mobile only creates, edits, marks and syncs.

- **M1 Templates and sync shortcuts.** Apple Shortcuts create capture, idea and question notes from the Vault templates, with conforming frontmatter including `id`, and drive Working Copy pull, commit and push. Acceptance to be written: a Shortcut-created note passes STD-7.
- **M2 Vault-borne requests.** A note marked `kos_request: <capability[:mode]>`, or a request note in `00_Inbox/Requests/`, is admitted by the Director after the Mac pulls. Results are written as usual, and the request marker is cleared through the C2 write path. To decide: request identity across devices, duplicate marks, and how the result is announced (digest or alert).
- **M3 GitHub Actions.** Candidates are deterministic checks on push (YAML validity, conflict markers, duplicate IDs) reported as a check status or issue. Constraints: no model calls, no KnowledgeOS credentials, and no commits unless Blueprint §5.7 is revised to admit a second Git writer.

## Delivery order

Build in thin slices and revise this document after each one. The console and CLI grow with each slice.

1. **Owner core, Director (static), StandardizationOfficer and static EvalOfficer**: C2 manual path and CLI batch, the C3 service, C11 basics, and a rebuildable project development image that keeps `make image-id` and `make state-check` working.
2. **Scheduling and attention**: C7 `standardize` and `digest`, C8, C5.
3. **Thinking Team**: C4 with ThinkingManager, its teammates, the model EvalOfficer and TeamReview.
4. **Curation Team**: C1 with C6 review, then the `curate` job.
5. **Fabric**: C9 Experience and C10 Gateway.
6. **Daily handoff**: C12 and the `daily` job, with actionable morning reminders.
7. **Mobile**: follow-ups M1 to M3.
