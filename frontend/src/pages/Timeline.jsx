import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Empty, ErrorNote, Loading, PageHeader, Panel, Tag, useApi } from '../components/ui'
import { fmtShort } from '../utils/format'

// Portfolio timeline: every active project on one time axis, with its milestones,
// the projected finish, and how far past the end date it is heading.

const DAY = 86400000
const toMs = (iso) => Date.parse(`${iso}T00:00:00Z`)

export default function Timeline() {
  const { data, error, loading, reload } = useApi(() => api.get('/milestones/timeline'), [])
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />
  const projects = data.projects
  if (!projects.length) return <><PageHeader title="Timeline" /><Empty title="No active projects" /></>

  const start = Math.min(...projects.map((p) => toMs(p.start_date)))
  const end = Math.max(...projects.map((p) => Math.max(toMs(p.end_date), toMs(p.projected_finish))))
  const days = Math.round((end - start) / DAY) + 1
  const pct = (iso) => `${((toMs(iso) - start) / DAY / days) * 100}%`
  const width = (a, b) => `${Math.max(((toMs(b) - toMs(a)) / DAY + 1) / days * 100, 0.4)}%`
  const today = projects[0].today
  const late = projects.filter((p) => p.days_past_project_end > 0)

  const months = []
  const first = new Date(start); first.setUTCDate(1)
  for (let d = new Date(first); d.getTime() <= end; d.setUTCMonth(d.getUTCMonth() + 1)) {
    if (d.getTime() >= start) months.push({ left: pct(d.toISOString().slice(0, 10)), label: d.toLocaleString('en-US', { month: 'short', year: '2-digit', timeZone: 'UTC' }) })
  }

  return (
    <>
      <PageHeader title="Timeline" subtitle="Every active project on one calendar: milestones, where each one stands today, and the projected finish from its critical path." />
      <div className="row-tags">
        <Tag tone={late.length ? 'bad' : 'good'}>{late.length ? `${late.length} projected past the end date` : 'Every project projected on time'}</Tag>
        <Tag tone="muted">{projects.length} active</Tag>
      </div>
      <Panel>
        <div className="table-wrap">
          <div className="tl" style={{ minWidth: Math.max(720, days * 6) }}>
            <div className="tl-axis">
              {months.map((m) => <span key={m.label + m.left} style={{ left: m.left }}>{m.label}</span>)}
              {today && <b style={{ left: pct(today) }}>Today</b>}
            </div>
            {projects.map((p) => (
              <div key={p.id} className="tl-row">
                <div className="tl-who">
                  <Link to={`/projects/${p.id}?tab=timeline`}>{p.name}</Link>
                  <span className="muted small">{p.manager_name}{p.days_past_project_end > 0 ? ` · ${p.days_past_project_end} d late` : ''}</span>
                </div>
                <div className="tl-track">
                  <div className="tl-project" style={{ left: pct(p.start_date), width: width(p.start_date, p.end_date) }} title={`${p.name}: ${fmtShort(p.start_date)} to ${fmtShort(p.end_date)}`} />
                  {p.milestones.map((m) => (
                    <Link key={m.id} to={`/projects/${p.id}?milestone=${m.id}`}
                      className={`tl-ms ${m.status} ${m.critical ? 'critical' : ''} ${m.overdue ? 'overdue' : ''}`}
                      style={{ left: pct(m.start_date), width: width(m.start_date, m.end_date) }}
                      title={`${m.position}. ${m.name}: ${fmtShort(m.start_date)} to ${fmtShort(m.end_date)}, ${m.progress_pct}% done${m.critical ? ', critical path' : ''}${m.slip_days > 0 ? `, projected ${m.slip_days} d late` : ''}`}>
                      <i style={{ width: `${m.progress_pct}%` }} /><span>{m.name}</span>
                    </Link>
                  ))}
                  {toMs(p.projected_finish) > toMs(p.end_date) && (
                    <div className="tl-slip" style={{ left: pct(p.end_date), width: width(p.end_date, p.projected_finish) }} title={`Projected finish ${fmtShort(p.projected_finish)}`} />
                  )}
                  <i className="tl-end" style={{ left: pct(p.end_date) }} title={`End date ${fmtShort(p.end_date)}`} />
                  {today && <i className="tl-today" style={{ left: pct(today) }} />}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="heat-legend" style={{ marginTop: 12 }}>
          <span><i className="gantt-sw done" /> Done</span>
          <span><i className="gantt-sw planned" /> Planned</span>
          <span><i className="gantt-sw slip" /> Projected overrun</span>
          <span><i className="gantt-sw critical" /> Critical path</span>
        </div>
      </Panel>
    </>
  )
}
