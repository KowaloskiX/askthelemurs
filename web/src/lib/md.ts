// Minimal markdown for agent answers: **bold** and "- " bullet lists; text is HTML-escaped first.
const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!);
export function md(s: string) {
  const L = esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").split("\n"); let h = "", ul = false;
  for (const l of L) {
    const b = /^\s*[-•]\s+/.test(l);
    if (b && !ul) { h += "<ul>"; ul = true; } if (!b && ul) { h += "</ul>"; ul = false; }
    if (b) h += "<li>" + l.replace(/^\s*[-•]\s+/, "") + "</li>"; else if (l.trim()) h += "<p>" + l + "</p>";
  }
  return h + (ul ? "</ul>" : "");
}
