/* eslint-disable react-refresh/only-export-components */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'
import { Button, Empty, ErrorNote, Field, FormModal, Loading, PageHeader, Panel, Tag, useAction, useApi } from '../components/ui'
import { ROLE_LABEL, fmtMoney } from '../utils/format'

const STATUS = {
  pending: ['Waiting for approval', 'warn'],
  approved: ['Approved', 'good'],
  rejected: ['Rejected', 'bad'],
  cancelled: ['Cancelled', 'muted'],
}

const MONEY = new Set(['planned_budget', 'pay_rate'])
const stamp = (v) => (v ? new Date(v).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '')

export function showValue(field, v) {
  if (v === null || v === undefined || v === '') return 'none'
  if (MONEY.has(field)) return field === 'pay_rate' ? `${fmtMoney(v)}/h` : fmtMoney(v)
  if (field === 'budget_hours') return `${Number(v).toLocaleString('en-US')} h`
  if (field === 'role') return ROLE_LABEL[v] || v
  return String(v)
}

export function ChangeList({ changes }) {
  return (
    <ul className="change-list">
      {changes.map((c) => (
        <li key={c.field}>
          <span className="change-label">{c.label[0].toUpperCase() + c.label.slice(1)}</span>
          <span className="change-from">{showValue(c.field, c.from)}</span>
          <span className="change-arrow" aria-hidden="true">to</span>
          <span className="change-to">{showValue(c.field, c.to)}</span>
        </li>
      ))}
    </ul>
  )
}

export const decidePath = (r, action) => `/${r.entity_type === 'project' ? 'projects' : 'users'}/approvals/${r.id}/${action}`

export default function Approvals() {
  const { user } = useAuth()
  const act = useAction()
  const [rejecting, setRejecting] = useState(null)
  const isAdmin = user.role === 'admin'
  const { data, error, loading, reload } = useApi(() => api.get('/users/approvals'), [])

  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  const all = data.requests
  const forMe = all.filter((r) => r.can_decide)
  const mine = all.filter((r) => String(r.requested_by) === String(user.id))
  const history = isAdmin ? all.filter((r) => r.status !== 'pending') : []

  const approve = (r) => act(() => api.post(decidePath(r, 'approve'), {}), 'Change approved and applied').then(reload)
  const cancel = (r) => act(() => api.post(decidePath(r, 'cancel'), {}), 'Request cancelled').then(reload)

  const card = (r, actions) => {
    const [label, tone] = STATUS[r.status]
    return (
      <article key={r.id} className="approval">
        <header className="approval-head">
          <div>
            <span className="approval-what">
              {r.entity_type === 'project' ? <Link to={`/projects/${r.entity_id}`}>{r.entity_name}</Link> : r.entity_name}
            </span>
            <span className="muted"> {r.entity_type === 'project' ? 'project' : 'person'}</span>
          </div>
          <Tag tone={tone}>{label}</Tag>
        </header>
        <ChangeList changes={r.changes} />
        <p className="approval-reason">"{r.reason}"</p>
        <p className="muted small">
          Requested by {r.requested_by_name} ({ROLE_LABEL[r.requested_by_role]}), {stamp(r.requested_at)}
          {r.decided_by_name && `. ${label} by ${r.decided_by_name}, ${stamp(r.decided_at)}`}
          {r.decision_note && `: "${r.decision_note}"`}
        </p>
        {actions && <div className="approval-actions">{actions}</div>}
      </article>
    )
  }

  return (
    <>
      <PageHeader
        title="Approvals"
        subtitle={isAdmin
          ? 'Budget, pay rate, manager, and role changes need a second person to approve them. You can approve requests made by anyone except yourself.'
          : 'Budget and project-manager changes you ask for wait here until an admin approves them.'}
      />

      {isAdmin && (
        <Panel title={`Waiting for you (${forMe.length})`}>
          {forMe.length ? (
            <div className="approvals">
              {forMe.map((r) => card(r, (
                <>
                  <Button size="sm" onClick={() => approve(r)}>Approve</Button>
                  <Button size="sm" variant="danger" onClick={() => setRejecting(r)}>Reject</Button>
                </>
              )))}
            </div>
          ) : <Empty title="Nothing waiting for you">New requests from managers and other admins show up here.</Empty>}
        </Panel>
      )}

      <Panel title="Your requests">
        {mine.length ? (
          <div className="approvals">
            {mine.map((r) => card(r, r.can_cancel && <Button size="sm" variant="ghost" onClick={() => cancel(r)}>Cancel request</Button>))}
          </div>
        ) : <Empty title="You have not asked for any changes">Budget and manager changes you make are sent here for approval.</Empty>}
      </Panel>

      {isAdmin && history.length > 0 && (
        <Panel title="Decided">
          <div className="approvals">{history.filter((r) => String(r.requested_by) !== String(user.id)).slice(0, 30).map((r) => card(r))}</div>
        </Panel>
      )}

      {rejecting && (
        <FormModal title={`Reject change to ${rejecting.entity_name}`} submitLabel="Reject change" onClose={() => setRejecting(null)}
          onSubmit={async (fd) => { await api.post(decidePath(rejecting, 'reject'), { note: fd.get('note') }); setRejecting(null); reload() }}>
          <Field label="Why are you rejecting it?" wide hint="The requester sees this note.">
            <textarea name="note" rows={3} required minLength={3} />
          </Field>
        </FormModal>
      )}
    </>
  )
}
