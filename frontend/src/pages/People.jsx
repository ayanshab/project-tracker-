import { useState } from 'react'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'
import {
  Avatar, Button, Empty, ErrorNote, Field, FormModal, Loading, Modal, PageHeader, Panel, Tag,
  formToObject, useAction, useApi, useToast,
} from '../components/ui'
import { ROLE_LABEL, fmtMoney } from '../utils/format'

const ROLE_TONE = { admin: 'info', manager: 'violet', employee: 'muted' }

export default function People() {
  const { user } = useAuth()
  if (user.role === 'admin') return <AdminPeople />
  return <MyTeam />
}

function AdminPeople() {
  const act = useAction()
  const [q, setQ] = useState('')
  const [filters, setFilters] = useState({ q: '', role: '', include_archived: '' })
  const [editing, setEditing] = useState(null)
  const [adding, setAdding] = useState(false)
  const [credentials, setCredentials] = useState(null)
  const { data, error, loading, reload } = useApi(() => api.get('/users', filters).then((d) => d.users), [filters])
  const managers = (data || []).filter((u) => u.role !== 'employee' && !u.is_archived)

  const resetPassword = async (u) => {
    if (!window.confirm(`Issue a new temporary password for ${u.name}? Their current password stops working.`)) return
    try {
      const d = await api.post(`/users/${u.id}/reset-password`)
      setCredentials({ name: u.name, username: u.username, password: d.temporary_password })
    } catch (err) {
      act(() => Promise.reject(err))
    }
  }

  return (
    <>
      <PageHeader title="People" subtitle="Everyone at ACME, their role, manager, and pay rate." actions={<Button onClick={() => setAdding(true)}>Add person</Button>} />
      <form className="toolbar" role="search" onSubmit={(e) => { e.preventDefault(); setFilters((f) => ({ ...f, q: q.trim() })) }}>
        <input type="search" placeholder="Search name, email, title, username, or ID" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search people" />
        <select value={filters.role} onChange={(e) => setFilters((f) => ({ ...f, role: e.target.value }))} aria-label="Filter by role">
          <option value="">All roles</option>
          <option value="admin">Admins</option>
          <option value="manager">Managers</option>
          <option value="employee">Employees</option>
        </select>
        <Button type="submit" variant="ghost">Search</Button>
        <label className="check">
          <input type="checkbox" checked={filters.include_archived === 'true'} onChange={(e) => setFilters((f) => ({ ...f, include_archived: e.target.checked ? 'true' : '' }))} />
          Show archived people
        </label>
      </form>

      {loading && !data ? <Loading /> : error ? <ErrorNote error={error} onRetry={reload} /> : data.length ? (
        <Panel>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Employee ID</th><th>Role</th><th>Manager</th><th className="r">Pay rate</th><th /></tr></thead>
              <tbody>
                {data.map((u) => (
                  <tr key={u.id} className={u.is_archived ? 'row-archived' : ''}>
                    <td>
                      <div className="person">
                        <Avatar name={u.name} size={28} />
                        <div>{u.name}<div className="muted small">{u.job_title || 'No title'}, {u.email}</div></div>
                      </div>
                    </td>
                    <td className="num">{u.employee_number}<div className="muted small">{u.username}</div></td>
                    <td><Tag tone={ROLE_TONE[u.role]}>{ROLE_LABEL[u.role]}</Tag>{u.is_archived && <Tag>Archived</Tag>}{!u.is_archived && u.must_change_password && <Tag tone="warn" title="Has not replaced the temporary password yet">Awaiting first sign-in</Tag>}{u.pending_change && <Tag tone="warn" title="A pay, manager, or role change is waiting for approval">Change pending</Tag>}</td>
                    <td>{u.manager_name || <span className="muted">None</span>}</td>
                    <td className="r num">{fmtMoney(u.pay_rate)}/h</td>
                    <td className="r nowrap">
                      {!u.is_archived && <button className="link-btn" onClick={() => setEditing(u)}>Edit</button>}
                      {!u.is_archived && <button className="link-btn" onClick={() => resetPassword(u)}>Reset password</button>}
                      {u.role !== 'admin' && (u.is_archived
                        ? <button className="link-btn" onClick={() => act(() => api.post(`/users/${u.id}/unarchive`), `${u.name} restored`).then(reload)}>Restore</button>
                        : <button className="link-btn danger" onClick={() => window.confirm(`Archive ${u.name}? They will no longer be able to sign in.`) && act(() => api.post(`/users/${u.id}/archive`), `${u.name} archived`).then(reload)}>Archive</button>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : (
        <Empty title="No people match" />
      )}

      {adding && (
        <PersonForm managers={managers} onClose={() => setAdding(false)} onSaved={(d) => {
          setAdding(false)
          setCredentials({ name: d.user.name, username: d.user.username, password: d.temporary_password })
          reload()
        }} />
      )}
      {editing && <PersonForm person={editing} managers={managers.filter((m) => m.id !== editing.id)} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload() }} />}
      {credentials && <CredentialsModal {...credentials} onClose={() => setCredentials(null)} />}
    </>
  )
}

function PersonForm({ person, managers, onClose, onSaved }) {
  const toast = useToast()
  const submit = async (fd) => {
    const body = formToObject(fd, ['pay_rate'])
    if (!fd.get('manager_id')) body.manager_id = null
    if (person) {
      if (!fd.get('job_title')) body.job_title = ''
      const d = await api.patch(`/users/${person.id}`, body)
      if (d.pending_approval) toast('Saved. The pay rate, manager, or role change was sent for approval.')
      onSaved(d)
    } else {
      onSaved(await api.post('/users', body))
    }
  }
  return (
    <FormModal title={person ? `Edit ${person.name}` : 'Add person'} submitLabel={person ? 'Save person' : 'Add person'} onSubmit={submit} onClose={onClose}>
      <Field label="Full name"><input name="name" required maxLength={200} defaultValue={person?.name} /></Field>
      <Field label="Email"><input name="email" type="email" required defaultValue={person?.email} /></Field>
      <Field label="Job title"><input name="job_title" maxLength={200} defaultValue={person?.job_title || ''} /></Field>
      <Field label="Role">
        <select name="role" defaultValue={person?.role || 'employee'}>
          <option value="employee">Employee: views projects, logs time, checks off tasks</option>
          <option value="manager">Manager: runs projects and their team</option>
          <option value="admin">Admin: full control</option>
        </select>
      </Field>
      <Field label="Manager" hint="Each person reports to one manager. Only admins can change this.">
        <select name="manager_id" defaultValue={person?.manager_id || ''}>
          <option value="">No manager</option>
          {managers.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </Field>
      <Field label="Pay rate (USD per hour)" hint="Used to cost project time"><input name="pay_rate" type="number" min="0" step="0.01" defaultValue={person?.pay_rate ?? 0} /></Field>
      {person && (
        <Field label="Reason for pay, manager, or role change" wide hint="Needed only for those changes. Another admin must approve them before they apply.">
          <input name="reason" maxLength={1000} placeholder="For example, annual compensation review" />
        </Field>
      )}
      {!person && <p className="muted small field-wide">A random username and a temporary password are created and shown once after you add the person.</p>}
    </FormModal>
  )
}

function CredentialsModal({ name, username, password, onClose }) {
  const [copied, setCopied] = useState(false)
  const copy = () => navigator.clipboard?.writeText(`Username: ${username}\nTemporary password: ${password}`).then(() => setCopied(true))
  return (
    <Modal title={`Sign-in details for ${name}`} onClose={onClose} footer={<><Button variant="ghost" onClick={copy}>{copied ? 'Copied' : 'Copy details'}</Button><Button onClick={onClose}>Done</Button></>}>
      <p>Share these with {name.split(' ')[0]} privately. The password is shown only once and is not stored in readable form.</p>
      <dl className="creds">
        <dt>Username</dt><dd className="num">{username}</dd>
        <dt>Temporary password</dt><dd className="num">{password}</dd>
      </dl>
      <p className="muted small">They will be asked to choose their own password the first time they sign in.</p>
    </Modal>
  )
}

function MyTeam() {
  const { user } = useAuth()
  const [editing, setEditing] = useState(null)
  const { data, error, loading, reload } = useApi(() => api.get('/users').then((d) => d.users), [])
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  const manager = data.find((u) => u.id === user.manager_id)
  const team = user.role === 'manager'
    ? data.filter((u) => u.manager_id === user.id)
    : data.filter((u) => u.manager_id && u.manager_id === user.manager_id)

  return (
    <>
      <PageHeader
        title="My team"
        subtitle={user.role === 'manager' ? 'Your direct reports. You can update their name and email.' : 'Your manager and the people on your team.'}
      />
      {manager && (
        <Panel title="Manager">
          <PersonCard person={manager} />
        </Panel>
      )}
      <Panel title={user.role === 'manager' ? `Direct reports (${team.length})` : `Team (${team.length})`}>
        {team.length ? (
          <div className="cards">
            {team.map((u) => (
              <PersonCard key={u.id} person={u} me={u.id === user.id}
                action={user.role === 'manager' && <button className="link-btn" onClick={() => setEditing(u)}>Edit name or email</button>} />
            ))}
          </div>
        ) : (
          <Empty title={user.role === 'manager' ? 'No direct reports yet' : 'No teammates yet'}>
            {user.role === 'manager' ? 'An admin assigns people to managers.' : ''}
          </Empty>
        )}
      </Panel>
      {editing && (
        <FormModal title={`Edit ${editing.name}`} submitLabel="Save" onClose={() => setEditing(null)}
          onSubmit={async (fd) => { await api.patch(`/users/${editing.id}`, formToObject(fd)); setEditing(null); reload() }}>
          <Field label="Full name"><input name="name" required defaultValue={editing.name} /></Field>
          <Field label="Email"><input name="email" type="email" required defaultValue={editing.email} /></Field>
        </FormModal>
      )}
    </>
  )
}

function PersonCard({ person, me, action }) {
  return (
    <div className="pcard">
      <Avatar name={person.name} size={40} />
      <div className="pcard-body">
        <div className="pcard-name">{person.name}{me && <span className="muted"> (you)</span>}</div>
        <div className="muted small">{person.job_title || ROLE_LABEL[person.role]}</div>
        <a className="small" href={`mailto:${person.email}`}>{person.email}</a>
        {action && <div>{action}</div>}
      </div>
    </div>
  )
}
