import { Link } from 'react-router-dom'
import MilestoneLine from './MilestoneLine'
import { Progress, Tag } from './ui'
import { fmtMoney, fmtShort } from '../utils/format'

export function RiskTags({ project }) {
  if (project.status === 'completed') return <Tag tone="good">Completed</Tag>
  if (project.is_archived) return <Tag tone="muted">Archived</Tag>
  if (!project.at_risk) return <Tag tone="good">On track</Tag>
  return project.risk_reasons.map((r) => (
    <Tag key={r} tone={r.startsWith('Over') || r.startsWith('Past') ? 'bad' : 'warn'}>{r}</Tag>
  ))
}

export default function ProjectRow({ project, showBudget = true }) {
  const p = project
  return (
    <Link to={`/projects/${p.id}`} className="prow">
      <div className="prow-main">
        <div className="prow-title">
          <span className="prow-name">{p.name}</span>
          <span className="prow-tags"><RiskTags project={p} /></span>
        </div>
        <div className="prow-meta">
          <span>Managed by {p.manager_name}</span>
          <span>{fmtShort(p.start_date)} to {fmtShort(p.end_date)}</span>
          <span>{p.member_count} {p.member_count === 1 ? 'person' : 'people'}</span>
        </div>
      </div>
      <div className="prow-line">
        <MilestoneLine milestones={p.milestones} compact />
        <span className="prow-line-label">
          {p.milestones_total ? `${p.milestones_completed} of ${p.milestones_total} milestones` : ''}
        </span>
      </div>
      {showBudget && p.planned_budget !== undefined && (
        <div className="prow-budget">
          <div className="prow-budget-nums">
            <span className="num">{fmtMoney(p.total_cost)}</span>
            <span className="muted"> of {fmtMoney(p.planned_budget)}</span>
          </div>
          <Progress value={p.budget_used_pct ?? 0} tone={p.budget_used_pct > 100 ? 'bad' : p.budget_used_pct > p.time_elapsed_pct + 25 ? 'warn' : 'info'} label="Budget used" />
        </div>
      )}
    </Link>
  )
}
