import { useState } from 'react';
import { clsx } from 'clsx';

export interface DonutSegment { value: number; color: string; label: string; tooltip?: string; }

export function DonutChart({
  segments, size = 140, strokeWidth = 22, centerTop, centerBottom, onSegmentClick, selectedIndex = null,
}: {
  segments: DonutSegment[]; size?: number; strokeWidth?: number;
  centerTop?: string; centerBottom?: string;
  onSegmentClick?: (index: number) => void;
  selectedIndex?: number | null;
}) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const r = (size - strokeWidth) / 2;
  const cx = size / 2, cy = size / 2;
  const circumference = 2 * Math.PI * r;
  const total = segments.reduce((s, seg) => s + seg.value, 0);
  let cumAngle = -90;

  return (
    <div className="relative">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {total === 0 ? (
          <circle cx={cx} cy={cy} r={r} fill="none" style={{ stroke: 'rgb(var(--slate-700))' }} strokeWidth={strokeWidth} />
        ) : (
          segments.map((seg, i) => {
            if (seg.value === 0) return null;
            const fraction = seg.value / total;
            const dash = Math.max(fraction * circumference - 2, 0);
            const startAngle = cumAngle;
            cumAngle += fraction * 360;
            const isHover = hoverIdx === i;
            const isSelected = selectedIndex === i;
            const isDimmed = (hoverIdx !== null && !isHover) || (selectedIndex !== null && !isSelected && hoverIdx === null);
            return (
              <circle key={i} cx={cx} cy={cy} r={r} fill="none" stroke={seg.color}
                strokeWidth={isHover || isSelected ? strokeWidth + 6 : strokeWidth} strokeLinecap="butt"
                strokeDasharray={`${dash} ${circumference - dash}`}
                transform={`rotate(${startAngle}, ${cx}, ${cy})`}
                className={clsx('transition-all duration-150', onSegmentClick && 'cursor-pointer')}
                style={{ opacity: isDimmed ? 0.4 : 1 }}
                onMouseEnter={() => setHoverIdx(i)}
                onMouseLeave={() => setHoverIdx(null)}
                onClick={() => onSegmentClick?.(i)} />
            );
          })
        )}
        {centerTop && (
          <text x={cx} y={cy - 7} textAnchor="middle" dominantBaseline="middle"
            style={{ fill: 'rgb(var(--slate-200))' }} fontSize="22" fontWeight="700" fontFamily="system-ui">{centerTop}</text>
        )}
        {centerBottom && (
          <text x={cx} y={cy + 13} textAnchor="middle" dominantBaseline="middle"
            style={{ fill: 'rgb(var(--slate-400))' }} fontSize="11" fontFamily="system-ui">{centerBottom}</text>
        )}
      </svg>
      {hoverIdx !== null && segments[hoverIdx] && (
        <div className="absolute -top-10 left-1/2 -translate-x-1/2 bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 shadow-lg pointer-events-none z-10 whitespace-nowrap">
          <p className="text-xs font-semibold text-slate-200">
            {segments[hoverIdx].label}: {segments[hoverIdx].value}건
            <span className="text-slate-400 ml-1">({Math.round((segments[hoverIdx].value / total) * 100)}%)</span>
          </p>
          {segments[hoverIdx].tooltip && (
            <p className="text-[10px] text-slate-400 mt-0.5">{segments[hoverIdx].tooltip}</p>
          )}
        </div>
      )}
    </div>
  );
}
