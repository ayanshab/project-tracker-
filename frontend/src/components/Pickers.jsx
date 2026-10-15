/* eslint-disable react-refresh/only-export-components */
// Pickers used inside the "new project" and "new milestone" forms, so a manager can
// staff the work in the same step as creating it instead of a second trip to another tab.
import { fmtHours } from '../utils/format'

/**
 * Choose people and weekly hours for a new project.
 * people: [{id, name, job_title, hours_this_week}]; value: {userId: hours}; onChange(nextValue)
 */
export function TeamPicker({ people, value, onChange, loading, later = true, minDate, maxDate }) {
  const toggle = (p) => {
    const next = { ...value }
    if (p.id in next) delete next[p.id]
    else next[p.id] = { hours: Math.max(Math.min(8, 40 - (p.hours_this_week || 0)), 0.5), from: '', until: '' }
    onChange(next)
  }
  const set = (id, patch) => onChange({ ...value, [id]: { ...value[id], ...patch } })
  const chosen = Object.keys(value).length

  return (
    <fieldset className="picker field-wide">
      <legend className="field-label">{later ? 'Team' : 'People'} <span className="muted">({later ? 'optional, ' : ''}{chosen} chosen)</span></legend>
      {loading ? <p className="muted small">Loading people…</p> : people.length ? (
        <ul className="pick-list">
          {people.map((p) => {
            const on = p.id in value
            const free = Math.max(40 - (p.hours_this_week || 0), 0)
            return (
              <li key={p.id} className={on ? 'on' : ''}>
                <label className="pick-main">
                  <input type="checkbox" checked={on} onChange={() => toggle(p)} />
                  <span>
                    {p.name}
                    <span className="muted small"> {p.job_title || p.role}</span>
                  </span>
                </label>
                <span className={`small ${free <= 0 ? 'text-bad' : 'muted'}`}>{fmtHours(free)} free / week</span>
                {on && (
                  <div className="pick-detail">
                    <label className="pick-hours">
                      <input type="number" min="0.5" max="40" step="0.5" value={value[p.id].hours} onChange={(e) => set(p.id, { hours: e.target.value })} aria-label={`Hours per week for ${p.name}`} />
                      <span className="muted small">h / week</span>
                    </label>
                    <label className="pick-date">
                      <span className="muted small">From</span>
                      <input type="date" min={minDate} max={maxDate} value={value[p.id].from} onChange={(e) => set(p.id, { from: e.target.value })} aria-label={`Start date for ${p.name}`} />
                    </label>
                    <label className="pick-date">
                      <span className="muted small">Until</span>
                      <input type="date" min={value[p.id].from || minDate} max={maxDate} value={value[p.id].until} onChange={(e) => set(p.id, { until: e.target.value })} aria-label={`End date for ${p.name}`} />
                    </label>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      ) : <p className="muted small">No one is available to add yet.</p>}
      <span className="field-hint">Free hours are as of this week. Nobody can go over 40 hours a week across projects. Leave From empty to start today (or at the project start), and Until empty to stay to the end.{later && ' You can change the team later on the Team tab.'}</span>
    </fieldset>
  )
}

/**
 * Choose people for a new milestone from the project team.
 * team: project.members; busy: {userId: 'milestone name'} for people already on another open milestone
 */
export function MilestonePeoplePicker({ team, busy, value, onChange }) {
  const toggle = (id) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id])
  return (
    <fieldset className="picker field-wide">
      <legend className="field-label">People <span className="muted">(optional, {value.length} chosen)</span></legend>
      {team.length ? (
        <ul className="pick-list">
          {team.map((x) => {
            const elsewhere = busy[x.user_id]
            return (
              <li key={x.user_id} className={value.includes(x.user_id) ? 'on' : ''}>
                <label className="pick-main">
                  <input type="checkbox" checked={value.includes(x.user_id)} disabled={Boolean(elsewhere)} onChange={() => toggle(x.user_id)} />
                  <span>{x.name}<span className="muted small"> {x.job_title}</span></span>
                </label>
                {elsewhere && <span className="muted small">On "{elsewhere}"</span>}
              </li>
            )
          })}
        </ul>
      ) : <p className="muted small">No one is on the project team yet. Add people on the Team tab first.</p>}
      <span className="field-hint">Each person can have one open milestone per project.</span>
    </fieldset>
  )
}

/** Editable list of starting tasks: rows of {title, assigned_to}. */
export function TaskRows({ rows, onChange, assignees }) {
  const update = (i, patch) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const remove = (i) => onChange(rows.filter((_, j) => j !== i))
  const add = () => onChange([...rows, { title: '', assigned_to: '' }])
  return (
    <fieldset className="picker field-wide">
      <legend className="field-label">Tasks <span className="muted">(optional)</span></legend>
      <div className="task-rows">
        {rows.map((r, i) => (
          <div key={i} className="task-row">
            <input
              value={r.title}
              placeholder={`Task ${i + 1}`}
              maxLength={200}
              aria-label={`Task ${i + 1} title`}
              onChange={(e) => update(i, { title: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); if (r.title.trim()) add() }
              }}
            />
            <select value={r.assigned_to} onChange={(e) => update(i, { assigned_to: e.target.value })} aria-label={`Task ${i + 1} assignee`}>
              <option value="">Anyone on the milestone</option>
              {assignees.map((a) => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}
            </select>
            <button type="button" className="icon-btn" aria-label={`Remove task ${i + 1}`} onClick={() => remove(i)}>×</button>
          </div>
        ))}
      </div>
      <button type="button" className="link-btn add-row" onClick={add}>+ Add a task</button>
      <span className="field-hint">Press Enter in a task to start the next one.</span>
    </fieldset>
  )
}

/** Turn the picker's value into API assignments. */
export function toAssignments(value) {
  return Object.entries(value).map(([user_id, v]) => ({
    user_id,
    hours_per_week: Number(v.hours),
    ...(v.from ? { assigned_from: v.from } : {}),
    ...(v.until ? { assigned_to: v.until } : {}),
  }))
}
