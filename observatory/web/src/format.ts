// Plain-words formatting shared by the pages.

const DATE = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" });

export function formatDate(iso: string): string {
  return DATE.format(new Date(`${iso.slice(0, 10)}T00:00:00`));
}

export function formatNumber(x: number | null | undefined, digits = 4): string {
  if (x === null || x === undefined) return "—";
  return Number(x.toPrecision(digits)).toLocaleString("en-IN", { maximumFractionDigits: 6 });
}

export function sessions(n: number): string {
  return n === 1 ? "1 session" : `${n} sessions`;
}
