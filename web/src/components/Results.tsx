"use client";
// One report card per answer, inside the conversation: headline numbers, conclusions, charts, sources.
import { motion } from "framer-motion";
import type { AgentResult, EventResult, GusFact, PolicyResult, Result } from "@/lib/api";
import { md } from "@/lib/md";

const pct = (x: number) => `${Math.round(x * 100)}%`;
const plural = (n: number, one: string, few: string, many: string) => (n === 1 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? few : many);
const fmt = (n: number) => Math.round(n).toLocaleString("pl-PL");
const RL: Record<string, string> = { price: "cena", distance_time: "dojazd / czas", schedule: "praca / rodzina / czas", taste: "zainteresowania i gust", age_health: "wiek / zdrowie",
  car: "auto", public_transport: "komunikacja", environment: "środowisko", indifferent: "obojętne" };
const DL: Record<string, string> = { Mon: "pon", Tue: "wt", Wed: "śr", Thu: "czw", Fri: "pt", Sat: "sob", Sun: "nd" };

export function Bars({ rows, format = pct, color = "bg-amber-400" }: { rows: { l: string; v: number; extra?: string }[]; format?: (v: number) => string; color?: string }) {
  const max = Math.max(...rows.map((r) => r.v), 1e-9);
  // long labels (groups, reasons, arguments) go in full above the bar; short ones (age, days) stay beside it
  const stacked = rows.some((r) => r.l.length > 16);
  const bar = (r: { v: number }, i: number) => (
    <motion.div initial={{ width: 0 }} animate={{ width: `${Math.max(2, (r.v / max) * (stacked ? 82 : 70))}%` }} transition={{ duration: 0.8, delay: i * 0.03, ease: "easeOut" }}
      className={`h-2 shrink-0 rounded ${color}`} />
  );
  if (stacked) return (
    <div className="space-y-2">
      {rows.map((r, i) => (
        <div key={r.l + i} className="text-[12px]">
          <div className="leading-snug text-white/70">{r.l}</div>
          <div className="mt-1 flex items-center gap-2">{bar(r, i)}<span className="whitespace-nowrap tabular-nums text-white/85">{format(r.v)}{r.extra}</span></div>
        </div>
      ))}
    </div>
  );
  return (
    <div className="space-y-1.5">
      {rows.map((r, i) => (
        <div key={r.l + i} className="grid grid-cols-[88px_1fr] items-center gap-2 text-[12px]">
          <div className="truncate text-right text-white/60" title={r.l}>{r.l}</div>
          <div className="flex items-center gap-2">{bar(r, i)}<span className="whitespace-nowrap tabular-nums text-white/85">{format(r.v)}{r.extra}</span></div>
        </div>
      ))}
    </div>
  );
}

const Section = ({ title, cap, children }: { title: string; cap?: string; children: React.ReactNode }) => (
  <div className="border-t border-white/[0.07] pt-3">
    <div className="text-[12px] font-semibold uppercase tracking-wide text-white/55">{title}</div>
    {cap && <p className="mb-2 mt-0.5 text-[11px] text-white/40">{cap}</p>}
    <div className={cap ? "" : "mt-2"}>{children}</div>
  </div>
);
const Tile = ({ k, v, accent }: { k: string; v: string; accent?: string }) => (
  <div className="rounded-xl bg-white/[0.05] px-3 py-2"><div className="text-[11px] text-white/50">{k}</div><div className={`text-[22px] font-bold tabular-nums ${accent ?? ""}`}>{v}</div></div>
);
const Bullets = ({ items }: { items: string[] }) => (
  <ul className="space-y-1.5 text-[13px] leading-snug text-white/85">{items.map((t, i) => <li key={i} className="flex gap-2"><span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-white/40" />{t}</li>)}</ul>
);
const Sources = ({ gus }: { gus?: GusFact[] }) =>
  gus?.length ? (
    <details className="border-t border-white/[0.07] pt-3 text-[12px]">
      <summary className="cursor-pointer select-none font-semibold uppercase tracking-wide text-white/55">Dane GUS ({gus.length}) · każda liczba sprawdzona z tabelą</summary>
      <ul className="mt-2 space-y-1.5 text-white/75">{gus.map((f, k) => <li key={k}>{f.text_pl} <span className="text-white/35">{f.source}</span></li>)}</ul>
    </details>
  ) : null;

function EventView({ R, sel, setSel }: { R: EventResult; sel: number; setSel: (i: number) => void }) {
  const v = R.venues[sel] ?? R.venues[0], v0 = R.venues[0], B = R.barriers;
  if (!v) return <Bullets items={R.insights} />;
  return (
    <>
      <div className="grid grid-cols-3 gap-2">
        <Tile k="Spodziewana publiczność" v={v0?.expected != null ? `~${fmt(v0.expected)}` : "–"} accent="text-amber-300" />
        <Tile k={B.want_target != null ? "Podoba się (cel)" : "Podoba się"} v={pct(B.want_target ?? B.want_share ?? 0)} />
        <Tile k="Typowy dojazd" v={`${v.median_travel ?? "–"} min`} />
      </div>
      <Bullets items={R.insights} />
      <Section title="Ranking miejsc" cap="Kliknij miejsce, żeby zobaczyć na mapie, skąd przyjdą ludzie">
        <ul className="space-y-0.5">
          {R.venues.slice(0, 6).map((x, i) => (
            <li key={x.name} onClick={() => setSel(i)} className={`grid cursor-pointer grid-cols-[22px_1fr_44px] items-center gap-2 rounded-lg px-2 py-1.5 transition ${i === sel ? "bg-white/10 ring-1 ring-amber-300/60" : "hover:bg-white/5"}`}>
              <span className="flex h-5 w-5 items-center justify-center rounded-full border border-white/50 text-[10px] font-bold">{x.rank}</span>
              <div className="min-w-0"><div className="truncate text-[13px] font-semibold">{x.pinned ? "📍 " : ""}{x.name}</div>
                <div className="truncate text-[11px] text-white/45">{x.expected != null && `~${fmt(x.expected)} osób${x.sellout ? " (wyprzedane)" : ""} · `}{x.kind === "district" ? "okolica" : `do ${fmt(x.capacity ?? 0)}`} · {pct(x.within_20min)} do 20 min</div></div>
              <div className="text-right text-[12px] tabular-nums text-amber-300">{x.index}</div>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Kto przyjdzie" cap={`${v.name} · w nawiasie: ile razy chętniej niż średnio`}>
        <Bars rows={v.segments.filter((s) => s.share >= 0.02).map((s) => ({ l: s.name, v: s.share, extra: ` (${s.lift.toFixed(1).replace(".", ",")}×)` }))} />
      </Section>
      <div className="grid grid-cols-2 gap-4">
        <Section title="Wiek"><Bars rows={Object.entries(v.ages).map(([l, x]) => ({ l, v: x }))} /></Section>
        {R.when && <Section title="Dzień" cap="względem wybranego (HETUS 2020: cały dzień, z czasu pandemii; wieczorem różnice mniejsze)"><Bars rows={Object.entries(R.when.days).map(([k, x]) => ({ l: DL[k] + (k === "Sun" && R.when!.sunday_note ? "*" : ""), v: x }))} color="bg-sky-400" /></Section>}
      </div>
      <Section title="Co decyduje"><Bars rows={Object.entries(R.reasons).filter(([, x]) => x >= 0.01).map(([k, x]) => ({ l: RL[k] ?? k, v: x }))} color="bg-white/60" /></Section>
      <Sources gus={R.gus} />
    </>
  );
}

function PolicyView({ R }: { R: PolicyResult }) {
  const groups = R.groups.filter((g) => g.share < 0.95 && g.support != null);
  const aff = groups.reduce((a, g) => a + g.share, 0);
  // each fact once: support is a tile, groups are the chart, arguments have their own list
  const lines = R.insights.filter((x) => !x.startsWith("Popiera") && !x.startsWith("Argumenty") && !x.startsWith("Założenia") && !/dorosłych\): /.test(x));
  const args = (xs: PolicyResult["args_for"]) => xs.filter((a) => a.share >= 0.1).slice(0, 3);
  const Args = ({ title, xs, dot }: { title: string; xs: PolicyResult["args_for"]; dot: string }) => (
    <div>
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-white/50">{title}</div>
      <ul className="space-y-0.5 text-[12.5px] text-white/85">
        {xs.map((a) => <li key={a.key} className="flex gap-2"><span className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />{a.label}{xs.length > 1 && <span className="text-white/40">{pct(a.share)}</span>}</li>)}
      </ul>
    </div>
  );
  const af = args(R.args_for), ag = args(R.args_against);
  return (
    <>
      <div className={`grid gap-2 ${groups.length ? "grid-cols-2" : "grid-cols-1"}`}>
        <Tile k="Popiera" v={pct(R.support)} accent="text-sky-300" />
        {!!groups.length && <Tile k="Bezpośrednio dotknięci" v={pct(Math.min(aff, 1))} />}
      </div>
      {!!lines.length && <Bullets items={lines} />}
      {!!groups.length && (
        <Section title="Kogo to dotyka" cap="Poparcie w grupie i wśród reszty mieszkańców">
          <div className="space-y-3">
            {groups.map((g) => (
              <div key={g.name}>
                <div className="mb-1 text-[12.5px] text-white/85">{g.name} <span className="text-white/40">· {pct(g.share)} dorosłych</span></div>
                <Bars rows={[{ l: "w grupie", v: g.support ?? 0 }, { l: "reszta mieszkańców", v: g.support_rest }]} color="bg-sky-400" />
              </div>
            ))}
          </div>
        </Section>
      )}
      {(!!af.length || !!ag.length) && (
        <div className="grid grid-cols-2 gap-4 border-t border-white/[0.07] pt-3">
          {!!af.length && <Args title="Zwolennicy mówią" xs={af} dot="bg-sky-400" />}
          {!!ag.length && <Args title="Przeciwnicy mówią" xs={ag} dot="bg-orange-400" />}
        </div>
      )}
      {!!R.assumptions.length && (
        <details className="border-t border-white/[0.07] pt-3 text-[12px]">
          <summary className="cursor-pointer select-none font-semibold uppercase tracking-wide text-white/55">Założenia ({R.assumptions.length})</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-white/55">{R.assumptions.map((x) => <li key={x}>{x.replace(/[.\s]+$/, "")}</li>)}</ul>
        </details>
      )}
      <Sources gus={R.gus} />
    </>
  );
}

const TOOLPL: Record<string, string> = { query_residents: "dane mieszkańców (GUS, symulacja)", get_table: "tabela (GUS, PKW, miasto)", ask_residents: "ankieta Jev (archetypy)",
  find_place: "miejsce", count_places: "OpenStreetMap", nearby_places: "OpenStreetMap", travel_by_district: "dojazdy (rozkład MPK, R5)" };
const METRICS = ["share_yes", "share", "minutes", "residents_per_place", "places", "mean", "median", "people"];
const NOT_GROUP = new Set(["n_personas", "people", "share", "mean", "median", "minutes", "places", "residents", "residents_per_place"]);

function AgentView({ R }: { R: AgentResult }) {
  const steps = R.steps.filter((s) => s.tool !== "find_place" && !s.result?.error);
  return (
    <>
      <div className="prose-chat text-[13px] leading-relaxed text-white/90" dangerouslySetInnerHTML={{ __html: md(R.answer) }} />
      {!!R.flagged?.length && <p className="text-[11px] text-amber-300/80">Tych liczb nie znalazłem w wynikach narzędzi: {R.flagged.join(", ")}</p>}
      {steps.map((s, i) => {
        const a = s.args, res = s.result, rows: any[] = res?.rows ?? [];
        const k = rows[0] && (METRICS.find((x) => rows[0][x] != null) ?? Object.keys(rows[0]).find((x) => x.startsWith("share_")));
        const g = rows[0] && Object.keys(rows[0]).find((x) => !NOT_GROUP.has(x) && !x.startsWith("share_"));
        if (!(rows.length > 1 && g && k)) return null;   // charts only; the full step list is below
        return (
          <Section key={i} title={a.title_pl ?? a.key ?? ""} cap={`${TOOLPL[s.tool] ?? s.tool}${k === "residents_per_place" ? " · mieszkańców na 1 miejsce" : k === "minutes" ? " · minut od drzwi do drzwi" : ""}`}>
            <Bars rows={[...rows].sort((x, y) => (y[k] ?? 0) - (x[k] ?? 0)).slice(0, 10).map((r) => ({ l: String(r[g]), v: r[k] ?? 0 }))}
              format={k.startsWith("share") ? pct : k === "minutes" ? (v: number) => `${Math.round(v)} min` : fmt} color={s.tool === "ask_residents" ? "bg-amber-400" : "bg-sky-400"} />
          </Section>
        );
      })}
      <details className="border-t border-white/[0.07] pt-3 text-[12px]">
        <summary className="cursor-pointer select-none font-semibold uppercase tracking-wide text-white/55">
          Jak to policzyłem ({steps.length} {plural(steps.length, "krok", "kroki", "kroków")}{R.survey_people ? `, ${R.survey_people} decyzji AI za ${(R.survey_residents ?? 0).toLocaleString("pl-PL")} mieszkańców` : ""})
        </summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-white/60">
          {steps.map((s, i) => <li key={i}>{TOOLPL[s.tool] ?? s.tool}: {s.args.title_pl ?? s.args.key ?? ""}{(s.args.filters ?? []).length ? ` · ${(s.args.filters ?? []).map((f: any) => `${f.field} ${f.op} ${f.value}`).join(", ")}` : ""}</li>)}
        </ol>
      </details>
    </>
  );
}

export default function Results({ R, sel, setSel }: { R: Result; sel: number; setSel: (i: number) => void }) {
  const title = R.mode === "event" ? R.summary : R.mode === "policy" ? R.title : "Analiza danych";
  const kind = R.mode === "event" ? "Wydarzenie" : R.mode === "policy" ? "Decyzja miasta" : "Agent danych";
  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}
      className="space-y-3 rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4">
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wider text-amber-300/80">{kind}</div>
        <div className="text-[15px] font-semibold leading-snug">{title}</div>
      </div>
      {R.mode === "event" && <EventView R={R} sel={sel} setSel={setSel} />}
      {R.mode === "policy" && <PolicyView R={R} />}
      {R.mode === "agent" && <AgentView R={R} />}
      {"notes" in R && !!R.notes?.length && (
        <div className="space-y-0.5 border-t border-white/[0.07] pt-2 text-[10.5px] leading-snug text-white/35">{R.notes.map((x, i) => <div key={i}>{x}</div>)}</div>
      )}
    </motion.div>
  );
}
