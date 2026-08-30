/**
 * The shapes the read API returns.
 *
 * Written against `packages/api/attention_sink/api/schemas.py` and kept in step with
 * it by `apps/web/src/api/contract.test.ts`, which checks these fields against the
 * live OpenAPI document rather than against a copy of it. A type that drifted from
 * the server would otherwise be discovered by a blank panel in the exhibition.
 */

export const PROVENANCE_LABELS = [
  'LOCAL_FIXTURE',
  'NON_CANONICAL',
  'SIMULATED_MODEL_OUTPUTS',
] as const;

export interface ApiEnvelope<T> {
  schema_version: number;
  data: T;
  simulated: boolean;
  labels: string[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface RunSummary {
  run_id: string;
  run_kind: string;
  status: string;
  current_cycle: number;
  maximum_cycles: number;
  checkpoint_cycles: number[];
  arms: string[];
  memory_budget_tokens: number;
  token_count_source: string;
  protocol_version: string;
  writer_prompt_version: string;
  summary_prompt_version: string;
  prompt_set_digest: string;
  simulated: boolean;
  created_at: string;
  updated_at: string;
}

export interface MemoryView {
  memory_id: string;
  text: string;
  memory_kind: string;
  birth_cycle: number;
  token_count: number;
  citation_count: number;
  pinned: boolean;
}

export interface ArmSummary {
  arm_id: string;
  active_memory_count: number;
  active_tokens: number;
  budget_tokens: number;
  retired_memory_count: number;
  state_hash: string;
  active_memories: MemoryView[];
}

export interface CycleView {
  arm_id: string;
  cycle: number;
  stimulus_id: string;
  stimulus_text: string;
  journal_entry: string;
  candidate_memory: string;
  candidate_memory_id: string;
  validated_citation_count: number;
  rejected_claim_count: number;
  retired_memory_ids: string[];
  compressed_memory_ids: string[];
  created_summary_id: string | null;
  summary_source_memory_ids: string[];
  tokens_before: number;
  tokens_after: number;
  budget_tokens: number;
  policy_version: string;
  policy_decision_codes: string[];
  prompt_versions: Record<string, string>;
  prompt_hashes: Record<string, string>;
  state_hash: string;
  snapshot_hash: string;
  simulated: boolean;
  run_kind: string;
}

export interface GraveyardView {
  arm_id: string;
  memory_id: string;
  text: string;
  memory_type: string;
  birth_cycle: number;
  retirement_cycle: number;
  lifespan: number;
  status: string;
  validated_citation_count: number;
  last_cited_cycle: number | null;
  retirement_reason: string;
  policy_version: string;
  snapshot_evidence: string;
  summary_descendant_id: string | null;
  genuinely_inaccessible: boolean;
  nearest_future_echo_id: string | null;
}

export interface InterviewAnswer {
  question_id: string;
  answer: string;
  cited_memory_refs: string[];
  stated_uncertainty: string;
}

export interface InterviewView {
  arm_id: string;
  cycle: number;
  interview_version: string;
  question_set_version: string;
  answers: InterviewAnswer[];
  reported_memory_ids: string[];
  prompt_hash: string;
  input_state_hash: string;
  record_hash: string;
  completed_at: string;
}

export interface MetricRow {
  run_id: string;
  arm_id: string;
  cycle: number;
  metric_name: string;
  value: number;
  evaluator_version: string;
  calculation_version: string;
  cited_memory_ids: string[];
  rationale: string;
  computed_at: string;
}

export interface EchoRow {
  run_id: string;
  arm_id: string;
  cycle: number;
  memory_id: string;
  forgotten_similarity: number;
  active_similarity: number;
  echo_delta: number;
  nearest_forgotten_memory_id: string | null;
  nearest_active_memory_id: string | null;
  category: string;
  threshold: number;
  metric_version: string;
  evaluator_version: string | null;
  evidence_excerpt: string;
}

export interface ContradictionRow {
  run_id: string;
  arm_id: string;
  cycle: number;
  question_id: string;
  label: string;
  fact_ids: string[];
  supporting_excerpt: string;
  method: string;
  metric_version: string;
  evaluator_version: string | null;
}

export interface QuestionScoreRow {
  arm_id: string;
  cycle: number;
  question_id: string;
  fact_ids: string[];
  score: number;
  method: string;
  matched_fact_ids: string[];
  matched_terms: string[];
  supporting_excerpt: string;
  evidence_memory_ids: string[];
  metric_version: string;
  evaluator_version: string | null;
  importance: number;
}

export interface Lineage {
  parents: string[];
  children: string[];
}

export interface DivergenceMatrices {
  matrices: Record<string, Record<string, Record<string, number>>>;
}

export interface ExportManifest {
  run_id: string;
  export_id: string;
  run_kind: string;
  directory: string;
  files: Record<string, string>;
  labels: string[];
  created_at: string;
}
