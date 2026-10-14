import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Empty, ErrorNote, Loading, PageHeader, Panel, Progress, Stat, Tag, useApi } from '../components/ui'
import { BUDGET_STATUS, fmtHours, fmtMoney, fmtPct, fmtShort } from '../utils/format'

export default function Budgets() {
  const { data, error, loading, reload } = useApi(async () => {
    const [portfolio, list] = await Promise.all([api.get('/budgets/portfolio'), api.get('/projects')])
    const forecast = Object.fromEntries(list.projects.map((p) => [p.id, p.forecast]))
    return { ...portfolio, projects: portfolio.projects.map((p) => ({ ...p, forecast: forecast[p.id] })) }
  }, [])
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />
  const t = data.totals
  const used = t.planned_budget > 0 ? Math.round((100 * t.actual_cost) / t.planned_budget) : null

  return (
    <>
      <PageHeader title="Budgets" subtitle="Planned budget against what has been spent on each active project, and where each is forecast to land. Spend includes labor (hours times pay rate) and other costs." />
      <div className="stats">
        <Stat label="Planned" value={fmtMoney(t.planned_budget)} />
        <Stat label="Spent" value={fmtMoney(t.actual_cost)} sub={used === null ? '' : `${used}% of plan`} />
        <Stat label="Hours logged" value={fmtHours(t.hours_logged)} />
      </div>
      <Panel>
        {data.projects.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Project</th><th>Status</th><th className="r">Planned</th><th className="r">Labor</th><th className="r">Other</th><th className="r">Spent</th><th className="r">Forecast cost</th><th>Forecast finish</th><th className="w-bar">Budget used vs. time passed</th></tr></thead>
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
                      <td className={`r num ${p.forecast?.forecast_variance < 0 ? 'bad' : ''}`}>
                        {p.forecast?.available && p.forecast.forecast_cost !== null ? fmtMoney(p.forecast.forecast_cost) : <span className="muted">–</span>}
                      </td>
                      <td className={p.forecast?.days_late > 7 ? 'bad' : ''}>
                        {p.forecast?.available && p.forecast.forecast_finish ? fmtShort(p.forecast.forecast_finish) : <span className="muted">–</span>}
                        {p.forecast?.days_late > 7 && <div className="small">{p.forecast.days_late} days late</div>}
                      </td>
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
