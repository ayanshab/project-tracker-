import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Button, Empty, ErrorNote, Loading, PageHeader, Panel, Tag, useApi } from '../components/ui'
import { ROLE_LABEL } from '../utils/format'

// Plain-language names and colors for every recorded action.
const ACTIONS = {
  'auth.login': ['Signed in', 'muted'],
  'auth.login_failed': ['Failed sign-in', 'bad'],
  'auth.login_blocked': ['Blocked sign-in (archived)', 'bad'],
  'auth.password_change': ['Changed password', 'muted'],
  'auth.password_change_failed': ['Failed password change', 'warn'],
  'user.create': ['Added person', 'good'],
  'user.update': ['Edited person', 'info'],
  'user.reassign_manager': ['Changed manager', 'violet'],
  'user.archive': ['Archived person', 'bad'],
  'user.unarchive': ['Restored person', 'good'],
  'user.reset_password': ['Reset password', 'warn'],
  'project.create': ['Created project', 'good'],
  'project.update': ['Edited project', 'info'],
  'project.reassign_manager': ['Changed project manager', 'violet'],
  'project.complete': ['Completed project', 'good'],
  'project.archive': ['Archived project', 'bad'],
  'project.unarchive': ['Restored project', 'good'],
  'project.member_add': ['Assigned to project', 'info'],
  'project.member_update': ['Changed assignment', 'info'],
  'project.member_remove': ['Removed from project', 'warn'],
  'milestone.create': ['Added milestone', 'good'],
  'milestone.update': ['Edited milestone', 'info'],
  'milestone.delete': ['Deleted milestone', 'bad'],
  'milestone.start': ['Started milestone', 'info'],
  'milestone.complete': ['Completed milestone', 'good'],
  'milestone.reopen': ['Reopened milestone', 'warn'],
  'milestone.member_add': ['Added to milestone', 'info'],
  'milestone.member_remove': ['Removed from milestone', 'warn'],
  'task.create': ['Added task', 'muted'],
  'task.update': ['Edited task', 'muted'],
  'task.complete': ['Checked off task', 'good'],
  'task.reopen': ['Unchecked task', 'warn'],
  'task.delete': ['Deleted task', 'warn'],
  'time.log': ['Logged time', 'muted'],
  'time.delete': ['Deleted time entry', 'warn'],
  'cost.add': ['Added cost', 'info'],
  'cost.delete': ['Removed cost', 'warn'],
}

const FIELD = {
  pay_rate: 'pay rate', manager_id: 'manager', job_title: 'job title', start_date: 'start', end_date: 'end',
  budget_hours: 'hour budget', planned_budget: 'budget', hours_per_week: 'hours/week',
  assigned_from: 'from', assigned_to: 'until', assigned_to_name: 'assignee',
}

const label = (a) => ACTIONS[a] || [a, 'muted']
const show = (v) => (v === null || v === undefined || v === '' ? 'none' : typeof v === 'string' && /^[0-9a-f-]{36}$/.test(v) ? 'another person' : String(v))
const stamp = (v) => new Date(v).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })

function describe(e) {
  const d = e.details || {}
  if (d.changes) {
    return Object.entries(d.changes).map(([k, [a, b]]) => `${FIELD[k] || k}: ${show(a)} to ${show(b)}`).join('; ')
  }
  const bits = []
  if (d.user_name) bits.push(d.user_name)
  if (d.title) bits.push(`"${d.title}"`)
  if (d.milestone_name) bits.push(`on ${d.milestone_name}`)
  if (d.project_name && e.entity_type !== 'project') bits.push(`in ${d.project_name}`)
  if (d.hours_per_week) bits.push(`${d.hours_per_week} h/week`)
  if (d.hours && d.work_date) bits.push(`${d.hours} h on ${d.work_date}`)
  if (d.amount) bits.push(`${d.category}, $${Number(d.amount).toLocaleString('en-US')}`)
  if (d.forced) bits.push(`with ${d.open_tasks} open task(s)`)
  if (d.username && e.action === 'auth.login_failed') bits.push(`username "${d.username}"`)
  if (d.role && e.action === 'user.create') bits.push(ROLE_LABEL[d.role])
  if (d.reason) bits.push(d.reason)
  return bits.join(', ')
}

function toCsv(rows) {
  const cols = ['occurred_at', 'actor_name', 'actor_role', 'action', 'entity_type', 'entity_name', 'entity_id', 'summary', 'source_ip', 'details']
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const lines = rows.map((r) => cols.map((c) => esc(c === 'summary' ? describe(r) : c === 'details' ? JSON.stringify(r.details) : r[c])).join(','))
  return [cols.join(','), ...lines].join('\n')
}

export default function AuditLog() {
  const [draft, setDraft] = useState({ q: '', action: '', from: '', to: '' })
  const [filters, setFilters] = useState(draft)
  const filterKey = JSON.stringify(filters)
  const [older, setOlder] = useState({ key: '', pages: [] })
  const [moreBusy, setMoreBusy] = useState(false)
  const { data, error, loading, reload } = useApi(() => api.get('/users/audit', { ...filters, limit: 100 }), [filterKey])

  const pages = older.key === filterKey ? older.pages : []
  const entries = [...(data?.entries || []), ...pages.flatMap((p) => p.entries)]
  const hasMore = pages.length ? pages[pages.length - 1].has_more : data?.has_more

  const loadMore = async () => {
    setMoreBusy(true)
    try {
      const page = await api.get('/users/audit', { ...filters, limit: 100, offset: entries.length })
      setOlder({ key: filterKey, pages: [...pages, page] })
    } finally {
      setMoreBusy(false)
    }
  }

  const exportCsv = () => {
    const blob = new Blob([toCsv(entries)], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }))
  const failed = entries.filter((e) => e.action === 'auth.login_failed').length

  return (
    <>
      <PageHeader
        title="Audit log"
        subtitle="Every change in the tracker: who did it, when, and what changed. Entries cannot be edited or deleted. Only admins can see this page."
        actions={<Button variant="ghost" onClick={exportCsv} disabled={!entries.length}>Export CSV</Button>}
      />
      <form className="toolbar" role="search" onSubmit={(e) => { e.preventDefault(); setFilters(draft) }}>
        <input type="search" placeholder="Search person, project, or detail" value={draft.q} onChange={set('q')} aria-label="Search audit log" />
        <select value={draft.action} onChange={(e) => { const next = { ...draft, action: e.target.value }; setDraft(next); setFilters(next) }} aria-label="Filter by action">
          <option value="">All actions</option>
          <option value="auth.">Sign-ins and passwords</option>
          <option value="user.">People</option>
          <option value="project.">Projects and assignments</option>
          <option value="milestone.">Milestones</option>
          <option value="task.">Tasks</option>
          <option value="time.">Time entries</option>
          <option value="cost.">Costs</option>
          {(data?.actions || []).map((a) => <option key={a} value={a}>{label(a)[0]}</option>)}
        </select>
        <label className="check">From <input type="date" value={draft.from} onChange={set('from')} /></label>
        <label className="check">To <input type="date" value={draft.to} onChange={set('to')} /></label>
        <Button type="submit" variant="ghost">Apply</Button>
      </form>
      {failed > 0 && <div className="row-tags"><Tag tone="bad">{failed} failed sign-in{failed === 1 ? '' : 's'} in this view</Tag></div>}

      {loading && !data ? <Loading /> : error ? <ErrorNote error={error} onRetry={reload} /> : entries.length ? (
        <Panel>
          <div className="table-wrap">
            <table className="table audit">
              <thead><tr><th>When</th><th>Who</th><th>Action</th><th>On</th><th>Details</th><th>From</th></tr></thead>
              <tbody>
                {entries.map((e) => {
                  const [text, tone] = label(e.action)
                  return (
                    <tr key={e.id}>
                      <td className="nowrap num">{stamp(e.occurred_at)}</td>
                      <td>{e.actor_name || <span className="muted">Unknown</span>}{e.actor_role && <div className="muted small">{ROLE_LABEL[e.actor_role]}</div>}</td>
                      <td><Tag tone={tone}>{text}</Tag></td>
                      <td>
                        {e.entity_type === 'project' && e.entity_id
                          ? <Link to={`/projects/${e.entity_id}`}>{e.entity_name || 'Project'}</Link>
                          : e.entity_name || <span className="muted">{e.entity_type}</span>}
                      </td>
                      <td className="audit-detail">{describe(e) || <span className="muted">–</span>}</td>
                      <td className="muted small num">{e.source_ip || '–'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {hasMore && <div className="form-actions"><Button variant="ghost" busy={moreBusy} onClick={loadMore}>Load older entries</Button></div>}
        </Panel>
      ) : (
        <Empty title="No entries match">Try a wider date range or a different action.</Empty>
      )}
    </>
  )
}
