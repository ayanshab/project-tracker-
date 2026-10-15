import { Link } from 'react-router-dom'
import MilestoneLine from './MilestoneLine'
import { Progress, Tag } from './ui'
import { fmtMoney, fmtShort } from '../utils/format'

const HEALTH = {
  green: { label: 'On track', tone: 'good' },
  yellow: { label: 'Needs attention', tone: 'warn' },
  red: { label: 'At risk', tone: 'bad' },
}
const AREA = { schedule: 'Schedule', budget: 'Budget', milestones: 'Milestones', resources: 'Resources' }

// Health light plus one short tag per reason, so a list row reads at a glance.
export function HealthTags({ project, brief = false }) {
  if (project.status === 'completed') return <Tag tone="good">Completed</Tag>
  if (project.is_archived) return <Tag tone="muted">Archived</Tag>
  const h = HEALTH[project.health] || (project.at_risk ? HEALTH.yellow : HEALTH.green)
  const reasons = project.health_reasons || []
  // One tag per area, coloured by the worst reason in that area.
  const areas = Object.keys(AREA).map((a) => {
    const rs = reasons.filter((r) => r.area === a)
    return rs.length ? { area: a, level: rs.some((r) => r.level === 'red') ? 'red' : 'yellow', text: rs.map((r) => r.text).join('. ') } : null
  }).filter(Boolean)
  return (
    <>
      <Tag tone={h.tone}><i className={`light light-${project.health || 'green'}`} aria-hidden="true" />{h.label}</Tag>
      {!brief && areas.map((r) => <Tag key={r.area} tone={r.level === 'red' ? 'bad' : 'warn'} title={r.text}>{AREA[r.area]}</Tag>)}
    </>
  )
}

// Kept for older callers.
export const RiskTags = HealthTags

// The full list of reasons, grouped by area, with the projected completion date.
export function HealthReasons({ project }) {
  const h = HEALTH[project.health] || HEALTH.green
  const reasons = project.health_reasons || []
  return (
    <div className={`health-box health-${project.health || 'green'}`} role="status">
      <div className="health-head">
        <Tag tone={h.tone}><i className={`light light-${project.health || 'green'}`} aria-hidden="true" />{h.label}</Tag>
        {project.projected_completion && (
          <span className="muted small">
            Projected completion {fmtShort(project.projected_completion)}
            {project.projected_days_late > 0 ? `, ${project.projected_days_late} days after the end date` : ', on time'}
            {project.projected_completion_basis ? ` (based on ${project.projected_completion_basis})` : ''}
          </span>
        )}
      </div>
      {reasons.length > 0 && (
        <ul className="health-list">
          {reasons.map((r, i) => (
            <li key={i}><i className={`light light-${r.level}`} aria-hidden="true" /><strong>{AREA[r.area]}:</strong> {r.text}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function ProjectRow({ project, showBudget = true }) {
  const p = project
  return (
    <Link to={`/projects/${p.id}`} className="prow">
      <div className="prow-main">
        <div className="prow-title">
          <span className="prow-name">{p.name}</span>
          <span className="prow-tags"><HealthTags project={p} /></span>
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
