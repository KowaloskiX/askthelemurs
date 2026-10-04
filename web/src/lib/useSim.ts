"use client";
// Conversation + simulation state: the same flow as the old app/static/index.html, plus the live stream for the map.
import { useCallback, useRef, useState } from "react";
import * as api from "./api";

export type Msg =
  | { id: number; role: "me" | "bot"; text: string }
  | { id: number; role: "bot"; list: string[]; title?: string; sources?: string[] }
  | { id: number; role: "bot"; markdown: string; flagged?: number[] }
  | { id: number; role: "bot"; result: api.Result };
export type Chip = { label: string; ghost?: boolean; primary?: boolean; onPick: () => void };
export type Phase = "idle" | "chat" | "agent" | "asking" | "done";

let mid = 0;
// demo cases, one per kind of user (tested: docs/CONTEXT.md); `who` and `short` for the start screen cards
export const EXAMPLES: { who: string; short: string; q: string }[] = [
  { who: "Organizator", short: "Koncert rapowy dla studentów 13.11", q: "Koncert rapowy dla studentów 13.11 wieczorem, 60 zł, ok. 1000 osób, w klubie" },
  { who: "Kawiarnia", short: "Gdzie otworzyć kawiarnię dla studentów?", q: "Chcę otworzyć kawiarnię dla studentów. Gdzie jest najwięcej studentów na jedną istniejącą kawiarnię?" },
  { who: "Rada dzielnicy", short: "Płatne parkowanie w Podgórzu", q: "Rada Dzielnicy Podgórze rozważa płatne parkowanie 6 zł za godzinę z abonamentem 30 zł miesięcznie dla mieszkańców. Jak to przyjmą?" },
  { who: "Miasto", short: "Nowy basen nad Zalewem Nowohuckim", q: "Miasto chce zbudować nowy basen nad Zalewem Nowohuckim. Kto by z niego korzystał i jak to przyjmą mieszkańcy?" },
  { who: "Rodzice", short: "Całodobowy żłobek a przedszkola", q: "Zapytaj rodziców małych dzieci w Krakowie, czy korzystaliby z całodobowego żłobka w swojej dzielnicy, i porównaj to z liczbą istniejących przedszkoli." },
  { who: "Dziennikarz", short: "Gdzie ludzie czują się najmniej bezpiecznie?", q: "Gdzie w Krakowie ludzie czują się najmniej bezpiecznie i czy to pokrywa się z danymi policji?" },
  { who: "Miasto", short: "Nocna prohibicja 22–6", q: "Miasto rozważa nocną prohibicję: zakaz sprzedaży alkoholu w sklepach od 22 do 6 w całym Krakowie. Jak to przyjmą mieszkańcy?" },
  { who: "Komunikacja", short: "Skąd najdłużej na Rynek tramwajem?", q: "Z której dzielnicy najdłużej jedzie się komunikacją miejską na Rynek Główny w piątek wieczorem?" },
  { who: "Dziennikarz", short: "Nowa Huta kontra Krowodrza", q: "Czym różnią się Nowa Huta i Krowodrza: wiek mieszkańców, poglądy i wynik 2. tury wyborów prezydenckich 2025?" },
];

export function useSim() {
  const [msgs, setMsgs] = useState<Msg[]>([
    { id: mid++, role: "bot", text: "Zapytaj o wydarzenie, decyzję miasta albo mieszkańców. Odpowiedzą symulowani krakowianie." },
  ]);
  const [chips, setChips] = useState<Chip[]>([]);
  const [priceSlot, setPriceSlot] = useState<string | null>(null);
  const [pills, setPills] = useState<api.Pill[]>([]);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [status, setStatus] = useState<string>("");
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [people, setPeople] = useState<api.Person[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const answers = useRef<api.Answer[]>([]);
  const [answersTick, setAnswersTick] = useState(0);
  const [result, setResult] = useState<api.Result | null>(null);
  const [mode, setMode] = useState<string | null>(null);
  const [archetypes, setArchetypes] = useState<number | null>(null);
  const [highlight, setHighlight] = useState<{ label: string; points: [number, number][] } | null>(null);
  const [places, setPlaces] = useState<{ label: string; points: [number, number, string][] } | null>(null);
  const [choro, setChoro] = useState<{ label: string; values: Record<string, number> } | null>(null);
  const slots = useRef<Record<string, unknown>>({});
  const options = useRef<Record<string, api.Option[]>>({});
  const lastN = useRef(0);
  const prevResult = useRef<api.Result | null>(null);
  const peopleAt = useRef(0), revealMs = useRef(0);

  const add = useCallback((m: Omit<Msg, "id"> & Record<string, unknown>) => setMsgs((x) => [...x, { ...(m as any), id: mid++ }]), []);
  const bot = useCallback((text: string) => add({ role: "bot", text }), [add]);
  const me = useCallback((text: string) => add({ role: "me", text }), [add]);

  const reset = useCallback(() => {
    slots.current = {}; lastN.current = 0; setResult(null); setPills([]); setPeople([]); answers.current = []; setAnswersTick((t) => t + 1);
    setPhase("idle"); setMode(null); bot("Jasne, o co chcesz zapytać?"); setChips(exampleChips());
  }, [bot]); // eslint-disable-line react-hooks/exhaustive-deps

  const runSim = useCallback((n: number) => {
    lastN.current = n || lastN.current; setBusy(true); setChips([]); setProgress(null);
    const m = String(slots.current.mode ?? "event"); setMode(m);
    setPhase(m === "agent" ? "agent" : "asking"); setStatus(m === "agent" ? "Agent planuje analizę…" : "Przygotowuję symulację…");
    answers.current = []; setAnswersTick((t) => t + 1); setPeople([]); setHighlight(null); setPlaces(null); setChoro(null);
    let gotResult = false, shown = false, follow: string[] = [];
    const stop = api.run(slots.current, n || 1200, (e) => {
      if (e.type === "status") setStatus(e.message);
      if (e.type === "progress") setProgress({ done: e.done, total: e.total });
      if (e.type === "people") {
        peopleAt.current = performance.now(); revealMs.current = Math.min(6000, 2000 + e.people.length * 0.2);
        setPeople(e.people); setLabels(e.labels ?? {}); setArchetypes(e.archetypes ?? null);
        setStatus(e.archetypes ? `AI decyduje za ${e.archetypes} archetypów (${e.people.length.toLocaleString("pl-PL")} mieszkańców)…` : `Pytam ${e.people.length} mieszkańców…`);
      }
      if (e.type === "answers") { answers.current.push(...e.rows); setAnswersTick((t) => t + 1); }
      if (e.type === "highlight") setHighlight({ label: e.label, points: e.points });
      if (e.type === "places") setPlaces({ label: e.label, points: e.points });
      if (e.type === "choropleth") setChoro({ label: e.label, values: e.values });
      // the server closes the stream after the follow-ups; a close after the result is not an error
      if (e.type === "error") { stop(); if (gotResult) return; bot("Błąd: " + e.message); setBusy(false); setPhase("done"); }
      if (e.type === "followups") {
        stop(); follow = e.items;
        if (shown) setChips((c) => [...followChips(follow), ...c.filter((x) => !x.label.startsWith("→ "))]);
      }
      if (e.type === "result") {
        gotResult = true;
        // cached runs answer at once: let the map finish its reveal wave before the results cover it
        const wait = m === "agent" ? 0 : Math.max(0, peopleAt.current + revealMs.current - performance.now());
        setTimeout(() => showResult(e.data), wait);
      }
    });
    const showResult = (R0: api.Result) => {
      shown = true; R0 = { ...R0, followups: follow } as api.Result;
      { setBusy(false); setPhase("done"); setProgress(null); setStatus("");
        const R = R0, prev = prevResult.current; prevResult.current = R; setResult(R);
        if (R.mode === "agent") {
          slots.current = { ...slots.current, prev: R.response_id };
          add({ role: "bot", result: R });   // the report card renders the answer, the steps and their charts
          setChips([...followChips(R.followups), { label: "Zacznij od nowa", ghost: true, onPick: reset }]);
          return;
        }
        if (R.mode === "event" && prev?.mode === "event" && prev.summary !== R.summary && prev.venues.length && R.venues.length) {
          const ch = (R.venues[0].score / prev.venues[0].score - 1) * 100;
          bot(`Porównanie z poprzednim wariantem: potencjalna publiczność najlepszego miejsca ${ch >= 0 ? "+" : ""}${ch.toFixed(0)}% (${prev.venues[0].name} → ${R.venues[0].name}).`);
        }
        if (R.mode === "policy" && prev?.mode === "policy" && prev.title !== R.title)
          bot(`Porównanie z poprzednim wariantem: poparcie ${Math.round(prev.support * 100)}% → ${Math.round(R.support * 100)}%.`);
        add({ role: "bot", result: R });   // one report card: conclusions, numbers, charts, GUS facts
        const sug = R.mode === "event" ? R.suggestions.map((s) => ({ label: s.label, onPick: () => { me(s.label); send({ slot: s.slot, value: s.value }); } })) : [];
        setChips([...followChips(R.followups), ...sug.slice(0, 2), { label: "Zacznij od nowa", ghost: true, onPick: reset }]);
      }
    };
  }, [add, bot, me, reset]); // eslint-disable-line react-hooks/exhaustive-deps

  // every resident is answered through archetypes (one AI decision per group of similar people), so no sample size to pick
  // suggested next questions: sent as a normal message (the router keeps the topic, the agent keeps its conversation)
  const followChips = (qs?: string[]): Chip[] => (qs ?? []).map((q) => ({ label: "→ " + q, onPick: () => { me(q); send({ text: q }); } }));

  const nChips = useCallback(() => setChips([
    { label: "Zapytaj miasto", primary: true, onPick: () => { me("Zapytaj miasto"); runSim(1); } },
  ]), [me, runSim]);

  const ask = useCallback((m: api.Missing) => {
    bot(m.question);
    if (m.free) return;
    if (m.options) setChips(m.options.map((o) => ({ label: o.label, onPick: () => { me(o.label); send({ slot: m.slot, value: o.value }); } })));
    else setPriceSlot(m.slot);
  }, [bot, me]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = useCallback(async (body: Record<string, unknown>) => {
    setBusy(true); setChips([]); setPriceSlot(null); setPhase((p) => (p === "idle" ? "chat" : p));
    try {
      const r = await api.chat({ slots: slots.current, ...body });
      slots.current = r.slots; if (r.options) options.current = r.options; setPills(r.pills ?? []);
      setMode(String(r.slots.mode ?? "")); setBusy(false);
      if (r.message) bot(r.message);
      else if (r.ready && r.auto) runSim(0);
      else if (r.ready) { if (r.intro) { bot(r.intro); setChips([{ label: "Zapytaj miasto", primary: true, onPick: () => { me("Zapytaj miasto"); runSim(1); } }]); } else if (lastN.current) runSim(lastN.current); else { bot(`Mam wszystko: ${r.summary}.\nZapytam wszystkich symulowanych mieszkańców 15+ przez archetypy (jedna decyzja AI na grupę podobnych osób).`); nChips(); } }
      else if (r.missing.length) ask(r.missing[0]);
    } catch (e) { bot("Błąd połączenia z API: " + e); setBusy(false); }
  }, [ask, bot, nChips, runSim]);

  const sendText = useCallback((t: string) => { if (!t.trim() || busy) return; me(t); send({ text: t }); }, [busy, me, send]);
  const editPill = useCallback((p: api.Pill) => {
    if (busy) return;
    if (p.slot === "place") { me("Bez konkretnej lokalizacji"); send({ slot: "place", value: "none" }); return; }
    bot(`Zmień: ${p.label}`);
    const o = options.current[p.slot];
    if (o) setChips(o.map((x) => ({ label: x.label, onPick: () => { me(x.label); send({ slot: p.slot, value: x.value }); } })));
    else setPriceSlot(p.slot);
  }, [bot, busy, me, send]);
  const submitPrice = useCallback((v: number) => { const s = priceSlot!; setPriceSlot(null); me(v ? `${v} zł` : "za darmo"); send({ slot: s, value: v }); }, [me, priceSlot, send]);

  function exampleChips(): Chip[] { return EXAMPLES.map((e) => ({ label: e.short, ghost: true, onPick: () => sendText(e.q) })); }
  const [inited, setInited] = useState(false);
  if (!inited) { setInited(true); }   // examples live on the start screen

  const started = msgs.some((m) => "role" in m && m.role === "me");
  return { started, msgs, chips, priceSlot, pills, busy, phase, status, progress, people, labels, archetypes, highlight, places, choro, answers, answersTick, result, mode, sendText, editPill, submitPrice };
}
