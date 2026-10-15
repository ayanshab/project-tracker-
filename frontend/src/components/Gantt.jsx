import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Button, Empty, ErrorNote, Loading, Panel, Tag, useApi } from '../components/ui'
import { fmtDate, fmtShort } from '../utils/format'

// Interactive Gantt chart for one project: planned bars with progress fill, projected overrun,
// dependency arrows, the critical path, today's line, and a "what if this slips" control.

const DAY = 86400000
const toMs = (iso) => Date.parse(`${iso}T00:00:00Z`)
const ROW = 38
const LEFT = 190
const TOP = 34
const RIGHT = 70
const MIN_WIDTH = 760

export default function Gantt({ projectId, canManage }) {
  const [sim, setSim] = useState({ id: '', days: 7, on: false })
  const simulate = sim.on && sim.id ? `${sim.id}:${sim.days}` : undefined
  const { data, error, loading, reload } = useApi(() => api.get('/milestones/schedule', { project_id: projectId, simulate }), [projectId, simulate])
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />
  if (!data.milestones.length) return <Empty title="No milestones yet">Add milestones to see the timeline and critical path.</Empty>

  const ms = data.milestones
  const critical = new Set(data.critical_path)
  const late = data.days_past_project_end
  const candidates = ms.filter((m) => m.status !== 'completed')

  return (
    <>
      <Panel title="Schedule at a glance">
        <div className="gantt-stats">
          <Fact label="Critical path" value={`${data.critical_length_days} days`} sub={`${critical.size} of ${ms.length} milestones, no slack`} />
          <Fact label="Earliest possible finish" value={fmtDate(data.earliest_finish)} sub="If every milestone runs exactly to plan" />
          <Fact label="Projected finish" value={fmtDate(data.projected_finish)} sub={late ? `${late} days after the end date` : data.delay_days > 0 ? `${data.delay_days} days after the planned finish` : 'On or before the planned finish'} tone={late ? 'bad' : data.delay_days > 0 ? 'warn' : 'good'} />
          <Fact label="Project end date" value={fmtDate(data.project_end)} sub={sim.on && sim.id ? 'Showing a what-if slip' : 'The commitment'} />
        </div>
        {canManage && candidates.length > 0 && (
          <div className="whatif">
            <label className="check">
              <input type="checkbox" checked={sim.on} onChange={(e) => setSim((s) => ({ ...s, on: e.target.checked, id: s.id || candidates[0].id }))} />
              What if
            </label>
            <select value={sim.id || candidates[0].id} onChange={(e) => setSim((s) => ({ ...s, id: e.target.value }))} disabled={!sim.on} aria-label="Milestone that slips">
              {candidates.map((m) => <option key={m.id} value={m.id}>{m.position}. {m.name}</option>)}
            </select>
            <span className="muted small">slips by</span>
            <input type="number" min="1" max="365" value={sim.days} onChange={(e) => setSim((s) => ({ ...s, days: Math.max(1, Number(e.target.value) || 1) }))} disabled={!sim.on} aria-label="Days of slip" />
            <span className="muted small">days</span>
            {sim.on && sim.id && <SimResult data={data} id={sim.id} />}
          </div>
        )}
      </Panel>

      <Panel title="Timeline" actions={<Legend />}>
        <div className="table-wrap">
          <Chart data={data} />
        </div>
        <p className="field-hint">Arrows show which milestone must finish before the next can start. Milestones on the critical path are outlined in gold: if one of them slips, the project finish slips by the same amount. Others can absorb a slip up to their slack.</p>
      </Panel>

      <Panel title="Milestones and slack">
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>#</th><th>Milestone</th><th>Depends on</th><th>Planned</th><th>Projected end</th><th className="num">Slack</th><th>Status</th></tr></thead>
            <tbody>
              {ms.map((m) => (
                <tr key={m.id} className={m.critical ? 'gantt-critical-row' : ''}>
                  <td className="num">{m.position}</td>
                  <td><Link to={`?milestone=${m.id}`}>{m.name}</Link>{m.critical && <Tag tone="gold" title="On the critical path">Critical</Tag>}</td>
                  <td className="muted small">{m.depends_on.length ? m.depends_on.map((d) => ms.find((x) => x.id === d)?.position).filter(Boolean).map((n) => `#${n}`).join(', ') : 'Nothing'}</td>
                  <td className="nowrap">{fmtShort(m.start_date)} to {fmtShort(m.end_date)}</td>
                  <td className={`nowrap ${m.slip_days > 0 && m.status !== 'completed' ? 'text-bad' : ''}`}>{m.status === 'completed' ? `Done ${fmtShort(m.projected_end)}` : fmtShort(m.projected_end)}{m.slip_days > 0 ? ` (${m.status === 'completed' ? `${m.slip_days} d late` : `+${m.slip_days} d`})` : ''}</td>
                  <td className="num">{m.critical ? '0 d' : `${m.slack_days} d`}</td>
                  <td>
                    {m.status === 'completed' ? <Tag tone="good">Completed</Tag> : m.overdue ? <Tag tone="bad">Overdue</Tag> : m.status === 'in_progress' ? <Tag tone="info">{m.progress_pct}% done</Tag> : <Tag tone="muted">Not started</Tag>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  )
}

function SimResult({ data, id }) {
  const m = data.milestones.find((x) => x.id === id)
  if (!m) return null
  const late = data.days_past_project_end
  const tone = late ? 'bad' : data.delay_days > 0 ? 'warn' : 'good'
  const text = m.critical
    ? `${m.name} is on the critical path, so the finish moves to ${fmtShort(data.projected_finish)}${late ? `, ${late} days past the end date` : ''}.`
    : late || data.delay_days > 0
      ? `That uses up ${m.name}'s ${m.slack_days} days of slack. The finish moves to ${fmtShort(data.projected_finish)}${late ? `, ${late} days past the end date` : ''}.`
      : `${m.name} has ${m.slack_days} days of slack, so the finish date does not move.`
  return <Tag tone={tone}>{text}</Tag>
}

function Fact({ label, value, sub, tone }) {
  return (
    <div className={`fact ${tone ? `fact-${tone}` : ''}`}>
      <span className="fact-label">{label}</span>
      <span className="fact-value">{value}</span>
      <span className="muted small">{sub}</span>
    </div>
  )
}

function Legend() {
  return (
    <div className="heat-legend" aria-label="Legend">
      <span><i className="gantt-sw done" /> Done</span>
      <span><i className="gantt-sw planned" /> Planned</span>
      <span><i className="gantt-sw slip" /> Projected overrun</span>
      <span><i className="gantt-sw critical" /> Critical path</span>
    </div>
  )
}

function Chart({ data }) {
  const ms = data.milestones
  const start = Math.min(toMs(data.project_start), ...ms.map((m) => toMs(m.start_date)))
  const end = Math.max(toMs(data.project_end), toMs(data.projected_finish), ...ms.map((m) => Math.max(toMs(m.end_date), toMs(m.projected_end))))
  const days = Math.max(Math.round((end - start) / DAY) + 1, 7)
  const width = Math.max(MIN_WIDTH, days * 9 + LEFT + RIGHT)
  const span = width - LEFT - RIGHT
  const x = (iso) => LEFT + ((toMs(iso) - start) / DAY / days) * span
  const xEnd = (iso) => LEFT + ((toMs(iso) - start) / DAY + 1) / days * span
  const height = TOP + ms.length * ROW + 24
  const today = data.today
  const rows = Object.fromEntries(ms.map((m, i) => [m.id, i]))

  // Month ticks
  const ticks = []
  const first = new Date(start); first.setUTCDate(1)
  for (let d = new Date(first); d.getTime() <= end; d.setUTCMonth(d.getUTCMonth() + 1)) {
    if (d.getTime() >= start) ticks.push({ x: x(d.toISOString().slice(0, 10)), label: d.toLocaleString('en-US', { month: 'short', year: '2-digit', timeZone: 'UTC' }) })
  }

  return (
    <svg className="gantt" width={width} height={height} role="img" aria-label="Milestone timeline">
      {ticks.map((t) => (
        <g key={t.label + t.x}>
          <line x1={t.x} x2={t.x} y1={TOP - 6} y2={height - 20} className="gantt-grid" />
          <text x={t.x + 4} y={14} className="gantt-tick">{t.label}</text>
        </g>
      ))}
      {ms.map((m, i) => {
        const y = TOP + i * ROW
        const planned = [x(m.start_date), xEnd(m.end_date)]
        // Completed milestones are history: the table says if they finished late, the bar stays green.
        const proj = m.status !== 'completed' && toMs(m.projected_end) > toMs(m.end_date) ? [Math.max(planned[1], x(m.end_date)), xEnd(m.projected_end)] : null
        const fillW = (planned[1] - planned[0]) * (m.progress_pct / 100)
        return (
          <g key={m.id} className={`gantt-row ${m.critical ? 'critical' : ''} ${m.status}`}>
            <text x={8} y={y + 20} className="gantt-label">{m.position}. {m.name.length > 24 ? `${m.name.slice(0, 23)}…` : m.name}</text>
            <rect x={planned[0]} y={y + 8} width={Math.max(planned[1] - planned[0], 3)} height={20} rx={4} className="gantt-bar" />
            {fillW > 0 && <rect x={planned[0]} y={y + 8} width={fillW} height={20} rx={4} className="gantt-fill" />}
            {proj && <rect x={proj[0]} y={y + 8} width={Math.max(proj[1] - proj[0], 2)} height={20} rx={4} className="gantt-slip"><title>Projected to run {m.slip_days} days over</title></rect>}
            {m.critical && <rect x={planned[0] - 1.5} y={y + 6.5} width={Math.max(planned[1] - planned[0], 3) + 3} height={23} rx={5} className="gantt-critical" />}
            <text x={(proj ? proj[1] : planned[1]) + 6} y={y + 22} className="gantt-pct">{m.status === 'completed' ? 'Done' : `${m.progress_pct}%`}</text>
            <title>{m.name}: {fmtShort(m.start_date)} to {fmtShort(m.end_date)}, {m.progress_pct}% done{m.slip_days > 0 ? `, projected ${m.slip_days} days late` : ''}{m.critical ? ', on the critical path' : `, ${m.slack_days} days of slack`}</title>
          </g>
        )
      })}
      {ms.map((m) => m.depends_on.map((d) => {
        const from = ms.find((p) => p.id === d); if (!from) return null
        const y1 = TOP + rows[from.id] * ROW + 18
        const y2 = TOP + rows[m.id] * ROW + 18
        const x1 = xEnd(from.end_date)
        const x2 = x(m.start_date)
        const mid = Math.max(x1 + 6, x2 - 6)
        const path = x2 >= x1 + 12
          ? `M${x1},${y1} H${(x1 + x2) / 2} V${y2} H${x2 - 4}`
          : `M${x1},${y1} H${mid} V${y2 - ROW / 2} H${x2 - 10} V${y2} H${x2 - 4}`
        return <path key={`${d}-${m.id}`} d={path} className={`gantt-dep ${critical(from, m, data) ? 'critical' : ''}`} markerEnd="url(#gantt-arrow)" />
      }))}
      {today && toMs(today) >= start && toMs(today) <= end && (
        <g>
          <line x1={x(today)} x2={x(today)} y1={TOP - 8} y2={height - 20} className="gantt-today" />
          <text x={x(today) + 4} y={height - 8} className="gantt-tick today">Today</text>
        </g>
      )}
      <line x1={xEnd(data.project_end)} x2={xEnd(data.project_end)} y1={TOP - 8} y2={height - 20} className="gantt-end" />
      <text x={xEnd(data.project_end) - 4} y={height - 8} className="gantt-tick end" textAnchor="end">End date</text>
      <defs>
        <marker id="gantt-arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto" markerUnits="userSpaceOnUse">
          <path d="M0,0 L8,4 L0,8 z" className="gantt-arrowhead" />
        </marker>
      </defs>
    </svg>
  )
}

const critical = (from, to, data) => data.critical_path.includes(from.id) && data.critical_path.includes(to.id)
