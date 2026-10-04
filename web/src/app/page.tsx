"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion, useSpring, useTransform } from "framer-motion";
import Chat from "@/components/Chat";
import type { Picked } from "@/components/LiveMap";
import { getGeo, getPopulation, type District } from "@/lib/api";
import { EXAMPLES, useSim } from "@/lib/useSim";

const LiveMap = dynamic(() => import("@/components/LiveMap"), { ssr: false });

function Counter({ value, format }: { value: number; format: (v: number) => string }) {
  const s = useSpring(0, { stiffness: 80, damping: 20 }); const t = useTransform(s, format);
  useEffect(() => { s.set(value); }, [s, value]);
  return <motion.span className="tabular-nums">{t}</motion.span>;
}

const SEX = { K: "Kobieta", M: "Mężczyzna" } as const;
const RL: Record<string, string> = { price: "cena", distance_time: "dojazd / czas", schedule: "praca / rodzina / brak czasu", taste: "zainteresowania i gust",
  age_health: "wiek / zdrowie", car: "auto", public_transport: "komunikacja", environment: "środowisko", indifferent: "obojętne" };

function Landing({ sim, people, hidden }: { sim: ReturnType<typeof useSim>; people: number; hidden: boolean }) {
  const [text, setText] = useState("");
  const go = (q: string) => { if (q.trim()) sim.sendText(q.trim()); };
  return (
    // fades out with plain CSS (an exit animation sometimes stayed half-visible over the map)
    <div className="pointer-events-none absolute inset-0" style={{ opacity: hidden ? 0 : 1, visibility: hidden ? "hidden" : "visible",
      transition: hidden ? "opacity .3s ease, visibility 0s linear .3s" : "opacity .3s ease" }}>
      <div className="absolute left-1/2 top-[10vh] w-[min(960px,92vw)] -translate-x-1/2 text-center">
        <h1 className="text-[34px] font-bold tracking-tight">AskTheLemurs!</h1>
      </div>
      <div className="pointer-events-auto absolute left-1/2 top-[calc(10vh+84px+38vh+28px)] w-[min(760px,92vw)] -translate-x-1/2">
        <form onSubmit={(e) => { e.preventDefault(); go(text); }} className="flex items-end gap-2 rounded-2xl border border-white/15 bg-[#14161d] p-2 shadow-xl focus-within:border-amber-300/50">
          <textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder="Opisz wydarzenie, decyzję miasta albo zapytaj o mieszkańców…"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go(text); } }}
            className="max-h-40 flex-1 resize-none bg-transparent px-3 py-2 text-[15px] outline-none placeholder:text-white/35" />
          <button className="h-10 rounded-xl bg-amber-400 px-4 font-semibold text-black transition hover:bg-amber-300">Zapytaj</button>
        </form>
        <div className="mt-4 grid grid-cols-3 gap-2">
          {EXAMPLES.map((e) => (
            <button key={e.q} onClick={() => go(e.q)} className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5 text-left transition hover:border-amber-300/40 hover:bg-white/[0.06]">
              <div className="text-[10.5px] font-semibold uppercase tracking-wider text-amber-300/80">{e.who}</div>
              <div className="text-[13px] leading-snug text-white/85">{e.short}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// a district clicked on the map: everything computed in the browser from the residents and answers already on the map
function DistrictCard({ name, sim, sel, onClose }: { name: string; sim: ReturnType<typeof useSim>; sel: number; onClose: () => void }) {
  const ans = new Map((sim.answers.current ?? []).map((a) => [a[0], a[1]]));
  const P = sim.people.filter((x) => x.district === name);
  const A = P.filter((x) => ans.has(x.id));
  const mean = (xs: typeof A) => (xs.length ? xs.reduce((s, x) => s + (ans.get(x.id) ?? 0), 0) / xs.length : null);
  const policy = sim.mode === "policy", pc = (v: number | null) => (v == null ? "–" : `${Math.round(v * 100)}%`);
  const bands: [string, number, number][] = [["15–24", 15, 24], ["25–34", 25, 34], ["35–49", 35, 49], ["50–64", 50, 64], ["65+", 65, 200]];
  const R = sim.result;
  const venueShare = R?.mode === "event" ? R.venues[sel]?.districts?.[name] : undefined;
  const polSupport = R?.mode === "policy" ? R.districts[name]?.support : undefined;
  const choroV = sim.choro?.values[name];
  // an area decision only asks that area, so there is no city figure to compare with
  const city = R?.mode === "policy" ? ((R as { area?: unknown }).area ? null : R.support) : ans.size ? [...ans.values()].reduce((a, b) => a + b, 0) / ans.size : null;
  const here = polSupport ?? mean(A);
  const diff = here != null && city != null ? Math.round((here - city) * 100) : null;
  const label = policy ? "popiera" : sim.mode === "agent" ? "odpowiada „tak”" : "chęć pójścia";
  return (
    <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="glass absolute left-6 top-24 w-[300px] p-4 text-[13px]">
      <div className="flex items-start justify-between">
        <div><div className="text-[11px] font-semibold uppercase tracking-wider text-amber-300/80">Dzielnica</div><div className="text-[17px] font-bold">{name}</div></div>
        <button onClick={onClose} className="text-white/40 hover:text-white">✕</button>
      </div>
      {A.length > 0 && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <div className="rounded-xl bg-white/5 px-3 py-2"><div className="text-[11px] text-white/50">{label}</div><div className={`text-[22px] font-bold ${policy ? "text-sky-300" : "text-amber-300"}`}>{pc(here)}</div>{diff != null && <div className="text-[11px] text-white/50">miasto {pc(city)}{diff ? ` · ${diff > 0 ? "+" : "−"}${Math.abs(diff)} pkt` : " · tyle samo"}</div>}</div>
          <div className="rounded-xl bg-white/5 px-3 py-2"><div className="text-[11px] text-white/50">mieszkańców z odpowiedzią</div><div className="text-[22px] font-bold">{A.length.toLocaleString("pl-PL")}</div></div>
        </div>
      )}
      {venueShare != null && <p className="mt-2 text-white/75">Z tej dzielnicy przyjdzie ok. <b>{pc(venueShare)}</b> publiczności wybranego miejsca.</p>}
      {choroV != null && sim.choro && <p className="mt-2 text-white/75">{sim.choro.label}: <b>{Number.isInteger(choroV) ? choroV.toLocaleString("pl-PL") : choroV < 1 ? pc(choroV) : choroV.toFixed(1).replace(".", ",")}</b></p>}
      {A.length >= 20 && (
        <div className="mt-3 space-y-1">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-white/50">{label}: wiek i auto</div>
          {[...bands.map(([l, lo, hi]) => [l, A.filter((x) => x.age >= lo && x.age <= hi)] as const), ["z autem", A.filter((x) => x.car)] as const, ["bez auta", A.filter((x) => !x.car)] as const]
            .filter(([, xs]) => xs.length >= 5).map(([l, xs]) => (
              <div key={l} className="grid grid-cols-[72px_1fr_40px] items-center gap-2 text-[12px]">
                <span className="text-white/60">{l}</span>
                <span className="h-1.5 rounded bg-white/10"><span className={`block h-1.5 rounded ${policy ? "bg-sky-400" : "bg-amber-400"}`} style={{ width: `${Math.round((mean(xs) ?? 0) * 100)}%` }} /></span>
                <span className="text-right tabular-nums text-white/80">{pc(mean(xs))}</span>
              </div>
            ))}
        </div>
      )}
      {!A.length && choroV == null && <p className="mt-2 text-white/55">Zadaj pytanie, żeby zobaczyć wyniki tej dzielnicy.</p>}
      <p className="mt-2 text-[10.5px] text-white/35">{P.length ? `${P.length.toLocaleString("pl-PL")} symulowanych mieszkańców w tej dzielnicy na mapie.` : ""} Kliknij inną dzielnicę albo puste miejsce, żeby zamknąć.</p>
    </motion.div>
  );
}

export default function Home() {
  const sim = useSim();
  const [pop, setPop] = useState<{ people: [number, number, number, number][]; weight: number }>({ people: [], weight: 55 });
  const [districts, setDistricts] = useState<District[]>([]);
  const [sel, setSel] = useState(0);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [stats, setStats] = useState({ revealed: 0, mean: 0 });
  const [extrude, setExtrude] = useState(false);
  const [selDistrict, setSelDistrict] = useState<string | null>(null);
  useEffect(() => { getPopulation().then(setPop).catch(() => {}); getGeo().then((g) => setDistricts(g.districts)).catch(() => {}); }, []);
  useEffect(() => { setSel(0); setPicked(null); }, [sim.result]);
  const onStats = useCallback((s: { revealed: number; mean: number }) => setStats((o) => (o.revealed === s.revealed && o.mean === s.mean ? o : s)), []);

  const total = sim.people.length, asking = total > 0, started = sim.started;
  const policy = sim.mode === "policy";
  const pickedAns = picked?.answer ?? null;

  return (
    <main className="fixed inset-0 overflow-hidden bg-[#0b0c10] text-white">
      {/* start screen: a smaller live map card above a big chat input; after the first question the same map grows into the
          left part of the screen and the report panel slides in (one LiveMap instance, only its box changes) */}
      <section className={`absolute overflow-hidden transition-all duration-700 ease-[cubic-bezier(.2,.8,.2,1)] ${started ? "" : "rounded-3xl border border-white/10 shadow-2xl"}`}
        style={started ? { top: 0, left: 0, bottom: 0, right: "min(520px, 42vw)" }
                       : { top: "calc(10vh + 84px)", left: "max(4vw, calc(50% - 480px))", right: "max(4vw, calc(50% - 480px))", bottom: "calc(90vh - 84px - 38vh)" }}>
        <div className="absolute inset-0">
          <LiveMap compact={!started} selDistrict={selDistrict} onDistrict={setSelDistrict} population={pop.people} districts={districts} people={sim.people} highlight={sim.highlight} places={sim.places} choro={sim.choro} extrude={extrude} answers={sim.answers} answersTick={sim.answersTick}
            mode={sim.mode} result={sim.result} selectedVenue={sel} onPick={setPicked} onStats={onStats} />
        </div>
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_45%,rgba(5,6,10,.5))]" />

        {/* population + live counters */}
        <div className={`pointer-events-none absolute left-1/2 top-4 -translate-x-1/2 text-center transition-opacity ${started ? "" : "opacity-0"}`}>
          <p className="text-[12px] text-white/55">
            <Counter value={pop.people.length * pop.weight} format={(v) => Math.round(v / 1000).toLocaleString("pl-PL")} /> tys. mieszkańców z danych GUS ·{" "}
            {pop.people.length.toLocaleString("pl-PL")} symulowanych osób
          </p>
          <AnimatePresence>
            {asking && (
              <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="glass mt-2 inline-flex gap-6 px-5 py-2.5">
                {sim.archetypes && <div><div className="text-[11px] text-white/50">decyzje AI (archetypy)</div><div className="text-[20px] font-bold">{sim.progress ? sim.progress.done : stats.revealed ? sim.archetypes : 0}<span className="text-white/40"> / {sim.archetypes}</span></div></div>}
                <div><div className="text-[11px] text-white/50">{sim.archetypes ? "mieszkańcy z odpowiedzią" : "zapytani przez AI"}</div><div className="text-[20px] font-bold"><Counter value={stats.revealed} format={(v) => Math.round(v).toLocaleString("pl-PL")} /><span className="text-white/40"> / {total}</span></div></div>
                <div><div className="text-[11px] text-white/50">{policy ? "popiera" : sim.mode === "agent" ? "odpowiedź „tak”" : "chęć pójścia"}</div><div className={`text-[20px] font-bold ${policy ? "text-sky-300" : "text-amber-300"}`}><Counter value={stats.mean * 100} format={(v) => `${Math.round(v)}%`} /></div></div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* what the agent is looking at */}
        <AnimatePresence>
          {(sim.highlight || sim.places || sim.choro) && (
            <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="glass absolute bottom-6 left-6 max-w-[340px] space-y-1 px-3 py-2 text-[12px]">
              {sim.highlight && <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 shrink-0 rounded-full bg-cyan-300" />{sim.highlight.label} · {sim.highlight.points.length.toLocaleString("pl-PL")} person</div>}
              {sim.places && <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 shrink-0 rounded-full bg-fuchsia-400" />{sim.places.label} · {sim.places.points.length} miejsc (OSM)</div>}
              {sim.choro && <div className="flex items-center gap-2"><span className="h-2.5 w-6 shrink-0 rounded bg-gradient-to-r from-teal-900 to-teal-300" />dzielnice: {sim.choro.label} (jaśniej = więcej)</div>}
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>{started && selDistrict && <DistrictCard key={selDistrict} name={selDistrict} sim={sim} sel={sel} onClose={() => setSelDistrict(null)} />}</AnimatePresence>

        {sim.result?.mode === "policy" && (
          <button onClick={() => setExtrude((x) => !x)} className="glass absolute bottom-6 right-6 px-3 py-2 text-[12px] hover:border-amber-300/60">
            {extrude ? "Dzielnice płasko (pokaż mieszkańców)" : "Dzielnice w 3D"}
          </button>
        )}

        {/* person card */}
        <AnimatePresence>
          {picked && (
            <motion.div key={picked.person.id} initial={{ opacity: 0, y: 16, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 16 }}
              className="glass absolute right-6 top-24 w-[320px] p-4 text-[13px]">
              <div className="flex items-start justify-between">
                <div><div className="text-[16px] font-bold">{SEX[picked.person.sex]}, {picked.person.age} lat</div><div className="text-white/55">{picked.person.district}</div></div>
                <button onClick={() => setPicked(null)} className="text-white/40 hover:text-white">✕</button>
              </div>
              <div className="mt-2 space-y-0.5 text-white/80">
                <div>{picked.person.status}{picked.person.income ? ` · ${picked.person.income.toLocaleString("pl-PL")} zł/mies.` : ""}</div>
                {picked.person.household && <div>{picked.person.household}</div>}
                <div>{picked.person.car ? "ma auto" : "bez auta, jeździ MPK"}</div>
                {!!picked.person.hobbies.length && <div className="text-white/60">czas wolny: {picked.person.hobbies.join(", ")}</div>}
              </div>
              {pickedAns && (
                <div className="mt-3 rounded-xl bg-white/5 p-3">
                  <div className="text-[11px] text-white/50">odpowiedź modelu Jev{sim.archetypes ? " (za archetyp tej osoby)" : ""}</div>
                  <div className={`text-[18px] font-bold ${policy ? "text-sky-300" : "text-amber-300"}`}>{policy ? "poparcie" : "szansa „tak”"}: {Math.round(pickedAns[1] * 100)}%</div>
                  {pickedAns[2] && <div className="text-white/70">{policy ? "najważniejszy argument: " + (sim.labels[pickedAns[2]] ?? pickedAns[2]) : "najważniejsze: " + (RL[pickedAns[2]] ?? pickedAns[2])}</div>}
                </div>
              )}
              <p className="mt-2 text-[10px] text-white/35">Profil złożony z danych GUS (NSP 2021, BDL, badania kultury i sportu). Pozycja: kratka 1 km.</p>
            </motion.div>
          )}
        </AnimatePresence>
      </section>

      {/* one report panel: conversation + answers (slides in after the first question) */}
      <aside className={`absolute bottom-0 right-0 top-0 w-[min(520px,42vw)] border-l border-white/[0.07] bg-[#0e1016]/95 transition-transform duration-700 ease-[cubic-bezier(.2,.8,.2,1)] ${started ? "" : "translate-x-full"}`}>
        <Chat sim={sim} sel={sel} setSel={setSel} />
      </aside>

      <Landing sim={sim} people={pop.people.length} hidden={started} />
    </main>
  );
}
