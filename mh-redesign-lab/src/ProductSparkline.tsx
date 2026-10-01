import "./ProductSparkline.css";

const nf = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });

/** Inline-SVG sparkline: min/max scaled polyline, no animation, no dependencies. */
export function ProductSparkline({
  values,
  label = "Выручка",
  unit = "₽",
  width = 96,
  height = 28,
}: {
  values: number[];
  label?: string;
  unit?: string;
  width?: number;
  height?: number;
}) {
  const fmt = (n: number) => `${nf.format(Math.round(n))}${unit ? ` ${unit}` : ""}`;
  if (!values.length) return <span className="product-sparkline-empty">—</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const total = values.reduce((sum, v) => sum + v, 0);
  const pad = 2;
  const span = max - min;
  const y = (v: number) => (span === 0 ? height / 2 : height - pad - ((v - min) / span) * (height - 2 * pad));
  const step = values.length > 1 ? (width - 2 * pad) / (values.length - 1) : 0;
  const points = values.map((v, i) => `${(pad + i * step).toFixed(1)},${y(v).toFixed(1)}`);
  const last = points[points.length - 1].split(",");
  const aria = max === 0 && min === 0
    ? `${label} за период: без изменений, итого ${fmt(0)}`
    : `${label} за период: от ${fmt(min)} до ${fmt(max)}, итого ${fmt(total)}`;
  return (
    <svg className="product-sparkline" role="img" aria-label={aria} width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <polyline points={points.join(" ")} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r="2" fill="currentColor" />
    </svg>
  );
}
