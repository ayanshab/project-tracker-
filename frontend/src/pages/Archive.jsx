import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Button, Empty, ErrorNote, Loading, PageHeader, Panel, Tag, useAction, useApi } from '../components/ui'
import { fmtDate, fmtHours, fmtMoney } from '../utils/format'

export default function Archive() {
  const act = useAction()
  const { data, error, loading, reload } = useApi(() => api.get('/projects', { archived: 'true' }).then((d) => d.projects), [])
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />

  return (
    <>
      <PageHeader title="Archive" subtitle="Completed and archived projects. Projects are never deleted. Only admins can see this list and restore projects." />
      <Panel>
        {data.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Project</th><th>Outcome</th><th>Ran</th><th className="r">Hours</th><th className="r">Spent</th><th /></tr></thead>
              <tbody>
                {data.map((p) => (
                  <tr key={p.id}>
                    <td><Link to={`/projects/${p.id}`}>{p.name}</Link><div className="muted small">{p.manager_name}</div></td>
                    <td>{p.status === 'completed' ? <Tag tone="good">Completed {p.completed_at ? fmtDate(p.completed_at) : ''}</Tag> : <Tag>Archived</Tag>}</td>
                    <td>{fmtDate(p.start_date)} to {fmtDate(p.end_date)}</td>
                    <td className="r num">{fmtHours(p.hours_logged)}</td>
                    <td className="r num">{fmtMoney(p.total_cost)}<div className="muted small">of {fmtMoney(p.planned_budget)}</div></td>
                    <td className="r"><Button size="sm" variant="ghost" onClick={() => act(() => api.post(`/projects/${p.id}/unarchive`), `${p.name} restored`).then(reload)}>Restore</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty title="The archive is empty">Projects land here when a manager marks them complete or an admin archives them.</Empty>}
      </Panel>
    </>
  )
}
