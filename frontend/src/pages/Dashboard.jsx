import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'
import { Empty, ErrorNote, Loading, PageHeader, Panel, Progress, Stat, Tag, useAction, useApi } from '../components/ui'
import ProjectRow from '../components/ProjectRow'
import { fmtHours, fmtMoney, fmtShort, fmtWeekday } from '../utils/format'

export default function Dashboard() {
  const { user } = useAuth()
  return user.role === 'employee' ? <EmployeeHome user={user} /> : <ManagerHome user={user} />
}

function ManagerHome({ user }) {
  const { data, error, loading, reload } = useApi(async () => {
    const [projects, allocation, portfolio] = await Promise.all([
      api.get('/projects'),
      api.get('/projects/allocation'),
      api.get('/budgets/portfolio'),
    ])
    return { projects: projects.projects, people: allocation.people, portfolio }
  })

  if (loading) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  const active = data.projects.filter((p) => p.status === 'active')
  const risky = active.filter((p) => p.at_risk)
  const over = data.people.filter((p) => p.over_allocated)
  const free = data.people.filter((p) => p.hours_this_week === 0 && p.role !== 'admin')
  const t = data.portfolio.totals
  const usedPct = t.planned_budget > 0 ? Math.round((100 * t.actual_cost) / t.planned_budget) : null
  const soon = [...active].sort((a, b) => a.end_date.localeCompare(b.end_date)).slice(0, 5)

  return (
    <>
      <PageHeader
        title={`Hello, ${user.name.split(' ')[0]}`}
        subtitle={user.role === 'admin' ? 'Health of every active project at ACME.' : 'Health of the projects you manage.'}
        actions={<Link className="btn btn-primary" to="/projects?new=1">New project</Link>}
      />
      <div className="stats">
        <Stat label="Active projects" value={active.length} />
        <Stat label="Need attention" value={risky.length} tone={risky.length ? 'warn' : undefined} sub={risky.length ? 'Late, behind, or over budget' : 'Nothing at risk'} />
        <Stat label="Over 40 h/week" value={over.length} tone={over.length ? 'bad' : undefined} sub={`${free.length} with no assignments`} />
        <Stat label="Budget used" value={usedPct === null ? '–' : `${usedPct}%`} sub={`${fmtMoney(t.actual_cost)} of ${fmtMoney(t.planned_budget)}`} />
      </div>

      <div className="grid-2">
        <Panel title="Needs attention" actions={<Link to="/projects?at_risk=true" className="link">See all</Link>}>
          {risky.length ? (
            <div className="plist">{risky.slice(0, 5).map((p) => <ProjectRow key={p.id} project={p} showBudget={false} />)}</div>
          ) : (
            <Empty title="Every project is on track">Projects show up here when they fall behind schedule, run late, or overspend.</Empty>
          )}
        </Panel>
        <Panel title="Next deadlines">
          {soon.length ? (
            <ul className="deadlines">
              {soon.map((p) => (
                <li key={p.id}>
                  <Link to={`/projects/${p.id}`}>{p.name}</Link>
                  <span className="muted">{fmtShort(p.end_date)}</span>
                  <Progress value={p.progress_pct} label="Milestones completed" tone={p.at_risk ? 'warn' : 'good'} />
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="No active projects" action={<Link className="btn btn-ghost" to="/projects?new=1">Create a project</Link>} />
          )}
        </Panel>
      </div>

      <Panel title="Workload this week" actions={<Link to="/allocation" className="link">Open allocation</Link>}>
        {data.people.length ? (
          <div className="workload">
            {data.people.filter((p) => p.role !== 'admin').slice(0, 8).map((p) => (
              <div key={p.id} className="workload-row">
                <span className="workload-name">{p.name}</span>
                <Progress value={(p.hours_this_week / 40) * 100} tone={p.over_allocated ? 'bad' : p.hours_this_week >= 36 ? 'warn' : 'info'} label="Weekly allocation" />
                <span className="num">{p.hours_this_week} / 40 h</span>
              </div>
            ))}
          </div>
        ) : (
          <Empty title="No one to show yet" />
        )}
      </Panel>
    </>
  )
}

function EmployeeHome({ user }) {
  const act = useAction()
  const { data, error, loading, reload } = useApi(async () => {
    const [mine, week, projects] = await Promise.all([
      api.get('/milestones/mine'),
      api.get('/budgets/time/week'),
      api.get('/projects'),
    ])
    return { milestones: mine.milestones, week, projects: projects.projects }
  })

  if (loading) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  const toggle = (task) =>
    act(() => api.patch(`/milestones/tasks/${task.id}`, { completed: !task.completed }), task.completed ? 'Task reopened' : 'Task checked off').then(reload)
  const { week } = data

  return (
    <>
      <PageHeader title={`Hello, ${user.name.split(' ')[0]}`} subtitle="Your milestones, tasks, and hours this week." actions={<Link className="btn btn-primary" to="/time">Log time</Link>} />

      <div className="grid-2">
        <Panel title="This week">
          <div className="week-bars" aria-label="Hours logged per day">
            {week.days.map((d) => (
              <div key={d.date} className="week-day">
                <div className="week-bar"><div className={`week-fill ${d.hours >= 8 ? 'full' : ''}`} style={{ height: `${(d.hours / 8) * 100}%` }} /></div>
                <span className="week-h num">{d.hours || ''}</span>
                <span className="week-label">{fmtWeekday(d.date)}</span>
              </div>
            ))}
          </div>
          <p className="muted">{fmtHours(week.total_hours)} logged, {fmtHours(week.remaining_hours)} left of your 40-hour week.</p>
        </Panel>
        <Panel title="My projects">
          {data.projects.length ? (
            <ul className="deadlines">
              {data.projects.map((p) => (
                <li key={p.id}>
                  <Link to={`/projects/${p.id}`}>{p.name}</Link>
                  <span className="muted">due {fmtShort(p.end_date)}</span>
                  <Progress value={p.progress_pct} label="Milestones completed" tone={p.at_risk ? 'warn' : 'good'} />
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="You are not on a project yet">Your manager will add you to a project team.</Empty>
          )}
        </Panel>
      </div>

      <Panel title="My open milestones">
        {data.milestones.length ? (
          <div className="my-ms">
            {data.milestones.map((m) => (
              <article key={m.id} className="my-ms-item">
                <header>
                  <div>
                    <Link to={`/projects/${m.project_id}?milestone=${m.id}`} className="my-ms-name">{m.name}</Link>
                    <span className="muted"> in {m.project_name}</span>
                  </div>
                  <div className="row-tags">
                    {m.status === 'in_progress' ? <Tag tone="info">In progress</Tag> : <Tag>Not started</Tag>}
                    {m.overdue && <Tag tone="bad">Overdue</Tag>}
                    <span className="muted">due {fmtShort(m.end_date)}</span>
                  </div>
                </header>
                {m.tasks.length ? (
                  <ul className="checklist">
                    {m.tasks.map((t) => (
                      <li key={t.id}>
                        <label className={t.completed ? 'done' : ''}>
                          <input type="checkbox" checked={t.completed} disabled={m.status !== 'in_progress'} onChange={() => toggle(t)} />
                          <span>{t.title}</span>
                          {!t.assigned_to && <span className="muted"> (shared)</span>}
                        </label>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">No tasks assigned to you on this milestone.</p>
                )}
                {m.status !== 'in_progress' && m.tasks.length > 0 && <p className="muted small">Tasks unlock when your manager starts this milestone.</p>}
              </article>
            ))}
          </div>
        ) : (
          <Empty title="No open milestones">When your manager adds you to a milestone, its tasks appear here.</Empty>
        )}
      </Panel>
    </>
  )
}
