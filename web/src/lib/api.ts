// Talking to the FastAPI backend (app/server.py, `make app`, port 8000). SSE goes straight to it (a Next rewrite may buffer streams).
export const API = process.env.NEXT_PUBLIC_API ?? "http://localhost:8000";

export type Option = { value: string; label: string };
export type Missing = { slot: string; question: string; options: Option[] | null; free?: boolean };
export type Pill = { slot: string; label: string; value: string | null };
export type ChatResponse = {
  slots: Record<string, unknown>;
  ready: boolean;
  auto?: boolean;
  message?: string;
  intro?: string;
  summary?: string;
  missing: Missing[];
  pills: Pill[];
  options?: Record<string, Option[]>;
};

export type Person = {
  id: number; lat: number; lon: number; age: number; sex: "M" | "K"; district: string; status: string;
  household: string | null; car: boolean; income: number; hobbies: string[]; music: string | null;
};
export type Answer = [id: number, p: number, reason: string | null];

export type Venue = {
  name: string; latlon: [number, number]; kind: "venue" | "district"; capacity?: number; index: number; rank: number; score: number;
  within_20min: number; expected?: number; sellout?: boolean; pinned?: boolean; fit?: string; median_travel?: number;
  ages: Record<string, number>; districts: Record<string, number>; segments: { name: string; share: number; lift: number; n: number }[];
};
export type GusFact = { table: string; text_pl: string; source: string };
export type EventResult = {
  mode: "event"; summary: string; n: number; venues: Venue[]; n_candidates: number; reasons: Record<string, number>;
  barriers: { want_share?: number; want_target?: number; price_strain?: number; busy?: { name: string; ratio: number }[] };
  when?: { days: Record<string, number>; times: Record<string, number>; best: string; sunday_note: boolean; expected_days?: Record<string, number> };
  insights: string[]; suggestions: { label: string; slot: string; value: string | number }[]; estimate?: number; gus?: GusFact[]; notes?: string[]; followups?: string[];
};
export type PolicyResult = {
  mode: "policy"; title: string; n: number; archetypes?: number | null; location?: { name: string; lat: number; lon: number } | null; support: number; stance: number; assumptions: string[]; description: string;
  districts: Record<string, { support: number; n: number }>; segments: { name: string; support: number; n: number }[];
  groups: { name: string; share: number; people: number; n: number; support: number | null; support_rest: number }[];
  args_for: { key: string; label: string; share: number }[]; args_against: { key: string; label: string; share: number }[];
  insights: string[]; gus?: GusFact[]; notes?: string[]; followups?: string[];
};
export type AgentStep = { tool: string; args: Record<string, any>; result: any };
export type AgentResult = { mode: "agent"; answer: string; steps: AgentStep[]; flagged: number[]; response_id: string; survey_people: number; survey_residents?: number; followups?: string[] };
export type Result = EventResult | PolicyResult | AgentResult;

export type RunEvent =
  | { type: "status"; message: string }
  | { type: "progress"; done: number; total: number }
  | { type: "people"; people: Person[]; labels?: Record<string, string>; archetypes?: number | null }
  | { type: "answers"; rows: Answer[] }
  | { type: "highlight"; label: string; points: [number, number][] }
  | { type: "places"; label: string; points: [number, number, string][] }
  | { type: "choropleth"; label: string; values: Record<string, number> }
  | { type: "followups"; items: string[] }
  | { type: "result"; data: Result }
  | { type: "error"; message: string };

export type District = { name: string; ring: [number, number][]; lat: number; lon: number; people: number };

export async function chat(body: Record<string, unknown>): Promise<ChatResponse> {
  const r = await fetch(`${API}/api/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function run(slots: Record<string, unknown>, n: number, on: (e: RunEvent) => void): () => void {
  const es = new EventSource(`${API}/api/run?n=${n}&slots=${encodeURIComponent(JSON.stringify(slots))}`);
  es.onmessage = (m) => on(JSON.parse(m.data));
  es.onerror = () => { on({ type: "error", message: "Połączenie przerwane." }); es.close(); };
  return () => es.close();
}

export const getGeo = (): Promise<{ districts: District[]; city: [number, number][] }> => fetch(`${API}/api/geo`).then((r) => r.json());
export const getPopulation = (): Promise<{ people: [number, number, number, number][]; weight: number }> => fetch(`${API}/api/population`).then((r) => r.json());
