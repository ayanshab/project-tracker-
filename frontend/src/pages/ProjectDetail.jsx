import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'
import {
  Button, Empty, ErrorNote, Field, FormModal, Loading, PageHeader, Panel, Progress, Stat, Tag,
  formToObject, useAction, useApi, useToast,
} from '../components/ui'
import MilestoneLine from '../components/MilestoneLine'
import { RiskTags } from '../components/ProjectRow'
import { ProjectForm } from './Projects'
import Forecast from '../components/Forecast'
import { CapacityNote, MilestonePeoplePicker, TaskRows, TeamPicker, toAssignments } from '../components/Pickers'
import { ChangeList, decidePath } from './Approvals'
import { BUDGET_STATUS, MILESTONE_STATUS, fmtDate, fmtHours, fmtMoney, fmtPct, fmtShort, todayISO } from '../utils/format'

export default function ProjectDetail() {
  const { id } = useParams()
  const { user } = useAuth()
  const navigate = useNavigate()
  const act = useAction()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') || 'milestones'
  const [editing, setEditing] = useState(false)

  const { data, error, loading, reload } = useApi(async () => {
    const [p, ms] = await Promise.all([api.get(`/projects/${id}`), api.get('/milestones', { project_id: id })])
    return { project: p.project, milestones: ms.milestones }
  }, [id])

  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  const { project: p, milestones } = data
  const manage = p.can_manage && p.status === 'active'
  const isAdmin = user.role === 'admin'
  const setTab = (t) => setParams(t === 'milestones' ? {} : { tab: t }, { replace: true })

  const complete = () => {
    if (!window.confirm(`Mark "${p.name}" as completed? It moves to the archive, which only admins can see.`)) return
    act(() => api.post(`/projects/${p.id}/complete`), 'Project completed and archived').then((ok) => ok && navigate(isAdmin ? '/archive' : '/projects'))
  }
  const archive = (on) => act(() => api.post(`/projects/${p.id}/${on ? 'archive' : 'unarchive'}`), on ? 'Project archived' : 'Project restored').then(reload)

  return (
    <>
      <PageHeader
        crumbs={<Link to={p.is_archived ? '/archive' : '/projects'}>{p.is_archived ? 'Archive' : 'Projects'}</Link>}
        title={p.name}
        tags={<RiskTags project={p} />}
        subtitle={p.description}
        meta={`Managed by ${p.manager_name}. Runs ${fmtDate(p.start_date)} to ${fmtDate(p.end_date)}.`}
        actions={(manage || isAdmin) && (
          <>
            {manage && <Button variant="ghost" onClick={() => setEditing(true)}>Edit project</Button>}
            {manage && <Button onClick={complete}>Mark complete</Button>}
            {isAdmin && !p.is_archived && p.status === 'active' && <Button variant="ghost" onClick={() => window.confirm('Archive this project? Its team and history are kept.') && archive(true)}>Archive</Button>}
            {isAdmin && p.is_archived && <Button onClick={() => archive(false)}>Restore from archive</Button>}
          </>
        )}
      />

      <div className="stats">
        <Stat label="Milestones done" value={`${p.milestones_completed} / ${p.milestones_total}`} sub={`${p.time_elapsed_pct}% of the schedule has passed`} />
        <Stat label="Hours logged" value={fmtHours(p.hours_logged)} sub={p.budget_hours ? `of ${fmtHours(p.budget_hours)} budgeted` : 'No hour budget set'} tone={p.hours_used_pct > 100 ? 'bad' : undefined} />
        {p.planned_budget !== undefined && (
          <Stat label="Spent" value={fmtMoney(p.total_cost)} sub={p.planned_budget ? `of ${fmtMoney(p.planned_budget)} (${fmtPct(p.budget_used_pct)})` : 'No budget set'} tone={p.budget_used_pct > 100 ? 'bad' : undefined} />
        )}
        <Stat label="Team" value={p.member_count} sub={p.member_count === 1 ? 'person' : 'people'} />
      </div>

      <div className="tabs" role="tablist">
        {[['milestones', 'Milestones'], ['team', 'Team'], ['forecast', 'Forecast'], ...(p.can_manage ? [['budget', 'Budget']] : [])].map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {p.can_manage && p.pending_changes?.length > 0 && <PendingChanges projectId={p.id} onChanged={reload} />}

      {tab === 'forecast' && <Forecast project={p} />}
      {tab === 'milestones' && <Milestones project={p} milestones={milestones} manage={manage} reload={reload} />}
      {tab === 'team' && <Team project={p} manage={manage} reload={reload} />}
      {tab === 'budget' && p.can_manage && <Budget project={p} manage={manage} />}

      {editing && <ProjectForm project={p} onClose={() => setEditing(false)} onSaved={reload} />}
    </>
  )
}

// ---------- Pending approvals ----------

function PendingChanges({ projectId, onChanged }) {
  const { user } = useAuth()
  const act = useAction()
  const { data, reload } = useApi(() => api.get('/users/approvals', { entity_id: projectId, status: 'pending' }), [projectId])
  const rows = data?.requests || []
  if (!rows.length) return null
  const run = (r, action, msg) => act(() => api.post(decidePath(r, action), {}), msg).then(() => { reload(); onChanged() })
  return (
    <div className="pending-banner" role="status">
      {rows.map((r) => (
        <div key={r.id} className="pending-row">
          <div>
            <strong>Waiting for approval.</strong>{' '}
            <span className="muted">Requested by {String(r.requested_by) === String(user.id) ? 'you' : r.requested_by_name}: "{r.reason}"</span>
            <ChangeList changes={r.changes} />
          </div>
          <div className="pending-actions">
            {r.can_decide && <Button size="sm" onClick={() => run(r, 'approve', 'Change approved and applied')}>Approve</Button>}
            {r.can_decide && <Link className="btn btn-sm btn-ghost" to="/approvals">Review</Link>}
            {r.can_cancel && <Button size="sm" variant="ghost" onClick={() => run(r, 'cancel', 'Request cancelled')}>Cancel request</Button>}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------- Milestones ----------

function Milestones({ project, milestones, manage, reload }) {
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const [adding, setAdding] = useState(false)
  const current = milestones.find((m) => m.status === 'in_progress') || milestones.find((m) => m.status === 'pending') || milestones[milestones.length - 1]
  const selectedId = params.get('milestone') || current?.id
  const select = (m) => { const n = new URLSearchParams(params); n.set('milestone', m.id); setParams(n, { replace: true }) }
  const last = milestones[milestones.length - 1]

  return (
    <>
      <Panel
        title="Plan"
        actions={manage && <Button size="sm" onClick={() => setAdding(true)}>Add milestone</Button>}
        className="panel-line"
      >
        {milestones.length ? (
          <>
            <MilestoneLine milestones={milestones} onSelect={select} selectedId={selectedId} />
            <p className="muted small">Milestones run in order. Each one starts after the previous one is completed, and their dates cannot overlap (a milestone can start on the day the previous one ends).</p>
          </>
        ) : (
          <Empty title="No milestones yet" action={manage && <Button onClick={() => setAdding(true)}>Add the first milestone</Button>}>
            Break the project into milestones, each with its own dates and deliverables.
          </Empty>
        )}
      </Panel>

      {selectedId && milestones.some((m) => m.id === selectedId) && (
        <MilestoneDetail key={selectedId} id={selectedId} project={project} milestones={milestones} manage={manage} onChanged={reload} onSelect={select} />
      )}

      {adding && (
        <MilestoneForm
          project={project}
          milestones={milestones}
          defaults={{ start_date: last ? nextDay(last.end_date) : project.start_date }}
          onClose={() => setAdding(false)}
          onSaved={(m) => { setAdding(false); toast(`Milestone "${m.name}" added`); reload(); select(m) }}
        />
      )}
    </>
  )
}

function nextDay(iso) {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + 1)
  return d.toISOString().slice(0, 10)
}

function MilestoneForm({ project, milestone, milestones = [], defaults = {}, onClose, onSaved }) {
  // New milestones can be staffed and given tasks in the same step.
  const [people, setPeople] = useState([])
  const [tasks, setTasks] = useState([{ title: '', assigned_to: '' }])
  const busy = {}
  milestones.filter((x) => x.status !== 'completed').forEach((x) => x.members.forEach((mem) => { busy[mem.user_id] = x.name }))
  const assignees = project.members.filter((x) => people.includes(x.user_id))

  const submit = async (fd) => {
    const body = formToObject(fd)
    const d = milestone
      ? await api.patch(`/milestones/${milestone.id}`, { ...body, description: fd.get('description') || '' })
      : await api.post('/milestones', {
        ...body,
        project_id: project.id,
        member_ids: people,
        tasks: tasks.filter((t) => t.title.trim()).map((t) => ({
          title: t.title.trim(),
          assigned_to: people.includes(t.assigned_to) ? t.assigned_to : undefined,
        })),
      })
    onSaved(d.milestone)
  }
  const locked = milestone?.status === 'completed'
  return (
    <FormModal title={milestone ? 'Edit milestone' : 'Add milestone'} submitLabel={milestone ? 'Save milestone' : 'Add milestone'} onSubmit={submit} onClose={onClose}>
      <Field label="Name" wide><input name="name" required maxLength={200} defaultValue={milestone?.name} /></Field>
      <Field label="Deliverables" wide hint="What has to be true for this milestone to be done"><textarea name="description" rows={3} defaultValue={milestone?.description || ''} /></Field>
      <Field label="Start date"><input name="start_date" type="date" required disabled={locked} min={project.start_date} max={project.end_date} defaultValue={milestone?.start_date || defaults.start_date} /></Field>
      <Field label="End date"><input name="end_date" type="date" required disabled={locked} min={project.start_date} max={project.end_date} defaultValue={milestone?.end_date} /></Field>
      {!milestone && <MilestonePeoplePicker team={project.members} busy={busy} value={people} onChange={setPeople} />}
      {!milestone && <TaskRows rows={tasks} onChange={setTasks} assignees={assignees} />}
    </FormModal>
  )
}

function MilestoneDetail({ id, project, milestones = [], manage, onChanged, onSelect }) {
  const { user } = useAuth()
  const act = useAction()
  const [editing, setEditing] = useState(false)
  const [addingMember, setAddingMember] = useState(false)
  const { data: m, error, loading, reload } = useApi(() => api.get(`/milestones/${id}`).then((d) => d.milestone), [id])
  if (loading && !m) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  const refresh = () => { reload(); onChanged() }
  const run = (fn, msg) => act(fn, msg).then((ok) => ok && refresh())
  const status = MILESTONE_STATUS[m.status]
  const isMember = m.members.some((x) => x.user_id === user.id)
  const canCheck = (t) => m.status === 'in_progress' && (manage || t.assigned_to === user.id || (!t.assigned_to && isMember))

  const complete = async () => {
    const open = m.tasks_total - m.tasks_completed
    if (open && !window.confirm(`${open} task(s) are still open. Complete the milestone anyway?`)) return
    const ok = await act(() => api.post(`/milestones/${m.id}/complete`, open ? { force: true } : {}), 'Milestone completed')
    if (!ok) return
    // Save a step: offer to start the next milestone right away.
    const next = milestones.find((x) => x.position === m.position + 1 && x.status === 'pending')
    if (next && window.confirm(`Start milestone ${next.position}, "${next.name}", now?`)) {
      await act(() => api.post(`/milestones/${next.id}/start`), `Milestone "${next.name}" started`)
      onSelect?.(next)
    }
    refresh()
  }

  return (
    <Panel
      className="ms-detail"
      title={`Milestone ${m.position}: ${m.name}`}
      actions={manage && (
        <>
          {m.status === 'pending' && <Button size="sm" disabled={!m.can_start} title={m.can_start ? '' : 'The previous milestone must be completed first'} onClick={() => run(() => api.post(`/milestones/${m.id}/start`), 'Milestone started')}>Start milestone</Button>}
          {m.status === 'in_progress' && <Button size="sm" onClick={complete}>Complete milestone</Button>}
          {m.status === 'completed' && <Button size="sm" variant="ghost" onClick={() => run(() => api.post(`/milestones/${m.id}/reopen`), 'Milestone reopened')}>Reopen</Button>}
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
          {m.status === 'pending' && <Button size="sm" variant="danger" onClick={() => window.confirm('Delete this milestone and its tasks?') && run(() => api.del(`/milestones/${m.id}`), 'Milestone deleted')}>Delete</Button>}
        </>
      )}
    >
      <div className="row-tags">
        <Tag tone={status.tone}>{status.label}</Tag>
        {m.overdue && <Tag tone="bad">Overdue</Tag>}
        {m.blocked_by_previous && <Tag tone="warn">Waiting on milestone {m.position - 1}</Tag>}
        <span className="muted">{fmtShort(m.start_date)} to {fmtShort(m.end_date)}, {fmtHours(m.hours_logged)} logged</span>
      </div>
      {m.description && <p className="ms-desc">{m.description}</p>}

      <div className="ms-cols">
        <div>
          <div className="sub-head">
            <h3>Tasks <span className="muted">{m.tasks_completed} of {m.tasks_total} done</span></h3>
          </div>
          <Progress value={m.progress_pct} tone="good" label="Tasks completed" />
          {m.tasks.length ? (
            <ul className="checklist">
              {m.tasks.map((t) => (
                <TaskItem key={t.id} task={t} milestone={m} canCheck={canCheck(t)} editable={manage && m.status !== 'completed'} run={run} />
              ))}
            </ul>
          ) : <p className="muted">No tasks yet.</p>}
          {m.status === 'pending' && m.tasks.length > 0 && <p className="muted small">Tasks can be checked off once the milestone starts.</p>}
          {manage && m.status !== 'completed' && <AddTask milestone={m} onAdded={refresh} />}
        </div>

        <div>
          <div className="sub-head">
            <h3>People on this milestone</h3>
            {manage && m.status !== 'completed' && <Button size="sm" variant="ghost" onClick={() => setAddingMember(true)}>Add person</Button>}
          </div>
          {m.members.length ? (
            <ul className="people">
              {m.members.map((x) => (
                <li key={x.user_id}>
                  <span>{x.name}<span className="muted small"> {x.job_title}</span></span>
                  {manage && m.status !== 'completed' && <button className="link-btn" onClick={() => run(() => api.del(`/milestones/${m.id}/members/${x.user_id}`), 'Removed from milestone')}>Remove</button>}
                </li>
              ))}
            </ul>
          ) : <p className="muted">No one assigned yet. Each person works on one open milestone per project at a time.</p>}
        </div>
      </div>

      {editing && <MilestoneForm project={project} milestone={m} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); refresh() }} />}
      {addingMember && <AddMilestoneMember project={project} milestone={m} milestones={milestones} onClose={() => setAddingMember(false)} onSaved={() => { setAddingMember(false); refresh() }} />}
    </Panel>
  )
}

function TaskItem({ task: t, milestone, canCheck, editable, run }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(t.title)
  const [assignee, setAssignee] = useState(t.assigned_to || '')
  const save = async (e) => {
    e.preventDefault()
    if (!title.trim()) return
    const ok = await run(() => api.patch(`/milestones/tasks/${t.id}`, { title: title.trim(), assigned_to: assignee || null }), 'Task updated')
    if (ok !== false) setEditing(false)
  }
  if (editing) {
    return (
      <li className="task-editing">
        <form className="inline-form" onSubmit={save}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Task title" maxLength={200} autoFocus
            onKeyDown={(e) => e.key === 'Escape' && setEditing(false)} />
          <select value={assignee} onChange={(e) => setAssignee(e.target.value)} aria-label="Assign to">
            <option value="">Anyone on the milestone</option>
            {milestone.members.map((x) => <option key={x.user_id} value={x.user_id}>{x.name}</option>)}
          </select>
          <Button size="sm" type="submit">Save</Button>
          <Button size="sm" variant="ghost" type="button" onClick={() => { setEditing(false); setTitle(t.title); setAssignee(t.assigned_to || '') }}>Cancel</Button>
        </form>
      </li>
    )
  }
  return (
    <li>
      <label className={t.completed ? 'done' : ''}>
        <input type="checkbox" checked={t.completed} disabled={!canCheck} onChange={() => run(() => api.patch(`/milestones/tasks/${t.id}`, { completed: !t.completed }))} />
        <span>{t.title}</span>
      </label>
      <span className="muted small">{t.assigned_to_name || 'Anyone on the milestone'}</span>
      {editable && <button className="link-btn" onClick={() => setEditing(true)} aria-label={`Edit task ${t.title}`}>Edit</button>}
      {editable && <button className="icon-btn" aria-label={`Delete task ${t.title}`} onClick={() => window.confirm(`Delete the task "${t.title}"?`) && run(() => api.del(`/milestones/tasks/${t.id}`), 'Task deleted')}>×</button>}
    </li>
  )
}

function AddTask({ milestone, onAdded }) {
  const act = useAction()
  const [title, setTitle] = useState('')
  const [assignee, setAssignee] = useState('')
  const submit = async (e) => {
    e.preventDefault()
    if (!title.trim()) return
    const ok = await act(() => api.post(`/milestones/${milestone.id}/tasks`, { title: title.trim(), assigned_to: assignee || undefined }), 'Task added')
    if (ok) { setTitle(''); onAdded() }
  }
  return (
    <form className="inline-form" onSubmit={submit}>
      <input placeholder="New task" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Task title" maxLength={200} />
      <select value={assignee} onChange={(e) => setAssignee(e.target.value)} aria-label="Assign to">
        <option value="">Anyone on the milestone</option>
        {milestone.members.map((x) => <option key={x.user_id} value={x.user_id}>{x.name}</option>)}
      </select>
      <Button size="sm" type="submit">Add task</Button>
    </form>
  )
}

function AddMilestoneMember({ project, milestone, milestones, onClose, onSaved }) {
  const [chosen, setChosen] = useState([])
  const candidates = project.members.filter((x) => !milestone.members.some((y) => y.user_id === x.user_id))
  const busy = {}
  milestones.filter((x) => x.status !== 'completed' && x.id !== milestone.id)
    .forEach((x) => x.members.forEach((mem) => { busy[mem.user_id] = x.name }))
  const submit = async () => {
    if (!chosen.length) throw new Error('Choose at least one person.')
    await addEach(chosen, (id) => api.post(`/milestones/${milestone.id}/members`, { user_id: id }),
      (id) => project.members.find((x) => x.user_id === id)?.name, onSaved)
  }
  return (
    <FormModal title={`Add people to ${milestone.name}`} submitLabel="Add to milestone" onClose={onClose} onSubmit={submit}>
      {candidates.length
        ? <MilestonePeoplePicker team={candidates} busy={busy} value={chosen} onChange={setChosen} />
        : <p className="muted field-wide">Everyone on the project team is already on this milestone. Add people on the Team tab first.</p>}
    </FormModal>
  )
}

/** Run one request per person; report any that fail by name, keep the ones that worked. */
async function addEach(ids, post, nameOf, onSaved) {
  const failed = []
  for (const id of ids) {
    try { await post(id) } catch (err) { failed.push(`${nameOf(id) || 'Someone'}: ${err.message}`) }
  }
  if (failed.length === ids.length) throw new Error(failed.join(' '))
  if (failed.length) window.alert(`Added ${ids.length - failed.length}. Not added:\n${failed.join('\n')}`)
  onSaved()
}

// ---------- Team ----------

function Team({ project, manage, reload }) {
  const act = useAction()
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState(null)
  const members = project.members

  return (
    <Panel title="Project team" actions={manage && <Button size="sm" onClick={() => setAdding(true)}>Add person</Button>}>
      {members.length ? (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Name</th><th>Role</th><th className="r">Hours / week</th><th>Assigned</th><th className="r">Logged</th>{manage && <th />}</tr></thead>
            <tbody>
              {members.map((x) => (
                <tr key={x.user_id}>
                  <td>{x.name}<div className="muted small">{x.other_team ? 'Another team' : x.email}</div></td>
                  <td>{x.job_title || '–'}</td>
                  <td className="r num">{x.hours_per_week}</td>
                  <td>{fmtShort(x.assigned_from)} to {x.assigned_to ? fmtShort(x.assigned_to) : 'project end'}</td>
                  <td className="r num">{x.hours_logged === undefined ? '–' : fmtHours(x.hours_logged)}</td>
                  {manage && (
                    <td className="r nowrap">
                      <button className="link-btn" onClick={() => setEditing(x)}>Change hours or dates</button>
                      <button className="link-btn danger" onClick={() => window.confirm(`Remove ${x.name} from the project?`) && act(() => api.del(`/projects/${project.id}/members/${x.user_id}`), 'Removed from project').then(reload)}>Remove</button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty title="No one on this project yet" action={manage && <Button onClick={() => setAdding(true)}>Add the first person</Button>}>
          People can work on several projects, up to 40 hours a week in total.
        </Empty>
      )}
      {adding && <AddPeople project={project} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload() }} />}
      {editing && <MemberForm project={project} member={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload() }} />}
    </Panel>
  )
}

function AddPeople({ project, onClose, onSaved }) {
  const { user } = useAuth()
  const [team, setTeam] = useState({})
  const people = useApi(() => api.get('/projects/allocation').then((d) => d.people.filter(
    (u) => (user.role !== 'admin' || u.role !== 'admin') && !project.members.some((x) => x.user_id === u.id))), [])
  const submit = async () => {
    const rows = toAssignments(team)
    if (!rows.length) throw new Error('Choose at least one person.')
    const byId = Object.fromEntries(rows.map((r) => [r.user_id, r]))
    await addEach(rows.map((r) => r.user_id), (id) => api.post(`/projects/${project.id}/members`, byId[id]),
      (id) => people.data?.find((u) => u.id === id)?.name, onSaved)
  }
  return (
    <FormModal title="Add people to the project" submitLabel="Add to project" onClose={onClose} onSubmit={submit}>
      <TeamPicker people={people.data || []} loading={people.loading} value={team} onChange={setTeam} later={false} minDate={project.start_date} maxDate={project.end_date} projectId={project.id} />
    </FormModal>
  )
}

function MemberForm({ project, member, onClose, onSaved }) {
  const people = useApi(() => (member ? Promise.resolve([]) : api.get('/users').then((d) => d.users)), [])
  const options = (people.data || []).filter((u) => u.role !== 'admin' && !project.members.some((x) => x.user_id === u.id))
  const [draft, setDraft] = useState({
    user_id: member?.user_id || '',
    hours_per_week: member?.hours_per_week ?? 8,
    assigned_from: member?.assigned_from || (todayISO() > project.start_date ? todayISO() : project.start_date),
    assigned_to: member?.assigned_to || '',
  })
  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }))
  const submit = async (fd) => {
    const body = formToObject(fd, ['hours_per_week'])
    if (!fd.get('assigned_to')) body.assigned_to = null
    if (member) await api.patch(`/projects/${project.id}/members/${member.user_id}`, body)
    else await api.post(`/projects/${project.id}/members`, body)
    onSaved()
  }
  return (
    <FormModal title={member ? `Change ${member.name}'s assignment` : 'Add person to project'} submitLabel={member ? 'Save assignment' : 'Add to project'} onSubmit={submit} onClose={onClose}>
      {!member && (
        <Field label="Person" wide hint="Managers can add their direct reports. Admins can add anyone.">
          {people.loading ? <select disabled><option>Loading…</option></select> : (
            <select name="user_id" required value={draft.user_id} onChange={set('user_id')}>
              <option value="" disabled>Choose a person</option>
              {options.map((u) => <option key={u.id} value={u.id}>{u.name} ({u.job_title || u.role})</option>)}
            </select>
          )}
        </Field>
      )}
      <Field label="Hours per week" hint="Total across all projects cannot exceed 40.">
        <input name="hours_per_week" type="number" min="0.5" max="40" step="0.5" required value={draft.hours_per_week} onChange={set('hours_per_week')} />
      </Field>
      <Field label="From"><input name="assigned_from" type="date" min={project.start_date} max={project.end_date} value={draft.assigned_from} onChange={set('assigned_from')} /></Field>
      <Field label="Until" hint="Leave empty for the rest of the project"><input name="assigned_to" type="date" min={project.start_date} max={project.end_date} value={draft.assigned_to} onChange={set('assigned_to')} /></Field>
      <CapacityNote userId={draft.user_id} hours={draft.hours_per_week} from={draft.assigned_from} until={draft.assigned_to} projectId={project.id} fallbackFrom={project.start_date} fallbackUntil={project.end_date} />
    </FormModal>
  )
}

// ---------- Budget ----------

function Budget({ project, manage }) {
  const act = useAction()
  const [adding, setAdding] = useState(false)
  const { data, error, loading, reload } = useApi(async () => {
    const [summary, costs] = await Promise.all([
      api.get('/budgets/summary', { project_id: project.id }),
      api.get('/budgets/costs', { project_id: project.id }),
    ])
    return { summary, costs }
  }, [project.id])
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  const { totals: t, by_person, by_milestone, by_week } = data.summary
  const st = BUDGET_STATUS[t.status]
  const maxWeek = Math.max(...by_week.map((w) => Number(w.cumulative_cost)), Number(t.planned_budget), 1)

  return (
    <>
      <Panel title="Budget versus actual">
        <div className="row-tags"><Tag tone={st.tone}>{st.label}</Tag><span className="muted">{t.time_elapsed_pct}% of the schedule has passed and {fmtPct(t.budget_used_pct)} of the budget is spent.</span></div>
        <div className="budget-bar" aria-label="Budget breakdown">
          <div className="bb-labor" style={{ width: `${Math.min((100 * t.labor_cost) / Math.max(t.planned_budget, t.actual_cost, 1), 100)}%` }} />
          <div className="bb-other" style={{ width: `${Math.min((100 * t.other_cost) / Math.max(t.planned_budget, t.actual_cost, 1), 100)}%` }} />
          {t.planned_budget > 0 && t.actual_cost > t.planned_budget && <div className="bb-limit" style={{ left: `${(100 * t.planned_budget) / t.actual_cost}%` }} />}
        </div>
        <div className="legend">
          <span><i className="sw sw-labor" /> Labor {fmtMoney(t.labor_cost)}</span>
          <span><i className="sw sw-other" /> Other costs {fmtMoney(t.other_cost)}</span>
          <span>{t.remaining_budget >= 0 ? `${fmtMoney(t.remaining_budget)} left` : `${fmtMoney(-t.remaining_budget)} over`} of {fmtMoney(t.planned_budget)}</span>
        </div>
        {by_week.length > 1 && (
          <div className="burn" aria-label="Cumulative spend by week">
            {by_week.map((w) => (
              <div key={w.week_start} className="burn-col" title={`Week of ${fmtShort(w.week_start)}: ${fmtMoney(w.cumulative_cost)} spent so far`}>
                <div className="burn-fill" style={{ height: `${(100 * w.cumulative_cost) / maxWeek}%` }} />
              </div>
            ))}
            {t.planned_budget > 0 && <div className="burn-limit" style={{ bottom: `${(100 * t.planned_budget) / maxWeek}%` }}><span>Budget</span></div>}
          </div>
        )}
      </Panel>

      <div className="grid-2">
        <Panel title="Labor by person">
          {by_person.length ? (
            <table className="table">
              <thead><tr><th>Name</th><th className="r">Hours</th><th className="r">Rate</th><th className="r">Cost</th></tr></thead>
              <tbody>{by_person.map((x) => (
                <tr key={x.user_id}><td>{x.name}</td><td className="r num">{x.hours}</td><td className="r num">{fmtMoney(x.pay_rate)}/h</td><td className="r num">{fmtMoney(x.cost)}</td></tr>
              ))}</tbody>
            </table>
          ) : <Empty title="No time logged yet">Labor cost is hours logged times each person's pay rate.</Empty>}
        </Panel>
        <Panel title="Hours by milestone">
          {by_milestone.length ? (
            <table className="table">
              <thead><tr><th>Milestone</th><th className="r">Hours</th><th className="r">Cost</th></tr></thead>
              <tbody>{by_milestone.map((x) => (
                <tr key={x.milestone_id}><td>{x.position}. {x.name}</td><td className="r num">{x.hours}</td><td className="r num">{fmtMoney(x.cost)}</td></tr>
              ))}</tbody>
            </table>
          ) : <Empty title="No milestones yet" />}
        </Panel>
      </div>

      <Panel title="Other costs" actions={manage && <Button size="sm" onClick={() => setAdding(true)}>Add cost</Button>}>
        {data.costs.costs.length ? (
          <table className="table">
            <thead><tr><th>Date</th><th>Category</th><th>Description</th><th className="r">Amount</th>{manage && <th />}</tr></thead>
            <tbody>{data.costs.costs.map((c) => (
              <tr key={c.id}>
                <td>{fmtShort(c.incurred_on)}</td><td><Tag tone="info">{c.category}</Tag></td><td>{c.description || '–'}</td><td className="r num">{fmtMoney(c.amount)}</td>
                {manage && <td className="r"><button className="link-btn danger" onClick={() => act(() => api.del(`/budgets/costs/${c.id}`), 'Cost removed').then(reload)}>Remove</button></td>}
              </tr>
            ))}</tbody>
          </table>
        ) : <Empty title="No other costs">Track technology, infrastructure, software and other spending here.</Empty>}
      </Panel>

      {adding && (
        <FormModal title="Add cost" submitLabel="Add cost" onClose={() => setAdding(false)}
          onSubmit={async (fd) => { await api.post('/budgets/costs', { ...formToObject(fd, ['amount']), project_id: project.id }); setAdding(false); reload() }}>
          <Field label="Category">
            <select name="category" required defaultValue="technology">
              {data.costs.categories.map((c) => <option key={c} value={c}>{c[0].toUpperCase() + c.slice(1)}</option>)}
            </select>
          </Field>
          <Field label="Amount (USD)"><input name="amount" type="number" min="0.01" step="0.01" required /></Field>
          <Field label="Date"><input name="incurred_on" type="date" defaultValue={todayISO()} /></Field>
          <Field label="Description" wide><input name="description" maxLength={500} placeholder="For example, AWS hosting for October" /></Field>
        </FormModal>
      )}
    </>
  )
}
