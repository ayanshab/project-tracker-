import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Empty, ErrorNote, Loading, PageHeader, Panel, Tag, useApi } from '../components/ui'

const PALETTE = ['#0F6B4F', '#B48A4E', '#2F5D8A', '#7A4E8C', '#1E7F8C', '#8C5A2B', '#5B6676']

export default function Allocation() {
  const { data, error, loading, reload } = useApi(() => api.get('/projects/allocation'), [])
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  const people = data.people.filter((p) => p.role !== 'admin')
  const projectColor = {}
  people.forEach((p) => p.assignments.forEach((a) => {
    if (!(a.project_id in projectColor)) projectColor[a.project_id] = PALETTE[Object.keys(projectColor).length % PALETTE.length]
  }))
  const over = people.filter((p) => p.over_allocated).length
  const free = people.filter((p) => p.hours_this_week === 0).length

  return (
    <>
      <PageHeader title="Allocation" subtitle="Hours each person is booked this week, across all active projects. The limit is 40 hours." />
      <div className="row-tags">
        <Tag tone={over ? 'bad' : 'good'}>{over ? `${over} over 40 hours` : 'No one is over-allocated'}</Tag>
        <Tag tone={free ? 'warn' : 'muted'}>{free} unassigned</Tag>
      </div>
      <Panel>
        {people.length ? (
          <div className="alloc">
            {people.map((p) => (
              <div key={p.id} className="alloc-row">
                <div className="alloc-who">
                  <span>{p.name}</span>
                  <span className="muted small">{p.job_title || p.role}</span>
                </div>
                <div className="alloc-bar" aria-label={`${p.hours_this_week} of 40 hours`}>
                  {p.assignments.map((a) => (
                    <Link key={a.project_id} to={`/projects/${a.project_id}?tab=team`} className="alloc-seg"
                      style={{ width: `${(Math.min(a.hours_per_week, 40) / 40) * 100}%`, background: projectColor[a.project_id] }}
                      title={`${a.project_name}: ${a.hours_per_week} h/week`}>
                      <span>{a.project_name}</span>
                    </Link>
                  ))}
                  <span className="alloc-cap" aria-hidden="true" />
                </div>
                <div className={`alloc-num num ${p.over_allocated ? 'bad' : ''}`}>{p.hours_this_week} h</div>
              </div>
            ))}
          </div>
        ) : <Empty title="No people to show" />}
      </Panel>
    </>
  )
}
