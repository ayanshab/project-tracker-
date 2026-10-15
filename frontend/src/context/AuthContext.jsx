import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api, getToken, setToken, setUnauthorizedHandler } from '../services/api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(Boolean(getToken()))

  // Every session starts on the home page. Without this, the hash is left wherever
  // the last person signed out (usually #/profile) and the next sign-in opens there.
  const goHome = () => {
    if (window.location.hash && window.location.hash !== '#/') window.location.hash = '#/'
  }

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
    goHome()
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(logout)
    if (!getToken()) return
    api
      .get('/auth/me')
      .then((d) => setUser(d.user))
      .catch(() => logout())
      .finally(() => setLoading(false))
  }, [logout])

  const login = useCallback(async (username, password) => {
    const d = await api.post('/auth/login', { username, password })
    setToken(d.token)
    goHome()
    setUser(d.user)
    return d.user
  }, [])

  const refresh = useCallback(async () => {
    const d = await api.get('/auth/me')
    setUser(d.user)
  }, [])

  const value = useMemo(() => ({ user, loading, login, logout, refresh }), [user, loading, login, logout, refresh])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  return useContext(AuthContext)
}
