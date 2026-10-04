"use client";
// The report panel: the conversation, with every answer rendered as a report card in place.
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import Results from "@/components/Results";
import { md } from "@/lib/md";
import type { useSim } from "@/lib/useSim";

type Sim = ReturnType<typeof useSim>;

export default function Chat({ sim, sel, setSel }: { sim: Sim; sel: number; setSel: (i: number) => void }) {
  const [text, setText] = useState("");
  const [price, setPrice] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" }); }, [sim.msgs, sim.chips, sim.priceSlot]);
  const lastResult = [...sim.msgs].reverse().find((m) => "result" in m)?.id;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-white/[0.07] px-5 pb-3 pt-4">
        <div className="text-[18px] font-bold tracking-tight">AskTheLemurs!</div>
        <div className="text-[12px] text-white/45">Zapytaj symulowanych mieszkańców Krakowa.</div>
      </div>
      <div ref={box} className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
        <AnimatePresence initial={false}>
          {sim.msgs.map((m) => (
            "result" in m ? (
              <div key={m.id}>
                <Results R={m.result} sel={m.id === lastResult ? sel : 0} setSel={m.id === lastResult ? setSel : () => {}} />
              </div>
            ) : (
              <motion.div key={m.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}
                className={"msg " + ("role" in m && m.role === "me" ? "me" : "bot")}>
                {"text" in m && <span className="whitespace-pre-wrap">{m.text}</span>}
                {"list" in m && (
                  <>
                    {m.title && <div className="mb-1 font-semibold">{m.title}</div>}
                    <ul className="list-disc space-y-1 pl-4">{m.list.map((t, i) => <li key={i}>{t}</li>)}</ul>
                  </>
                )}
                {"markdown" in m && <div className="prose-chat" dangerouslySetInnerHTML={{ __html: md(m.markdown) }} />}
              </motion.div>
            )
          ))}
        </AnimatePresence>
        {sim.busy && (
          <div className="msg bot flex items-center gap-2 text-white/60">
            <span className="live-dot" />
            <span className="text-[13px]">{sim.status || "…"}{sim.progress ? ` ${sim.progress.done}/${sim.progress.total}` : ""}</span>
          </div>
        )}
        {!!sim.chips.length && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-wrap gap-1.5">
            {sim.chips.map((c, i) => (
              <button key={i} onClick={c.onPick} className={"chip " + (c.primary ? "chip-primary" : c.ghost ? "chip-ghost" : "")}>{c.label}</button>
            ))}
          </motion.div>
        )}
        {sim.priceSlot && (
          <form className="flex gap-1.5" onSubmit={(e) => { e.preventDefault(); if (price !== "") { sim.submitPrice(+price); setPrice(""); } }}>
            <input autoFocus type="number" min={0} step={5} value={price} onChange={(e) => setPrice(e.target.value)} placeholder="zł"
              className="w-28 rounded-lg border border-white/15 bg-white/5 px-2.5 py-1.5 outline-none focus:border-amber-300/60" />
            <button className="chip chip-primary">OK</button>
          </form>
        )}
      </div>
      {!!sim.pills.length && (
        <div className="flex flex-wrap gap-1 px-5 pt-2">
          {sim.pills.map((p) => (
            <button key={p.slot} onClick={() => sim.editPill(p)} className={"pill " + (p.value ? "" : "pill-miss")}>{p.label}: <b>{p.value ?? "?"}</b></button>
          ))}
        </div>
      )}
      <form className="flex gap-2 border-t border-white/[0.07] p-4" onSubmit={(e) => { e.preventDefault(); sim.sendText(text); setText(""); }}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder="Opisz wydarzenie, decyzję miasta albo zapytaj o mieszkańców…"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sim.sendText(text); setText(""); } }}
          className="flex-1 resize-none rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 outline-none placeholder:text-white/35 focus:border-amber-300/50" />
        <button disabled={sim.busy} className="rounded-xl bg-amber-400 px-4 font-semibold text-black transition hover:bg-amber-300 disabled:opacity-40">Wyślij</button>
      </form>
    </div>
  );
}
