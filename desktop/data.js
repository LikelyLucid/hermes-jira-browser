import { queryClient, useQuery } from '@hermes/plugin-sdk'

export const MAX_BATCH_ISSUES = 50
export const MAX_SNAPSHOTS = 6
export const DEFAULT_QUERY_STALE_TIME_MS = 15_000
const ISSUE_KEY_PATTERN = /^[A-Z][A-Z0-9]+-\\d+$/

function text(value, fallback = '') {
  const result = String(value ?? '').trim()
  return result || fallback
}

export function normaliseJiraScope(scope = {}) {
  const value = {
    origin: text(scope.origin),
    connectionId: text(scope.connectionId),
    profile: text(scope.profile),
    targetProfile: text(scope.targetProfile)
  }
  if (Object.values(value).some(item => !item)) throw new Error('Complete Jira scope is required.')
  return value
}

export function jiraScopeKey(scope = {}) {
  const value = normaliseJiraScope(scope)
  return [
    'jira-browser',
    value.origin,
    value.connectionId,
    value.profile,
    value.targetProfile
  ]
}

export function jiraQueryKey(scope, resource, params = {}) {
  return [...jiraScopeKey(scope), String(resource || ''), params]
}

/**
 * Shared React Query seam for the Desktop plugin. The query key deliberately
 * carries the complete connection/profile/target identity so a remote profile
 * can never display another profile's Jira response.
 */
export function useJiraQuery(scope, resource, queryFn, options = {}) {
  const { params = {}, ...queryOptions } = options
  return useQuery({
    queryKey: jiraQueryKey(scope, resource, params),
    queryFn,
    ...queryOptions
  })
}

export function boundedIssueKeys(issueKeys) {
  const rawKeys = (Array.isArray(issueKeys) ? issueKeys : [])
    .map(key => text(key).toUpperCase())
  if (rawKeys.some(key => !ISSUE_KEY_PATTERN.test(key))) throw new Error('Invalid Jira issue key in batch request.')
  return [...new Set(rawKeys)]
}

function issueKeyChunks(issueKeys) {
  const keys = boundedIssueKeys(issueKeys)
  const chunks = []
  if (keys.length > 0) chunks.push(keys.slice(0, MAX_BATCH_ISSUES))
  for (let index = MAX_BATCH_ISSUES; index < keys.length; index += MAX_BATCH_ISSUES) {
    chunks.push(keys.slice(index, index + MAX_BATCH_ISSUES))
  }
  return chunks
}

export function fetchIssueBatch(rest, scope, issueKeys, options = {}) {
  const scopeValue = normaliseJiraScope(scope)
  const requestedKeys = boundedIssueKeys(issueKeys)
  const includeTransitions = options.includeTransitions !== false
  const includeLinks = options.includeLinks !== false
  const includeDetails = options.includeDetails === true
  const chunks = issueKeyChunks(requestedKeys)
  if (chunks.length === 0) return Promise.resolve({ items: [], bounded: true, max_items: MAX_BATCH_ISSUES })
  return Promise.all(chunks.map(keys => {
    const params = { keys, includeTransitions, includeLinks, includeDetails }
    return queryClient.fetchQuery({
      queryKey: jiraQueryKey(scope, 'issue-batch', params),
      queryFn: () => rest('/issues/batch', {
        method: 'POST',
        timeoutMs: options.timeoutMs || 30_000,
        body: {
          issue_keys: keys,
          include_transitions: includeTransitions,
          include_links: includeLinks,
          include_details: includeDetails,
          connection_id: scopeValue.connectionId,
          profile_name: scopeValue.profile,
          target_profile: scopeValue.targetProfile
        }
      }),
      staleTime: options.staleTime ?? DEFAULT_QUERY_STALE_TIME_MS
    })
  })).then(results => {
    const itemsByKey = new Map()
    for (const result of results) {
      for (const item of Array.isArray(result?.items) ? result.items : []) {
        const key = text(item?.issue?.key || item?.issue_key).toUpperCase()
        if (key && !itemsByKey.has(key)) itemsByKey.set(key, item)
      }
    }
    return {
      items: requestedKeys.map(key => itemsByKey.get(key) || { issue_key: key, error: 'Could not load issue data.' }),
      bounded: true,
      max_items: MAX_BATCH_ISSUES,
      requested_items: requestedKeys.length
    }
  })
}

export function readBoundedSnapshot(storage, key, scope, fallback = {}) {
  const value = storage?.get?.(`${key}:${jiraScopeKey(scope).join(':')}`, fallback)
  return value && typeof value === 'object' ? value : fallback
}

export function writeBoundedSnapshot(storage, key, scope, value, limit = MAX_SNAPSHOTS) {
  if (!storage?.set || !value || typeof value !== 'object') return
  const scopedKey = `${key}:${jiraScopeKey(scope).join(':')}`
  const current = storage.get(scopedKey, {})
  const entries = Object.entries({ ...(current && typeof current === 'object' ? current : {}), ...value })
    .sort((left, right) => Number(right[1]?.storedAt || 0) - Number(left[1]?.storedAt || 0))
    .slice(0, limit)
  storage.set(scopedKey, Object.fromEntries(entries))
}

export function invalidateJiraQueries(scope) {
  return queryClient.invalidateQueries({ queryKey: jiraScopeKey(scope) })
}

export function clearJiraQueryScope(scope) {
  return queryClient.removeQueries({ queryKey: jiraScopeKey(scope) })
}
