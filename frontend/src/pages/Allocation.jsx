import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Button, Empty, ErrorNote, Loading, PageHeader, Panel, Tag, useApi } from '../components/ui'
import { fmtHours, fmtShort } from '../utils/format'

// One row per person, one column per week. Each cell is the hours they are booked that week
// across every active project, coloured against the 40-hour week.
const STATUS = {
  free: { label: 'Unassigned', tone: 'muted' },
  ok: { label: 'Within capacity', tone: 'good' },
  near: { label: 'Approaching capacity', tone: 'warn' },
  over: { label: 'Over-allocated', tone: 'bad' },
}
const WEEKS = 12
const SHOW_ALL_MAX = 60

const iso = (d) => d.toISOString().slice(0, 10)
const shift = (from, weeks) => { const d = new Date(from + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + weeks * 7); return iso(d) }
const thisMonday = () => { const d = new Date(); const day = (d.getUTCDay() + 6) % 7; d.setUTCDate(d.getUTCDate() - day); return iso(d) }

export default function Allocation() {
  const [from, setFrom] = useState(thisMonday)
  const [picked, setPicked] = useState(null) // {personId, week}
  const [showAll, setShowAll] = useState(false)
  const { data, error, loading, reload } = useApi(() => api.get('/projects/allocation', { from, weeks: WEEKS }), [from])
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  const everyone = data.people.filter((p) => p.role !== 'admin')
  const people = showAll || everyone.length <= SHOW_ALL_MAX ? everyone : everyone.slice(0, SHOW_ALL_MAX)
  const cap = data.weekly_cap
  const over = everyone.filter((p) => p.weeks_over > 0)
  const near = everyone.filter((p) => p.weeks_over === 0 && p.weeks_near > 0)
  const idle = everyone.filter((p) => p.peak_hours === 0)
  const today = thisMonday()

  const sel = picked && everyone.find((p) => p.id === picked.personId)
  const selWeek = sel && sel.weeks.find((w) => w.week_start === picked.week)

  return (
    <>
      <PageHeader
        title="Allocation"
        subtitle={`Hours each person is booked per week across all active projects, for the ${WEEKS} weeks from ${fmtShort(data.from)}. The limit is ${cap} hours a week.`}
        actions={(
          <div className="heat-nav">
            <Button variant="ghost" size="sm" onClick={() => setFrom(shift(from, -WEEKS))} aria-label="Earlier weeks">‹ Earlier</Button>
            <Button variant="ghost" size="sm" onClick={() => setFrom(thisMonday())} disabled={from === today}>This week</Button>
            <Button variant="ghost" size="sm" onClick={() => setFrom(shift(from, WEEKS))} aria-label="Later weeks">Later ›</Button>
          </div>
        )}
      />
      <div className="row-tags">
        <Tag tone={over.length ? 'bad' : 'good'}>{over.length ? `${over.length} over-allocated in this window` : 'No one is over-allocated'}</Tag>
        <Tag tone={near.length ? 'warn' : 'muted'}>{near.length} approaching capacity</Tag>
        <Tag tone="muted">{idle.length} with no assignments</Tag>
      </div>

      <Panel title="Workload by week" actions={<Legend cap={cap} near={data.near_capacity} />}>
        {people.length ? (
          <div className="table-wrap">
            <table className="heat" aria-label="Hours booked per person per week">
              <thead>
                <tr>
                  <th scope="col" className="heat-who">Person</th>
                  {data.weeks.map((w) => (
                    <th key={w} scope="col" className={`heat-week ${w === today ? 'now' : ''}`}>
                      <span className="small">{fmtShort(w)}</span>
                    </th>
                  ))}
                  <th scope="col" className="heat-peak num">Peak</th>
                </tr>
              </thead>
              <tbody>
                {people.map((p) => (
                  <tr key={p.id}>
                    <th scope="row" className="heat-who">
                      <span className="heat-name">{p.name}</span>
                      <span className="muted small">{p.job_title || p.role}</span>
                    </th>
                    {p.weeks.map((w) => {
                      const on = picked && picked.personId === p.id && picked.week === w.week_start
                      const names = w.assignments.map((a) => `${a.project_name} ${fmtHours(a.hours)}`).join(', ')
                      return (
                        <td key={w.week_start} className={`heat-cell ${w.status} ${on ? 'picked' : ''} ${w.week_start === today ? 'now' : ''}`}>
                          <button
                            type="button"
                            onClick={() => setPicked(on ? null : { personId: p.id, week: w.week_start })}
                            title={`${p.name}, week of ${fmtShort(w.week_start)}: ${fmtHours(w.hours)} of ${cap}${names ? ` (${names})` : ''}`}
                            aria-label={`${p.name}, week of ${fmtShort(w.week_start)}, ${fmtHours(w.hours)}, ${STATUS[w.status].label}`}
                          >
                            {w.hours > 0 ? Number(w.hours).toLocaleString('en-US', { maximumFractionDigits: 1 }) : ''}
                          </button>
                        </td>
                      )
                    })}
                    <td className={`heat-peak num ${p.weeks_over ? 'text-bad' : ''}`}>{fmtHours(p.peak_hours)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty title="No people to show" />}
        {everyone.length > SHOW_ALL_MAX && !showAll && (
          <div className="form-actions"><Button variant="ghost" onClick={() => setShowAll(true)}>Show all {everyone.length} people</Button></div>
        )}
      </Panel>

      {sel && selWeek && (
        <Panel
          title={`${sel.name}, week of ${fmtShort(selWeek.week_start)}`}
          actions={<Button variant="ghost" size="sm" onClick={() => setPicked(null)}>Close</Button>}
        >
          <div className="row-tags">
            <Tag tone={STATUS[selWeek.status].tone}>{STATUS[selWeek.status].label}</Tag>
            <span className="muted">
              {fmtHours(selWeek.hours)} of {cap} booked
              {selWeek.hours > cap ? `, ${fmtHours(selWeek.hours - cap)} over` : `, ${fmtHours(cap - selWeek.hours)} free`}
            </span>
          </div>
          {selWeek.assignments.length ? (
            <ul className="heat-detail">
              {selWeek.assignments.map((a) => (
                <li key={a.project_id}>
                  <Link to={`/projects/${a.project_id}?tab=team`}>{a.project_name}</Link>
                  <span className="num">{fmtHours(a.hours)} / week</span>
                </li>
              ))}
            </ul>
          ) : <p className="muted">No assignments this week.</p>}
          {selWeek.status === 'over' && (
            <p className="field-hint">To fix this, open one of the projects above and lower the hours or shorten the dates on the Team tab.</p>
          )}
        </Panel>
      )}
    </>
  )
}

function Legend({ cap, near }) {
  return (
    <div className="heat-legend" aria-label="Legend">
      <span><i className="heat-sw ok" /> Within capacity (under {near} h)</span>
      <span><i className="heat-sw near" /> Approaching ({near} to {cap} h)</span>
      <span><i className="heat-sw over" /> Over-allocated (above {cap} h)</span>
    </div>
  )
}
