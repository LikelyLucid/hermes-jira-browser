import * as sdk from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

const {
  Codicon,
  KEYBINDS_AREA,
  PALETTE_AREA,
  STATUSBAR_AREAS,
  TITLEBAR_AREAS,
  atom,
  host,
  useValue
} = sdk
const atomFactory = typeof atom === 'function' ? atom : initial => {
  let value = initial
  const listeners = new Set()
  return {
    get: () => value,
    set: next => { value = next; for (const listener of listeners) listener(value) },
    subscribe: listener => { listeners.add(listener); listener(value); return () => listeners.delete(listener) },
    listen: listener => { listeners.add(listener); return () => listeners.delete(listener) }
  }
}
const useValueSafe = typeof useValue === 'function' ? useValue : value => value.get()
const SessionStatusDot = sdk.SessionStatusDot
import {
  chooseTicketContext,
  eventSessionIdentity,
  markNotified,
  normaliseLiveState,
  notificationTransition,
  shouldNotify,
  statusLabel,
  statusPriority
} from './live_status_core.mjs'

export const LIVE_STATUS_NOTIFICATIONS_KEY = 'live-status-notifications-v1'
const RECEIPTS_KEY = 'live-status-notification-receipts-v1'
const REFRESH_MS = 3000
const EMPTY_FOCUSED = atomFactory(null)

export const liveStatusAtom = atomFactory({
  context: { links: [], selectedKey: '', title: '' },
  entries: [],
  notificationsEnabled: false,
  refreshedAt: 0
})

let pluginContext = null
let controller = null
let contextGeneration = 0
let currentContext = { links: [], selectedKey: '', title: '' }

function safeStorageGet(key, fallback) {
  try {
    return pluginContext?.storage?.get(key, fallback) ?? fallback
  } catch {
    return fallback
  }
}

function safeStorageSet(key, value) {
  try {
    pluginContext?.storage?.set(key, value)
  } catch {
    // Older hosts can expose storage after the plugin is loaded. Presentation
    // keeps working when persistence is unavailable.
  }
}

function notificationsEnabled() {
  return Boolean(safeStorageGet(LIVE_STATUS_NOTIFICATIONS_KEY, false))
}

function setNotificationsEnabled(enabled) {
  safeStorageSet(LIVE_STATUS_NOTIFICATIONS_KEY, Boolean(enabled))
  liveStatusAtom.set({ ...liveStatusAtom.get(), notificationsEnabled: Boolean(enabled) })
}

function readReceipts() {
  const receipts = safeStorageGet(RECEIPTS_KEY, {})
  return receipts && typeof receipts === 'object' ? receipts : {}
}

function writeReceipts(receipts) {
  safeStorageSet(RECEIPTS_KEY, receipts)
}

function routeKey(route) {
  return [route?.connectionId, route?.profile, route?.targetProfile].map(value => String(value || '')).join('::')
}

function validRoute(route) {
  return Boolean(route?.connectionId && route?.profile && route?.targetProfile)
}

function atomDisposer(readable, callback) {
  if (!readable) return () => undefined
  try {
    if (typeof readable.subscribe === 'function') return readable.subscribe(callback)
    if (typeof readable.listen === 'function') return readable.listen(callback)
  } catch {
    return () => undefined
  }
  return () => undefined
}

function linkId(link) {
  return String(link?.session_id || link?.stored_session_id || '').trim()
}

function explicitRoute(link) {
  const route = link?.owner || link?.route || link
  const connectionId = route?.connectionId || route?.connection_id
  const profile = route?.profile || route?.source_profile
  const targetProfile = route?.targetProfile || route?.target_profile
  if (connectionId && profile && targetProfile) {
    return { connectionId, mode: route.mode || 'remote', profile, targetProfile }
  }
  return null
}

async function discoverRoutes() {
  if (typeof host.profileRoutes !== 'function') return []
  try {
    const routes = await host.profileRoutes()
    return Array.isArray(routes) ? routes.filter(validRoute) : []
  } catch {
    return []
  }
}

async function resolveOwners(links, focusedStoredSessionId) {
  const owners = new Map()
  const ambiguous = new Set()
  for (const link of links) {
    const id = linkId(link)
    const route = explicitRoute(link)
    if (!id || !route) continue
    if (owners.has(id) && routeKey(owners.get(id)) !== routeKey(route)) ambiguous.add(id)
    owners.set(id, route)
  }

  const unresolved = links.map(linkId).filter(id => id && !owners.has(id))
  const routes = await discoverRoutes()
  const focusedOwner = host.state?.focusedSessionOwner?.get?.()
  if (focusedStoredSessionId && unresolved.includes(focusedStoredSessionId) && focusedOwner?.connectionId && focusedOwner?.profile) {
    const focusedRoutes = routes.filter(route =>
      route.connectionId === focusedOwner.connectionId && route.profile === focusedOwner.profile
    )
    if (focusedRoutes.length === 1) owners.set(focusedStoredSessionId, focusedRoutes[0])
    else if (focusedRoutes.length > 1) ambiguous.add(focusedStoredSessionId)
  }
  const stillUnresolved = unresolved.filter(id => !owners.has(id) && !ambiguous.has(id))
  if (stillUnresolved.length > 0 && routes.length > 0 && typeof host.listPersistedSessions === 'function') {
    const found = await Promise.all(routes.map(async route => {
      try {
        const result = await host.listPersistedSessions(route, { profile: route.targetProfile, limit: 500 })
        const ids = new Set((Array.isArray(result?.sessions) ? result.sessions : []).flatMap(session => [
          String(session?.id || ''),
          ...(Array.isArray(session?._lineage_ids) ? session._lineage_ids.map(String) : [])
        ]).filter(Boolean))
        return { route, ids }
      } catch {
        return { route, ids: new Set() }
      }
    }))
    for (const id of stillUnresolved) {
      const matches = found.filter(candidate => candidate.ids.has(id)).map(candidate => candidate.route)
      if (matches.length === 1) owners.set(id, matches[0])
      else if (matches.length > 1) ambiguous.add(id)
    }
  }

  return { owners, ambiguous, routes }
}

async function activeRowsForOwners(ownerRoutes, legacyIds) {
  const rows = new Map()
  const busyBySession = host.state?.busyBySession?.get?.() || {}
  if (typeof host.requestProfile === 'function' && ownerRoutes.length > 0) {
    await Promise.all(ownerRoutes.map(async route => {
      try {
        const result = await host.requestProfile(route, 'session.active_list', {})
        for (const row of Array.isArray(result?.sessions) ? result.sessions : []) {
          const storedId = String(row?.session_key || '').trim()
          if (storedId) rows.set(`${routeKey(route)}::${storedId}`, { ...row, owner: route })
        }
      } catch {
        // A route can disappear while a remote profile is reconnecting.
      }
    }))
    return rows
  }

  // Compatibility-only path for pre-routing SDKs. Current SDKs always use the
  // owner-qualified branch above; this never guesses a profile on new hosts.
  if (typeof host.request === 'function' && legacyIds.size > 0) {
    try {
      const result = await host.request('session.active_list', {})
      for (const row of Array.isArray(result?.sessions) ? result.sessions : []) {
        const storedId = String(row?.session_key || '').trim()
        if (legacyIds.has(storedId)) rows.set(`legacy::${storedId}`, {
          ...row,
          busy: row.busy ?? Boolean(busyBySession[String(row?.id || '')])
        })
      }
    } catch {
      // Retain the last snapshot through transient gateway failure.
    }
  }
  return rows
}

function epochFor(entry, previous) {
  return String(entry.active?.started_at || entry.active?.last_active || entry.link?.last_active || previous?.epoch || 'unknown')
}

function displayTicket(snapshot, focusedStoredId) {
  const context = chooseTicketContext(snapshot.context, focusedStoredId)
  const entries = snapshot.entries.filter(entry => {
    if (!context.focusedLink) return true
    return linkId(entry.link) === linkId(context.focusedLink)
  })
  const selected = entries.sort((left, right) => statusPriority(right.state) - statusPriority(left.state))[0]
  return { context, entry: selected || null }
}

function notifyTransition(entry, previous, receipts) {
  const transition = notificationTransition(previous?.state, entry.state, `${entry.ownerKey}:${linkId(entry.link)}:${entry.epoch}`)
  if (!transition || !shouldNotify(receipts, transition)) return receipts
  host.notify({
    kind: transition.kind === 'needs-input' ? 'warning' : 'success',
    message: transition.kind === 'needs-input'
      ? `${entry.ticketLabel || 'Jira ticket'} needs your input.`
      : `${entry.ticketLabel || 'Jira ticket'} completed.`
  })
  return markNotified(receipts, transition)
}

function focusToken() {
  const owner = host.state?.focusedSessionOwner?.get?.()
  return JSON.stringify([
    String(host.state?.focusedStoredSessionId?.get?.() || ''),
    owner?.connectionId || '',
    owner?.profile || ''
  ])
}

async function refresh() {
  if (!controller || !pluginContext) return
  const runGeneration = contextGeneration
  const runFocusToken = focusToken()
  const links = Array.isArray(currentContext.links) ? currentContext.links.filter(link => linkId(link)) : []
  if (links.length === 0) {
    liveStatusAtom.set({ context: currentContext, entries: [], notificationsEnabled: notificationsEnabled(), refreshedAt: Date.now() })
    return
  }
  const focusedStoredId = String(host.state?.focusedStoredSessionId?.get?.() || '').trim()
  const previous = new Map(liveStatusAtom.get().entries.map(entry => [`${entry.ownerKey}::${linkId(entry.link)}`, entry]))
  const { owners, ambiguous } = await resolveOwners(links, focusedStoredId)
  const ownerRoutes = [...new Map([...owners.values()].filter(validRoute).map(route => [routeKey(route), route])).values()]
  const legacyIds = new Set(links.map(linkId).filter(id => !owners.has(id) && !ambiguous.has(id)))
  const activeRows = await activeRowsForOwners(ownerRoutes, legacyIds)
  if (runGeneration !== contextGeneration || runFocusToken !== focusToken()) return
  const receipts = readReceipts()
  let nextReceipts = receipts
  const entries = links.map(link => {
    const id = linkId(link)
    const owner = owners.get(id) || null
    const ownerKey = owner ? routeKey(owner) : 'legacy'
    const active = owner
      ? activeRows.get(`${ownerKey}::${id}`) || null
      : activeRows.get(`legacy::${id}`) || null
    const previousEntry = previous.get(`${ownerKey}::${id}`)
    const state = ambiguous.has(id) ? 'idle' : normaliseLiveState(link, active)
    const entry = {
      active,
      epoch: epochFor({ active, link }, previousEntry),
      link,
      owner,
      ownerKey,
      state,
      ticketLabel: currentContext.title || currentContext.selectedKey || 'Jira ticket'
    }
    if (notificationsEnabled() && !ambiguous.has(id)) nextReceipts = notifyTransition(entry, previousEntry, nextReceipts)
    return entry
  })
  if (nextReceipts !== receipts) writeReceipts(nextReceipts)
  liveStatusAtom.set({
    context: currentContext,
    entries,
    notificationsEnabled: notificationsEnabled(),
    refreshedAt: Date.now()
  })
}

function scheduleRefresh() {
  if (!controller || controller.refreshing) return
  controller.refreshing = true
  const generation = contextGeneration
  const focus = focusToken()
  Promise.resolve(refresh()).catch(() => undefined).finally(() => {
    if (!controller) return
    controller.refreshing = false
    if (generation !== contextGeneration || focus !== focusToken()) scheduleRefresh()
  })
}

function LiveStatusBar() {
  const snapshot = useValueSafe(liveStatusAtom)
  const focusedAtom = host.state?.focusedStoredSessionId || EMPTY_FOCUSED
  const focusedStoredId = useValueSafe(focusedAtom)
  const { context, entry } = displayTicket(snapshot, focusedStoredId)
  const label = context.selectedKey || 'Jira'
  const detail = entry ? statusLabel(entry.state) : context.selectedKey ? 'Idle' : ''
  return jsx('button', {
    'aria-label': `Open Jira${context.selectedKey ? ` ${context.selectedKey}` : ''}`,
    className: 'inline-flex h-full min-w-0 items-center gap-1 rounded-none px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground',
    onClick: () => host.navigate(context.selectedKey ? `/jira?issue=${encodeURIComponent(context.selectedKey)}` : '/jira'),
    title: snapshot.notificationsEnabled ? 'Jira live notifications on' : 'Jira live notifications off',
    type: 'button',
    children: [
      entry?.link && context.focusedLink && typeof SessionStatusDot === 'function'
        ? jsx(SessionStatusDot, { storedSessionId: linkId(entry.link) })
        : jsx(Codicon || 'span', { name: 'issues', size: '0.75rem' }),
      jsx('span', { className: 'max-w-28 truncate', children: label }),
      detail ? jsx('span', { className: 'text-(--ui-text-quaternary)', children: `· ${detail}` }) : null
    ]
  })
}

function LiveTitlebar() {
  const snapshot = useValueSafe(liveStatusAtom)
  const focusedAtom = host.state?.focusedStoredSessionId || EMPTY_FOCUSED
  const focusedStoredId = useValueSafe(focusedAtom)
  const { context, entry } = displayTicket(snapshot, focusedStoredId)
  const label = context.selectedKey ? `${context.selectedKey}${entry ? ` · ${statusLabel(entry.state)}` : ''}` : 'Jira'
  return jsx('button', {
    'aria-label': `Open Jira${context.selectedKey ? ` ${context.selectedKey}` : ''}`,
    className: 'inline-flex h-7 max-w-80 items-center gap-1.5 rounded px-2 text-xs text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground',
    onClick: () => host.navigate(context.selectedKey ? `/jira?issue=${encodeURIComponent(context.selectedKey)}` : '/jira'),
    title: context.title || 'Jira Browser',
    type: 'button',
    children: [jsx(Codicon || 'span', { name: 'issues', size: '0.8rem' }), jsx('span', { className: 'truncate', children: label })]
  })
}

export function publishJiraContext(context = {}) {
  contextGeneration += 1
  currentContext = {
    links: Array.isArray(context.links) ? context.links.map(link => ({ ...link })) : [],
    selectedKey: String(context.selectedKey || '').trim(),
    title: String(context.title || '').trim()
  }
  liveStatusAtom.set({ ...liveStatusAtom.get(), context: currentContext })
  scheduleRefresh()
}

export function subscribeLiveStatuses(callback) {
  return atomDisposer(liveStatusAtom, callback)
}

export function installLiveStatus(ctx) {
  pluginContext = ctx
  if (typeof ctx?.register !== 'function') return () => undefined
  controller = { refreshing: false }
  const disposers = []
  const register = contribution => {
    try {
      const disposer = ctx.register(contribution)
      if (typeof disposer === 'function') disposers.push(disposer)
    } catch {
      // Optional surfaces are independently guarded for older Desktop builds.
    }
  }

  register({
    id: 'live-status',
    area: STATUSBAR_AREAS?.right || 'statusBar.right',
    order: 135,
    render: () => jsx(LiveStatusBar, {})
  })
  register({
    id: 'live-status-titlebar',
    area: TITLEBAR_AREAS?.center || 'titleBar.center',
    order: 120,
    render: () => jsx(LiveTitlebar, {})
  })
  register({
    id: 'toggle-notifications',
    area: PALETTE_AREA || 'palette',
    data: {
      id: 'jira-browser.toggle-live-notifications',
      label: 'Toggle Jira live notifications',
      keywords: ['jira', 'notifications', 'completed', 'needs input'],
      detail: () => (notificationsEnabled() ? 'on' : 'off'),
      detailVariant: 'state',
      keepOpen: true,
      run: () => setNotificationsEnabled(!notificationsEnabled())
    }
  })
  register({
    id: 'open-jira-keybind',
    area: KEYBINDS_AREA || 'keybinds',
    data: {
      id: 'jira-browser.open',
      category: 'navigation',
      defaults: ['mod+shift+j'],
      label: 'Open Jira Browser',
      run: () => host.navigate('/jira')
    }
  })

  const onEvent = typeof host.onEvent === 'function' ? host.onEvent('*', event => {
    const identity = eventSessionIdentity(event)
    if (!identity.storedId && !identity.runtimeId) return
    scheduleRefresh()
  }) : null
  if (typeof onEvent === 'function') disposers.push(onEvent)
  const focusAtom = host.state?.focusedStoredSessionId
  if (focusAtom) disposers.push(atomDisposer(focusAtom, scheduleRefresh))
  const ownerAtom = host.state?.focusedSessionOwner
  if (ownerAtom) disposers.push(atomDisposer(ownerAtom, scheduleRefresh))
  const busyAtom = host.state?.busyBySession
  if (busyAtom) disposers.push(atomDisposer(busyAtom, scheduleRefresh))
  const timer = typeof window !== 'undefined' ? window.setInterval(scheduleRefresh, REFRESH_MS) : null
  scheduleRefresh()

  const dispose = () => {
    if (timer !== null && typeof window !== 'undefined') window.clearInterval(timer)
    for (const disposer of disposers) {
      try { disposer() } catch { /* best effort */ }
    }
    controller = null
    pluginContext = null
    contextGeneration += 1
    liveStatusAtom.set({ context: { links: [], selectedKey: '', title: '' }, entries: [], notificationsEnabled: false, refreshedAt: 0 })
  }
  if (typeof ctx.onDispose === 'function') ctx.onDispose(dispose)
  return dispose
}
