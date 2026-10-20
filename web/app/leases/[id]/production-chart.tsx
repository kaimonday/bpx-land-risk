import { fmtMonth, fmtNumber } from "@/lib/api";

interface Point { production_month: string; boe: number }

// Monthly BOE as a line, with the trailing no-production months shaded so the
// gap that drives the HBP flag is visible at a glance.
export default function ProductionChart({ data }: { data: Point[] }) {
  if (data.length === 0) {
    return <p className="muted">No RRC production is linked to this tract.</p>;
  }
  const W = 640, H = 220, L = 56, R = 12, T = 12, B = 30;
  const max = Math.max(...data.map((d) => d.boe), 1);
  const x = (i: number) => L + (i / Math.max(data.length - 1, 1)) * (W - L - R);
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const path = data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.boe).toFixed(1)}`).join(" ");

  let gapStart = data.length;
  while (gapStart > 0 && data[gapStart - 1].boe <= 0) gapStart--;
  const hasGap = gapStart < data.length;

  const ticks = [0, max / 2, max];
  const years = data
    .map((d, i) => ({ i, m: d.production_month }))
    .filter(({ m }) => m.slice(5, 7) === "01");

  return (
    <figure style={{ margin: 0 }}>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label={`Monthly production in BOE from ${fmtMonth(data[0].production_month)} to ${fmtMonth(data[data.length - 1].production_month)}`}>
        {hasGap && (
          <rect x={x(Math.max(gapStart - 1, 0))} y={T} width={x(data.length - 1) - x(Math.max(gapStart - 1, 0))}
                height={H - T - B} fill="var(--high-tint)" />
        )}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--rule)" />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--ink-muted)">{fmtNumber(t)}</text>
          </g>
        ))}
        {years.map(({ i, m }) => (
          <text key={m} x={x(i)} y={H - 10} textAnchor="middle" fontSize="11" fill="var(--ink-muted)">{m.slice(0, 4)}</text>
        ))}
        <path d={path} fill="none" stroke="var(--ink)" strokeWidth="1.75" />
      </svg>
      <figcaption className="small muted" style={{ marginTop: "0.4rem" }}>
        Barrels of oil equivalent per month, allocated to this tract.
        {hasGap && " Shaded months reported no production."}
      </figcaption>
    </figure>
  );
}
