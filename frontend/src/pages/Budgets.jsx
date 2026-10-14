import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Empty, ErrorNote, Loading, PageHeader, Panel, Progress, Stat, Tag, useApi } from '../components/ui'
import { BUDGET_STATUS, fmtHours, fmtMoney, fmtPct } from '../utils/format'

export default function Budgets() {
  const { data, error, loading, reload } = useApi(() => api.get('/budgets/portfolio'), [])
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />
  const t = data.totals
  const used = t.planned_budget > 0 ? Math.round((100 * t.actual_cost) / t.planned_budget) : null

  return (
    <>
      <PageHeader title="Budgets" subtitle="Planned budget against what has been spent on each active project. Spend includes labor (hours times pay rate) and other costs." />
      <div className="stats">
        <Stat label="Planned" value={fmtMoney(t.planned_budget)} />
        <Stat label="Spent" value={fmtMoney(t.actual_cost)} sub={used === null ? '' : `${used}% of plan`} />
        <Stat label="Hours logged" value={fmtHours(t.hours_logged)} />
      </div>
      <Panel>
        {data.projects.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Project</th><th>Status</th><th className="r">Planned</th><th className="r">Labor</th><th className="r">Other</th><th className="r">Spent</th><th className="w-bar">Budget used vs. time passed</th></tr></thead>
              <tbody>
                {data.projects.map((p) => {
                  const st = BUDGET_STATUS[p.status]
                  return (
                    <tr key={p.id}>
                      <td><Link to={`/projects/${p.id}?tab=budget`}>{p.name}</Link><div className="muted small">{p.manager_name}</div></td>
                      <td><Tag tone={st.tone}>{st.label}</Tag></td>
                      <td className="r num">{fmtMoney(p.planned_budget)}</td>
                      <td className="r num">{fmtMoney(p.labor_cost)}</td>
                      <td className="r num">{fmtMoney(p.other_cost)}</td>
                      <td className="r num">{fmtMoney(p.actual_cost)}</td>
                      <td>
                        <Progress value={p.budget_used_pct ?? 0} tone={st.tone === 'muted' ? 'info' : st.tone} label="Budget used" />
                        <div className="muted small">{fmtPct(p.budget_used_pct)} spent, {p.time_elapsed_pct}% of time passed</div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : <Empty title="No active projects" />}
      </Panel>
    </>
  )
}
