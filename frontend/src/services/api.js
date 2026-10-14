// Thin API client for the backend Lambda services.
// Locally VITE_API_URL is the CORS proxy (http://localhost:3001); in the cloud it is the
// CloudFront URL, or empty, in which case requests go to the same origin.

const BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')
const TOKEN_KEY = 'pt_token'

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable: session lasts until reload */
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

let onUnauthorized = () => {}
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn
}

// The cloud database pauses when idle and takes a few seconds to wake. The backend answers 503
// (or CloudFront 504) meanwhile, so wait and retry quietly before showing an error.
const RETRY_STATUSES = new Set([503, 504])
const RETRY_DELAYS_MS = [2000, 4000, 6000]
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function request(method, path, body, params) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await requestOnce(method, path, body, params)
    } catch (err) {
      if (!(err instanceof ApiError) || !RETRY_STATUSES.has(err.status) || attempt >= RETRY_DELAYS_MS.length) throw err
      await sleep(RETRY_DELAYS_MS[attempt])
    }
  }
}

async function requestOnce(method, path, body, params) {
  const url = new URL(`${BASE}/api${path}`, window.location.origin)
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
  })
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let res
  try {
    res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check that the backend is running.')
  }
  let data = {}
  const text = await res.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { error: text }
    }
  }
  if (!res.ok) {
    if (res.status === 401 && path !== '/auth/login') onUnauthorized()
    throw new ApiError(res.status, data.error || data.Message || `Request failed (${res.status})`)
  }
  return data
}

export const api = {
  get: (path, params) => request('GET', path, undefined, params),
  post: (path, body = {}) => request('POST', path, body),
  patch: (path, body) => request('PATCH', path, body),
  del: (path) => request('DELETE', path),
}
