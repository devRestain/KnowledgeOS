<!-- GENERATED: BEGIN knowledgeos-property-dictionary -->
# Property Dictionary

이 파일은 Blueprint registry에서 생성된 C06 strict note contract의 검증된 사본이다.

- contract: `knowledgeos-blueprint-v2`
- capability profile: `portable_core`
- authoritative input: `blueprint/blueprint.yaml` (SHA-256 `bdae82d1754783f23243f325214cf1f0a794aae53767ed90b128f7cee8105d46`)
- property count: `76`

| Property | Obsidian type | Constraints | Owner |
|---|---|---|---|
| `ai_policy` | `text` | `{"enum":["remote_ok","ask","local_only","deny"]}` | `common_properties.required` |
| `ai_status` | `text` | `{"enum":["idle","queued","proposed","approved","applied","rejected","conflict","expired","error"]}` | `common_properties.required` |
| `aliases` | `list` | `{"item_type":"text"}` | `common_properties.required` |
| `applies_to` | `list` | `{"item_type":"quoted_wikilink","object_type":"project_or_idea"}` | `property_registry` |
| `areas` | `list` | `{"item_type":"quoted_wikilink","object_type":"area"}` | `property_registry` |
| `artifact_hash` | `text` | `{}` | `property_registry` |
| `artifact_kind` | `text` | `{"enum":["specification","prototype","report","presentation","code","dataset","other"]}` | `property_registry` |
| `artifact_repo` | `text` | `{}` | `property_registry` |
| `artifact_uri` | `text` | `{}` | `property_registry` |
| `asset` | `text` | `{"format":"quoted_wikilink_to_80_assets"}` | `property_registry` |
| `asset_hash` | `text` | `{"format":"sha256"}` | `property_registry` |
| `attendees` | `list` | `{"item_type":"quoted_wikilink_or_text"}` | `property_registry` |
| `audience` | `text` | `{"enum":["desktop","mobile","system"]}` | `property_registry` |
| `authors` | `list` | `{"item_type":"text"}` | `property_registry` |
| `canonical_date` | `date` | `{}` | `property_registry` |
| `canonical_timezone` | `text` | `{"format":"iana_timezone"}` | `property_registry` |
| `capture_device` | `text` | `{"enum":["mac","iphone","ipad"]}` | `property_registry` |
| `capture_kind` | `text` | `{"enum":["thought","url","quote","voice","file","other"]}` | `property_registry` |
| `capture_label` | `text` | `{}` | `property_registry` |
| `captured_from` | `text` | `{"enum":["mac_quickadd","mac_cli","ios_shortcut","ipad_shortcut","ios_share_sheet","ipad_share_sheet","import"]}` | `property_registry` |
| `citation_key` | `text` | `{}` | `property_registry` |
| `claim` | `text` | `{"min_length":1}` | `property_registry` |
| `confidence` | `text` | `{"enum":["low","medium","high","unknown"]}` | `property_registry` |
| `contradicts` | `list` | `{"item_type":"quoted_wikilink","object_type":"knowledge"}` | `property_registry` |
| `created` | `datetime` | `{"require_timezone":true}` | `common_properties.required` |
| `decision` | `text` | `{}` | `property_registry` |
| `decision_by` | `date` | `{}` | `property_registry` |
| `derived_from` | `list` | `{"item_type":"quoted_wikilink","object_type":"provenance_source"}` | `property_registry` |
| `device_timezone` | `text` | `{"format":"iana_timezone"}` | `property_registry` |
| `explains` | `list` | `{"item_type":"quoted_wikilink","object_type":"knowledge_or_question"}` | `property_registry` |
| `extractor` | `text` | `{}` | `property_registry` |
| `extractor_version` | `text` | `{}` | `property_registry` |
| `focus_rank` | `number` | `{"integer":true,"minimum":1}` | `property_registry` |
| `id` | `text` | `{"approved_deterministic_id_classes":["proposal_from_job_uuid","periodic_from_period_id","system_fixed_id"],"format":"uuid_v4_or_approved_deterministic_id","immutable":true}` | `common_properties.required` |
| `implements` | `list` | `{"item_type":"quoted_wikilink","object_type":"idea"}` | `property_registry` |
| `last_reviewed` | `date` | `{}` | `property_registry` |
| `meeting_at` | `datetime` | `{"require_timezone":true}` | `property_registry` |
| `modified` | `datetime` | `{"require_timezone":true,"semantics":"schema_writer_time"}` | `common_properties.required` |
| `needs_desktop_review` | `checkbox` | `{}` | `property_registry` |
| `next_action` | `text` | `{"min_length":1}` | `property_registry` |
| `next_review` | `date` | `{}` | `property_registry` |
| `note_kind` | `text` | `{"enum":["exploration","draft","research","plan","log","other"]}` | `property_registry` |
| `organization` | `text` | `{}` | `property_registry` |
| `outcome` | `text` | `{"min_length":1}` | `property_registry` |
| `page_locator_scheme` | `text` | `{}` | `property_registry` |
| `people` | `list` | `{"item_type":"quoted_wikilink","object_type":"person"}` | `property_registry` |
| `period_end` | `date` | `{}` | `property_registry` |
| `period_start` | `date` | `{}` | `property_registry` |
| `possibility` | `text` | `{"min_length":1}` | `property_registry` |
| `priority` | `text` | `{"enum":["high","medium","low"]}` | `property_registry` |
| `projects` | `list` | `{"item_type":"quoted_wikilink","object_type":"project"}` | `property_registry` |
| `proposal_id` | `text` | `{"format":"uuid_v4"}` | `property_registry` |
| `published_date` | `date` | `{}` | `property_registry` |
| `purpose` | `text` | `{"min_length":1}` | `property_registry` |
| `question_kind` | `text` | `{"enum":["decision","research","problem"]}` | `property_registry` |
| `raises` | `list` | `{"item_type":"quoted_wikilink","object_type":"question"}` | `property_registry` |
| `related` | `list` | `{"item_type":"quoted_wikilink","object_type":"any_note"}` | `property_registry` |
| `review_cadence` | `text` | `{"enum":["weekly","monthly","quarterly","semiannual","annual"]}` | `property_registry` |
| `schema_version` | `number` | `{"const":1}` | `common_properties.required` |
| `scope` | `text` | `{"min_length":1}` | `property_registry` |
| `sensitivity` | `text` | `{"enum":["public","personal","confidential"]}` | `common_properties.required` |
| `source_hashes` | `list` | `{"item_format":"vault_relative_path\|sha256:64hex","item_type":"text"}` | `property_registry` |
| `source_kind` | `text` | `{"enum":["paper","book","article","web","video","podcast","course","document","dataset","other"]}` | `property_registry` |
| `source_url` | `text` | `{"format":"uri"}` | `property_registry` |
| `sources` | `list` | `{"item_type":"quoted_wikilink","object_type":"source"}` | `property_registry` |
| `standard` | `text` | `{"min_length":1}` | `property_registry` |
| `status` | `text` | `{"enum_from":"note_types.TYPE.statuses"}` | `common_properties.required` |
| `supports` | `list` | `{"item_type":"quoted_wikilink","object_type":"knowledge"}` | `property_registry` |
| `tags` | `tags` | `{}` | `common_properties.required` |
| `target_date` | `date` | `{}` | `property_registry` |
| `title` | `text` | `{"equals_filename_stem":true,"min_length":1}` | `common_properties.required` |
| `today_focus` | `text` | `{}` | `property_registry` |
| `topics` | `list` | `{"item_type":"quoted_wikilink","object_type":"moc"}` | `property_registry` |
| `triage_hint` | `text` | `{"enum":["none","discard","hold","task","project","idea","question","knowledge","source"]}` | `property_registry` |
| `triage_projects` | `list` | `{"item_type":"quoted_wikilink","object_type":"project"}` | `property_registry` |
| `type` | `text` | `{"enum":["capture","proposal","daily","weekly","monthly","project","project_note","idea","question","artifact","area","knowledge","source","person","moc","meeting","home","system"]}` | `common_properties.required` |

<!-- GENERATED: END knowledgeos-property-dictionary -->
