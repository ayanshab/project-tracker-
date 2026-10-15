import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'
import { Button, Empty, ErrorNote, Field, FormModal, Loading, PageHeader, formToObject, useApi, useToast } from '../components/ui'
import ProjectRow from '../components/ProjectRow'
import { TeamPicker, toAssignments } from '../components/Pickers'
import { todayISO } from '../utils/format'

export default function Projects() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(params.get('q') || '')
  const atRisk = params.get('at_risk') === 'true'
  const creating = params.get('new') === '1'
  const canCreate = user.role !== 'employee'

  const { data, error, loading, reload } = useApi(
    () => api.get('/projects', { q: params.get('q'), at_risk: atRisk ? 'true' : undefined }),
    [params.get('q'), atRisk],
  )

  const setParam = (k, v) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next, { replace: true })
  }

  return (
    <>
      <PageHeader
        title="Projects"
        subtitle={user.role === 'employee' ? 'Projects you are assigned to.' : user.role === 'admin' ? 'All active projects.' : 'Projects you manage or work on.'}
        actions={canCreate && <Button onClick={() => setParam('new', '1')}>New project</Button>}
      />
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setParam('q', q.trim()) }} role="search">
        <input type="search" placeholder="Search by project or manager" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search projects" />
        <Button type="submit" variant="ghost">Search</Button>
        <label className="check">
          <input type="checkbox" checked={atRisk} onChange={(e) => setParam('at_risk', e.target.checked ? 'true' : '')} />
          Only projects that need attention
        </label>
      </form>

      {loading ? <Loading /> : error ? <ErrorNote error={error} onRetry={reload} /> : data.projects.length ? (
        <div className="plist">{data.projects.map((p) => <ProjectRow key={p.id} project={p} showBudget={user.role !== 'employee'} />)}</div>
      ) : (
        <Empty
          title={params.get('q') || atRisk ? 'No projects match' : 'No projects yet'}
          action={canCreate && !params.get('q') && !atRisk && <Button onClick={() => setParam('new', '1')}>Create the first project</Button>}
        >
          {params.get('q') || atRisk ? 'Try a different search or clear the filter.' : user.role === 'employee' ? 'Your manager will add you to a project team.' : ''}
        </Empty>
      )}

      {creating && <ProjectForm onClose={() => setParam('new', '')} />}
    </>
  )
}

export function ProjectForm({ project, onClose, onSaved }) {
  const { user } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const editing = Boolean(project)
  const managers = useApi(
    () => (user.role === 'admin' ? api.get('/users').then((d) => d.users.filter((u) => u.role !== 'employee')) : Promise.resolve([])),
    [],
  )

  // Starting team, chosen in the same form (new projects only).
  const [team, setTeam] = useState({})
  const [dates, setDates] = useState({ start: project?.start_date || todayISO(), end: project?.end_date || '' })
  const people = useApi(
    () => (editing ? Promise.resolve([]) : api.get('/projects/allocation').then((d) => d.people.filter((p) => user.role !== 'admin' || p.role !== 'admin'))),
    [],
  )

  const submit = async (fd) => {
    const body = formToObject(fd, ['budget_hours', 'planned_budget'])
    if (!editing) body.members = toAssignments(team)
    if (editing) {
      if (!fd.get('description')) body.description = ''
      const d = await api.patch(`/projects/${project.id}`, body)
      toast(d.pending_approval ? 'Saved. The budget or manager change was sent for approval.' : 'Project saved')
      onSaved?.()
      onClose()
    } else {
      const d = await api.post('/projects', body)
      toast(body.members.length ? `Project created with ${body.members.length} ${body.members.length === 1 ? 'person' : 'people'}` : 'Project created')
      navigate(`/projects/${d.project.id}`)
    }
  }

  return (
    <FormModal title={editing ? 'Edit project' : 'New project'} submitLabel={editing ? 'Save project' : 'Create project'} onSubmit={submit} onClose={onClose}>
      <Field label="Name" wide><input name="name" required maxLength={200} defaultValue={project?.name} /></Field>
      <Field label="Description" wide><textarea name="description" rows={3} maxLength={2000} defaultValue={project?.description || ''} /></Field>
      <Field label="Start date"><input name="start_date" type="date" required value={dates.start} onChange={(e) => setDates((d) => ({ ...d, start: e.target.value }))} /></Field>
      <Field label="End date"><input name="end_date" type="date" required min={dates.start} value={dates.end} onChange={(e) => setDates((d) => ({ ...d, end: e.target.value }))} /></Field>
      <Field label="Budgeted hours" hint="Total effort planned"><input name="budget_hours" type="number" min="0" step="1" defaultValue={project?.budget_hours ?? ''} /></Field>
      <Field label="Planned budget (USD)" hint="Labor plus other costs"><input name="planned_budget" type="number" min="0" step="100" defaultValue={project?.planned_budget ?? ''} /></Field>
      {editing && (
        <Field label="Reason for budget or manager change" wide hint="Needed only when you change the budget, budgeted hours, or project manager. A second admin approves those changes before they apply.">
          <input name="reason" maxLength={1000} placeholder="For example, scope added for two new markets" />
        </Field>
      )}
      {user.role === 'admin' && (
        <Field label="Project manager" wide hint="Each project has exactly one manager.">
          {managers.loading ? <select disabled><option>Loading…</option></select> : (
            <select name="manager_id" required defaultValue={project?.manager_id || ''}>
              <option value="" disabled>Choose a manager</option>
              {(managers.data || []).map((m) => <option key={m.id} value={m.id}>{m.name} ({m.job_title || m.role})</option>)}
            </select>
          )}
        </Field>
      )}
      {!editing && <TeamPicker people={people.data || []} loading={people.loading} value={team} onChange={setTeam} minDate={dates.start} maxDate={dates.end || undefined} />}
    </FormModal>
  )
}
