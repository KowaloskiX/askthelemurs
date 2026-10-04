"use client";
// The city as dots. Phases:
//  idle    every persona (20k) as a faint dot: the synthetic population built from GUS
//  asking  the sample lights up; each dot changes colour at the moment Jev answers for that person (real stream, paced for the eye)
//  done    event: the people who would come travel along arcs to the best venue, counter fills; policy: districts rise by support
// Click a dot: who this person is and what they answered.
import { useEffect, useMemo, useRef, useState } from "react";
import DeckGL from "@deck.gl/react";
import { FlyToInterpolator, LinearInterpolator, type PickingInfo } from "@deck.gl/core";
import { ScatterplotLayer, TextLayer, PolygonLayer, PathLayer } from "@deck.gl/layers";
import { TripsLayer } from "@deck.gl/geo-layers";
import { Map } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { setWorkerUrl } from "maplibre-gl";
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");   // Turbopack does not bundle MapLibre's module worker; served from public/ (postinstall copies it)
import type { Answer, District, Person, Result } from "@/lib/api";

const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json";
const HOME = { longitude: 19.955, latitude: 50.058, zoom: 10.9, pitch: 45, bearing: -12 };
type RGBA = [number, number, number, number];
const AMBER: [number, number, number] = [255, 186, 73], COLD: [number, number, number] = [86, 104, 140];
const BLUE: [number, number, number] = [84, 166, 255], ORANGE: [number, number, number] = [255, 122, 69], GREY: [number, number, number] = [150, 152, 164];
const mix = (a: number[], b: number[], t: number) => a.map((x, i) => x + (b[i] - x) * t);

export type Picked = { person: Person; answer?: Answer };
function inRing(ring: [number, number][], [x, y]: [number, number]) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
const districtAt = (ds: District[], pt: [number, number]) => ds.find((d) => inRing(d.ring, pt))?.name ?? null;

type Revealed = { p: number; reason: string | null; t: number };
type Flow = { path: [number, number][]; timestamps: number[]; v: number };

function arc(from: [number, number], to: [number, number], t0: number, speed: number): Flow {
  const [x0, y0] = from, [x1, y1] = to, dx = x1 - x0, dy = y1 - y0, d = Math.hypot(dx, dy);
  const cx = (x0 + x1) / 2 - dy * 0.25, cy = (y0 + y1) / 2 + dx * 0.25;   // control point: bend to one side
  const N = 24, path: [number, number][] = [], ts: number[] = [], dur = 900 + d * 1000 * speed;
  for (let i = 0; i <= N; i++) {
    const u = i / N, a = (1 - u) ** 2, b = 2 * u * (1 - u), c = u * u;
    path.push([a * x0 + b * cx + c * x1, a * y0 + b * cy + c * y1]); ts.push(t0 + dur * (u * u * (3 - 2 * u)));
  }
  return { path, timestamps: ts, v: 1 };
}

export default function LiveMap(props: {
  population: [number, number, number, number][]; districts: District[]; people: Person[]; answers: React.RefObject<Answer[]>;
  answersTick: number; mode: string | null; result: Result | null; selectedVenue: number; onPick: (p: Picked | null) => void;
  highlight?: { label: string; points: [number, number][] } | null; places?: { label: string; points: [number, number, string][] } | null;
  choro?: { label: string; values: Record<string, number> } | null; extrude?: boolean; compact?: boolean;
  selDistrict?: string | null; onDistrict?: (name: string | null) => void;
  onStats: (s: { revealed: number; mean: number }) => void;
}) {
  const { population, districts, people, answers, answersTick, mode, result, selectedVenue, onPick, onStats, highlight, places, choro, extrude, compact, selDistrict, onDistrict } = props;
  const hlAt = useRef(0), plAt = useRef(0);
  useEffect(() => { hlAt.current = performance.now(); }, [highlight]);
  useEffect(() => { plAt.current = performance.now(); }, [places]);
  const [viewState, setViewState] = useState<any>(props.compact ? { ...HOME, zoom: 10.2, pitch: 30 } : HOME);
  // start screen card -> full map: zoom in with the box growing. Not on mount: a fly-to started while the canvas is still
  // 0x0 computed a wrong zoom (the city ended up far too close)
  const wasCompact = useRef(compact);
  useEffect(() => {
    if (wasCompact.current === compact) return; wasCompact.current = compact;
    setViewState((v: any) => ({ ...v, longitude: HOME.longitude, latitude: HOME.latitude, bearing: HOME.bearing, ...(compact ? { zoom: 10.2, pitch: 30 } : { zoom: HOME.zoom, pitch: HOME.pitch }),
      transitionDuration: 900, transitionInterpolator: new LinearInterpolator(["longitude", "latitude", "zoom", "pitch", "bearing"]) }));
  }, [compact]);
  const [now, setNow] = useState(0);
  const revealed = useRef(new globalThis.Map<number, Revealed>());
  const cursor = useRef(0);
  const peopleStart = useRef(0);
  const flowStart = useRef(0);
  const [picked, setPicked] = useState<number | null>(null);
  const policy = mode === "policy";

  // animation clock + paced reveal of answers (cached runs arrive at once; show them as a wave of ~4 s)
  useEffect(() => {
    let raf = 0;
    const tick = (t: number) => {
      const q = answers.current ?? [];
      if (cursor.current < q.length) {
        const k = Math.max(3, Math.ceil((q.length - cursor.current) / 110));   // ~3.5 s wave at 30 fps
        for (let i = 0; i < k && cursor.current < q.length; i++) {
          const [id, p, reason] = q[cursor.current++]; revealed.current.set(id, { p, reason, t });
        }
      }
      setNow(t); raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [answers]);

  // a new run: clear reveal state, fly back to the city
  useEffect(() => {
    if ((answers.current?.length ?? 0) === 0) { revealed.current.clear(); cursor.current = 0; }
  }, [answersTick, answers]);
  useEffect(() => {
    peopleStart.current = performance.now(); setPicked(null);
    if (people.length) setViewState({ ...HOME, ...(compact ? { zoom: 10.2, pitch: 30 } : {}), transitionDuration: 1600, transitionInterpolator: new FlyToInterpolator() });
  }, [people]);

  // stats for the HUD
  useEffect(() => {
    const id = setInterval(() => {
      const v = [...revealed.current.values()]; onStats({ revealed: v.length, mean: v.length ? v.reduce((a, b) => a + b.p, 0) / v.length : 0 });
    }, 120);
    return () => clearInterval(id);
  }, [onStats]);

  // event result: who travels to the selected venue
  const venue = result?.mode === "event" ? result.venues[selectedVenue] : null;
  const flows = useMemo<Flow[]>(() => {
    if (!venue || !people.length) return [];
    const to: [number, number] = [venue.latlon[1], venue.latlon[0]];
    const cand = people.map((p) => {
      const r = revealed.current.get(p.id); if (!r) return null;
      const km = Math.hypot((p.lon - to[0]) * 71.5, (p.lat - to[1]) * 111.2), mins = 8 + km * 3;
      return { p, w: r.p * 0.5 ** (Math.max(0, mins - 15) / 45) };
    }).filter(Boolean) as { p: Person; w: number }[];
    cand.sort((a, b) => b.w - a.w);
    const top = cand.slice(0, Math.min(320, Math.max(60, Math.round(cand.length * 0.28))));
    flowStart.current = performance.now() + 400;
    return top.map(({ p, w }, i) => ({ ...arc([p.lon, p.lat], to, (i / top.length) * 2600 + Math.random() * 300, 0.09), v: w }));
  }, [venue, people]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (venue) setViewState({ longitude: venue.latlon[1], latitude: venue.latlon[0] - 0.012, zoom: 11.6, pitch: 50, bearing: 8, transitionDuration: 2200, transitionInterpolator: new FlyToInterpolator() });
  }, [venue]);
  const flowT = now - flowStart.current;
  const arrived = flows.filter((f) => f.timestamps[f.timestamps.length - 1] <= flowT).length;

  const colorOf = (p: number, alpha: number): RGBA => {
    // policy: most people sit near 0.5, so stretch around it (0.3 -> full orange, 0.7 -> full blue)
    if (policy) { const u = Math.min(1, Math.max(0, (p - 0.5) * 2.5 + 0.5)); const c = u < 0.5 ? mix(ORANGE, GREY, u / 0.5) : mix(GREY, BLUE, (u - 0.5) / 0.5); return [c[0], c[1], c[2], alpha]; }
    const v = Math.min(1, p / 0.4), c = mix(COLD, AMBER, v); return [c[0], c[1], c[2], alpha * (0.45 + 0.55 * v)];
  };
  const sampleOn = people.length > 0;
  const sup = result?.mode === "policy" ? Object.values(result.districts).map((x) => x.support) : [];
  const lo = Math.min(...sup), hi = Math.max(...sup);
  // relative scale only when districts really differ (>= 3 pts); else absolute around 50% (one district, or near-equal ones,
  // must not turn orange just for being lowest)
  const rel = (name: string) => {
    const s = result?.mode === "policy" ? result.districts[name]?.support : undefined; if (s == null) return null;
    return hi - lo >= 0.03 ? (s - lo) / (hi - lo) : Math.min(1, Math.max(0, (s - 0.5) * 2.5 + 0.5));
  };
  const cv = choro ? Object.values(choro.values) : []; const clo = Math.min(...cv), chi = Math.max(...cv);
  const crel = (name: string) => { const v = choro?.values[name]; return v == null ? null : (v - clo) / Math.max(chi - clo, 1e-9); };
  const age = now - peopleStart.current;

  const layers = [
    new PolygonLayer({
      id: "districts", data: districts, getPolygon: (d: District) => d.ring, stroked: true,
      // always filled (an almost invisible fill when unused) so a click anywhere inside a district selects it
      filled: true, extruded: !!extrude && policy && result?.mode === "policy",   // flat by default: 3D blocks hide the residents
      getLineColor: (d: District) => (d.name === selDistrict ? [255, 255, 255, 230] : [140, 160, 200, 60]), lineWidthMinPixels: 1,
      getLineWidth: (d: District) => (d.name === selDistrict ? 60 : 1), lineWidthUnits: "meters",
      // relative scale: district support usually sits within a few points of the city mean, an absolute scale paints all grey
      getFillColor: (d: District) => {
        if (choro && !(policy && result?.mode === "policy")) { const c = crel(d.name); if (c == null) return [0, 0, 0, 1]; const m = mix([16, 60, 70], [110, 235, 220], c); return [m[0], m[1], m[2], 120]; }
        const u = rel(d.name); if (u == null) return d.name === selDistrict ? [255, 255, 255, 18] : [0, 0, 0, 1];
        const c = mix(ORANGE, BLUE, u); return [c[0], c[1], c[2], 60];
      },
      getElevation: (d: District) => { const u = rel(d.name); return u == null ? 0 : 80 + u * 1600; },
      wireframe: true, material: false,
      transitions: { getElevation: { duration: 1800 }, getFillColor: { duration: 1200 } },
      updateTriggers: { getFillColor: [result, choro, selDistrict], getElevation: [result], getLineColor: [selDistrict], getLineWidth: [selDistrict] }, pickable: true,
    }),
    new ScatterplotLayer({
      id: "population", data: population, getPosition: (d: number[]) => [d[0], d[1]], radiusUnits: "pixels",
      getRadius: 1.4, getFillColor: [130, 165, 230, sampleOn ? 40 : 120], updateTriggers: { getFillColor: [sampleOn] },
      transitions: { getFillColor: 800 },
    }),
    new ScatterplotLayer<Person>({
      id: "sample", data: people, getPosition: (d) => [d.lon, d.lat], radiusUnits: "pixels", pickable: true,
      getRadius: (d) => { const r = revealed.current.get(d.id); const v = r ? (policy ? Math.min(1, Math.abs(r.p - 0.5) * 5) : Math.min(1, r.p / 0.4)) : 0; return (r ? (policy ? 2.4 : 1.8) + 3 * v : 1.7) * (d.id === picked ? 2.2 : 1); },
      getFillColor: (d) => {
        const appear = Math.min(1, Math.max(0, (age - ((d.id * 7919) % 1500)) / 500));   // ripple in over 2 s
        const r = revealed.current.get(d.id);
        return r ? colorOf(r.p, 235) : [225, 230, 245, 150 * appear];
      },
      stroked: true, getLineColor: (d) => (d.id === picked ? [255, 255, 255, 255] : [0, 0, 0, 0]), lineWidthMinPixels: 1.5,
      updateTriggers: { getFillColor: [now], getRadius: [now, picked], getLineColor: [picked] },
      onClick: (info: PickingInfo<Person>) => { if (info.object) { setPicked(info.object.id); const r = revealed.current.get(info.object.id); onPick({ person: info.object, answer: r ? [info.object.id, r.p, r.reason] : undefined }); } },
    }),
    // a ring for every fresh answer: the moment Jev decided for this person
    new ScatterplotLayer<Person>({
      id: "pulses", data: people, getPosition: (d) => [d.lon, d.lat], radiusUnits: "pixels", stroked: true, filled: false, lineWidthMinPixels: 1.2,
      getRadius: (d) => { const r = revealed.current.get(d.id); const a = r ? now - r.t : 1e9; return a < 900 ? 2 + a / 900 * 14 : 0; },
      getLineColor: (d) => { const r = revealed.current.get(d.id); const a = r ? now - r.t : 1e9; const c = colorOf(r?.p ?? 0, 255); return a < 900 ? [c[0], c[1], c[2], 220 * (1 - a / 900)] : [0, 0, 0, 0]; },
      updateTriggers: { getRadius: [now], getLineColor: [now] },
    }),
    // data agent: residents a query is about (cyan, fade in), and OpenStreetMap places it counts (magenta, pop in)
    new ScatterplotLayer({
      id: "highlight", data: highlight?.points ?? [], getPosition: (d: number[]) => [d[0], d[1]] as [number, number], radiusUnits: "pixels", getRadius: 1.8,
      getFillColor: () => [90, 220, 255, Math.min(1, (now - hlAt.current) / 900) * 170], updateTriggers: { getFillColor: [Math.floor(now / 60)] },
    }),
    new ScatterplotLayer({
      id: "places", data: places?.points ?? [], getPosition: (d: any[]) => [d[0], d[1]], radiusUnits: "pixels", pickable: false,
      getRadius: (d: any[], { index }: any) => { const a = (now - plAt.current - index * 6) / 500; return a < 0 ? 0 : Math.min(1, a) * 5 + (a < 1.4 ? Math.max(0, 1 - Math.abs(a - 1)) * 4 : 0); },
      getFillColor: [235, 90, 255, 230], stroked: true, getLineColor: [255, 255, 255, 200], lineWidthMinPixels: 1,
      updateTriggers: { getRadius: [Math.floor(now / 30)] },
    }),
    new TripsLayer<Flow>({
      id: "flows", data: flows, getPath: (d) => d.path, getTimestamps: (d) => d.timestamps, getColor: [255, 196, 90],
      widthMinPixels: 2, capRounded: true, jointRounded: true, trailLength: 650, currentTime: flowT, fadeTrail: true, opacity: 0.9,
    }),
    // policy about one place: a pin + 1 km / 3 km rings (the agent defines neighbours by distance)
    ...(result?.mode === "policy" && result.location ? [
      new ScatterplotLayer({
        id: "loc-rings", data: [1000, 3000], getPosition: () => [result.location!.lon, result.location!.lat], getRadius: (r: number) => r, radiusUnits: "meters",
        filled: false, stroked: true, getLineColor: [255, 255, 255, 140], lineWidthMinPixels: 1.5,
      }),
      new ScatterplotLayer({
        id: "loc-pin", data: [result.location], getPosition: (l: any) => [l.lon, l.lat], radiusUnits: "pixels", getRadius: 9 + 2 * Math.sin(now / 280),
        getFillColor: [255, 90, 80, 240], stroked: true, getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2, updateTriggers: { getRadius: [now] },
      }),
      new TextLayer({
        id: "loc-label", data: [result.location], getPosition: (l: any) => [l.lon, l.lat], getPixelOffset: [0, -26], getText: (l: any) => l.name,
        getSize: 14, getColor: [255, 255, 255, 255], fontWeight: 700, outlineWidth: 4, outlineColor: [10, 10, 14, 255], fontSettings: { sdf: true }, characterSet: "auto",
        fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
      }),
    ] : []),
    ...(result?.mode === "event" ? [
      new ScatterplotLayer({
        id: "venues", data: result.venues.slice(0, 8), getPosition: (v: any) => [v.latlon[1], v.latlon[0]], radiusUnits: "pixels",
        getRadius: (v: any) => (v === venue ? 10 + 3 * Math.sin(now / 260) : 6), getFillColor: (v: any) => (v === venue ? [255, 90, 80, 240] : [20, 22, 30, 230]),
        stroked: true, getLineColor: (v: any) => (v === venue ? [255, 255, 255, 255] : [255, 186, 73, 220]), lineWidthMinPixels: 1.5,
        updateTriggers: { getRadius: [now, venue], getFillColor: [venue], getLineColor: [venue] },
      }),
      new TextLayer({
        id: "venue-label", data: venue ? [venue] : [], getPosition: (v: any) => [v.latlon[1], v.latlon[0]], getPixelOffset: [0, -30],
        getText: (v: any) => `${v.name}\n${v.expected != null ? "~" + Math.round((v.expected * arrived) / Math.max(flows.length, 1)).toLocaleString("pl-PL") + " osób" : ""}`,
        getSize: 15, getColor: [255, 255, 255, 255], fontWeight: 700, fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif", lineHeight: 1.25, outlineWidth: 4, outlineColor: [10, 10, 14, 255], fontSettings: { sdf: true },
        characterSet: "auto", updateTriggers: { getText: [arrived, venue] },
      }),
    ] : []),
    // arrived trips stay as a faint web: where this venue's audience comes from
    new PathLayer<Flow>({
      id: "flow-web", data: flows, getPath: (d) => d.path, widthMinPixels: 1, getColor: (d) => [255, 196, 90, d.timestamps[d.timestamps.length - 1] <= flowT ? 38 : 0],
      updateTriggers: { getColor: [arrived] },
    }),
  ];

  return (
    <DeckGL viewState={viewState} onViewStateChange={(e: any) => setViewState(e.viewState)} controller layers={layers}
      getCursor={({ isHovering }) => (isHovering ? "pointer" : "grab")}
      onClick={(info) => {
        // dots cover the polygons, so pick the district from the clicked point, not from the picked object
        const d = info.coordinate ? districtAt(districts, info.coordinate as [number, number]) : null;
        const person = info.object && info.layer?.id !== "districts";
        // a dot is jittered within its grid cell, so its own home district beats the clicked point
        if (person) { const home = (info.object as Person).district; onDistrict?.(districts.some((x) => x.name === home) ? home : d); return; }
        setPicked(null); onPick(null);
        onDistrict?.(d && d !== selDistrict ? d : null);
      }}>
      <Map mapStyle={STYLE} attributionControl={{ compact: true }} />
    </DeckGL>
  );
}
