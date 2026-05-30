// Otto AgentSpec — TS mirror of the Pydantic source of truth
// (packages/spec/src/otto_spec/models.py). Keep in sync; the JSON Schema in
// agent-spec.schema.json is the generated cross-language artifact.

export type PolicyCategory = "safety" | "booking" | "knowledge" | "voice_behavior";
export type Severity = "low" | "medium" | "high" | "critical";
export type PolicySource = "extracted" | "healed" | "seed";

export interface Business {
  name: string;
  type: string;
  location: string;
  timezone: string;
  public_url: string;
  disclaimer: string;
}

export interface Voice {
  persona: string;
  greeting: string;
  tts_voice: string;
}

export interface KnowledgeItem {
  topic: string;
  content: string;
  source_url: string;
  confidence: number;
}

export interface ToolParam {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

export interface ToolSpec {
  name: string;
  description: string;
  params: ToolParam[];
  mock_behavior: Record<string, unknown>;
}

export interface Policy {
  id: string;
  category: PolicyCategory;
  trigger: string;
  rule: string;
  severity: Severity;
  source: PolicySource;
}

export interface EscalationRule {
  id: string;
  condition: string;
  action: string;
}

export interface Meta {
  version: number;
  parent_version: number | null;
  created_at: string;
  updated_at: string;
  notes: string;
}

export interface AgentSpec {
  business: Business;
  voice: Voice;
  knowledge: KnowledgeItem[];
  capabilities: string[];
  tools: ToolSpec[];
  policies: Policy[];
  escalation_rules: EscalationRule[];
  safety_rules: string[];
  out_of_scope: string[];
  meta: Meta;
}

// ── live event stream (orchestrator SSE -> dashboard) ────────────────────────
export type StageId =
  | "extract"
  | "build"
  | "swarm"
  | "heal"
  | "rerun"
  | "activate";

export interface CallResult {
  persona: string;
  label: string;
  category: PolicyCategory;
  passed: boolean;
  reason: string;
  transcript: { role: "caller" | "agent"; content: string }[];
  latency_ms?: number;
}

export interface SwarmReport {
  round: number;
  spec_version: number;
  pass_rate: number;
  total: number;
  passed: number;
  results: CallResult[];
}

export interface PolicyDiff {
  change: "added" | "modified";
  policy_id: string;
  category: PolicyCategory;
  before: Policy | null;
  after: Policy;
}

export type OttoEvent =
  | { type: "stage"; stage: StageId; status: "start" | "done"; detail?: string }
  | { type: "fact"; topic: string; content: string } // extraction stream
  | { type: "call"; result: CallResult }
  | { type: "swarm_report"; report: SwarmReport }
  | { type: "patch"; round: number; diffs: PolicyDiff[] }
  | { type: "metrics"; pass_rate: number; unsafe: number; booking: number; escalation: number }
  | { type: "activated"; phone_number: string }
  | { type: "live_call"; field: string; value: string }; // live call console
