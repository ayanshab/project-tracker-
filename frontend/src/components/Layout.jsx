import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useMediaQuery } from 'react-responsive'
import { useAuth } from '../context/AuthContext'
import { ROLE_LABEL } from '../utils/format'
import { Avatar, useApi } from './ui'
import { api } from '../services/api'

const NAV = {
  admin: [
    ['/', 'Overview'],
    ['/projects', 'Projects'],
    ['/people', 'People'],
    ['/allocation', 'Allocation'],
    ['/budgets', 'Budgets'],
    ['/approvals', 'Approvals'],
    ['/archive', 'Archive'],
    ['/audit', 'Audit log'],
    ['/my-work', 'My work'],
    ['/time', 'My time'],
  ],
  manager: [
    ['/', 'Overview'],
    ['/my-work', 'My work'],
    ['/projects', 'Projects'],
    ['/people', 'My team'],
    ['/allocation', 'Allocation'],
    ['/budgets', 'Budgets'],
    ['/approvals', 'Approvals'],
    ['/time', 'My time'],
  ],
  employee: [
    ['/', 'My work'],
    ['/projects', 'Projects'],
    ['/time', 'My time'],
    ['/people', 'My team'],
  ],
}

export function Brand() {
  return (
    <div className="brand">
      <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
        <line x1="3" y1="13" x2="23" y2="13" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="5" cy="13" r="3.5" fill="currentColor" />
        <circle cx="13" cy="13" r="3.5" fill="var(--brand-bg, var(--surface))" stroke="currentColor" strokeWidth="2.2" />
        <circle cx="21" cy="13" r="3.5" fill="var(--brand-bg, var(--surface))" stroke="currentColor" strokeWidth="2.2" />
      </svg>
      <span>ACME Tracker</span>
    </div>
  )
}

export default function Layout() {
  const { user, logout } = useAuth()
  const isPhone = useMediaQuery({ maxWidth: 820 })
  const items = NAV[user.role] || NAV.employee
  const { pathname } = useLocation()
  // Admins see how many approvals are waiting for them; refreshed on every page change.
  const inbox = useApi(() => (user.role === 'admin' ? api.get('/users/approvals', { status: 'pending' }) : Promise.resolve(null)), [pathname])
  const waiting = inbox.data?.waiting_for_you || 0

  const links = items.map(([to, label]) => (
    <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
      {label}
      {to === '/approvals' && waiting > 0 && <span className="nav-badge" aria-label={`${waiting} waiting`}>{waiting}</span>}
    </NavLink>
  ))

  if (isPhone) {
    return (
      <div className="shell shell-phone">
        <header className="topbar">
          <Brand />
          <NavLink to="/profile" className="topbar-me" aria-label="Your profile">
            <Avatar name={user.name} size={30} />
          </NavLink>
        </header>
        <main className="content"><Outlet /></main>
        <nav className="tabbar" aria-label="Main">{links}</nav>
      </div>
    )
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand />
        <nav className="side-nav" aria-label="Main">{links}</nav>
        <div className="side-foot">
          <NavLink to="/profile" className={({ isActive }) => `me ${isActive ? 'active' : ''}`}>
            <Avatar name={user.name} />
            <span className="me-text">
              <span className="me-name">{user.name}</span>
              <span className="me-role">{ROLE_LABEL[user.role]}, ID {user.employee_number}</span>
            </span>
          </NavLink>
          <button className="link-btn" onClick={logout}>Sign out</button>
        </div>
      </aside>
      <main className="content"><Outlet /></main>
    </div>
  )
}
