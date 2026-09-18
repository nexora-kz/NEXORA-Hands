import { createMcpHandler, McpServer } from 'npm:@modelcontextprotocol/server@^2.0.0'
import { createClient } from 'npm:@supabase/supabase-js@^2.57.4'
import { pipeline } from 'npm:@supabase/middleware@^0.5.0'
import { withOAuthProtectedResource, withSupabase } from 'npm:@supabase/server@^1.6.0'
import * as z from 'npm:zod@^4.3.6'

const KNOWN_WORKER_NAMES: Record<string, { name: string; aliases: string[] }> = {
  'nexora-hands-bf69a451a7744955bf7b63639dd3b06e': {
    name: 'NEXORA-LAPTOP',
    aliases: ['LAPTOP-PJ1VRBPC'],
  },
  'nexora-hands-7e8490ee8a03424b9026e4d7f8d45d37': {
    name: 'NEXORA-DESKTOP',
    aliases: ['DESKTOP-PQU4USG'],
  },
}

function automaticWorkerName(workerId: string) {
  const compact = String(workerId || '').replace(/^nexora-hands-/i, '').replace(/[^a-z0-9]/gi, '')
  const suffix = (compact.slice(-8) || 'UNKNOWN').toUpperCase()
  return `NEXORA-PC-${suffix}`
}

function decorateWorker(worker: any) {
  const workerId = String(worker?.worker_id || '')
  const known = KNOWN_WORKER_NAMES[workerId]
  const automatic = automaticWorkerName(workerId)
  const name = known?.name || automatic
  const aliases = Array.from(new Set([name, automatic, ...(known?.aliases || [])]))
  return {
    ...worker,
    name,
    aliases,
    transport_online: true,
    executor_online: null,
    executor_health: 'unknown_until_health_check',
  }
}

function resolveWorker(workers: any[], requested: string) {
  const needle = String(requested || '').trim().toLowerCase()
  if (!needle) throw new Error('worker_id or worker name is required')
  const matches = workers.filter((raw: any) => {
    const w = decorateWorker(raw)
    return [w.worker_id, w.name, ...(w.aliases || [])]
      .filter(Boolean)
      .some((v: any) => String(v).toLowerCase() === needle)
  })
  if (matches.length === 1) return decorateWorker(matches[0])
  if (matches.length > 1) throw new Error(`Worker name "${requested}" is ambiguous`)
  throw new Error(`NEXORA Hands worker not found: ${requested}`)
}

function workerResult(latest: any) {
  return latest?.result?.result || latest?.result || null
}

function normalizeStatus(data: any) {
  const command = data?.command || null
  const resultEnvelope = data?.result || null
  const result = workerResult(data)
  const rawStatus = String(result?.status || command?.status || data?.status || '')
  let stage = rawStatus || 'unknown'
  if (result) stage = rawStatus === 'error' ? 'failed' : (rawStatus === 'completed' ? 'completed' : rawStatus)
  else if (rawStatus === 'error') stage = 'failed'
  else if (rawStatus === 'claimed') stage = 'claimed'
  else if (rawStatus === 'queued') stage = 'queued'
  const timing = result?.timing || {}
  return {
    stage,
    status: rawStatus || null,
    created_at: command?.created_at || resultEnvelope?.created_at || null,
    claimed_at: command?.claimed_at || null,
    started_at: timing?.started_at || null,
    completed_at: timing?.completed_at || command?.completed_at || resultEnvelope?.created_at || null,
    duration_ms: timing?.duration_ms ?? null,
    error: result?.error || command?.error || null,
  }
}

Deno.serve(pipeline([withOAuthProtectedResource(), withSupabase({ auth: 'user' })], async (_req, { supabase }) => {
  const admin = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!,
    { auth: { persistSession: false, autoRefreshToken: false } },
  )

  const handler = createMcpHandler(() => {
    const server = new McpServer({ name: 'NEXORA Hands', version: '4.2.0' }, { capabilities: { tools: {} } })

    async function availableWorkers() {
      const list = await supabase.rpc('hands_mcp_list_workers')
      if (list.error) throw new Error(list.error.message)
      const cutoff = Date.now() - 90_000
      return (list.data || [])
        .filter((w: any) => w?.enabled !== false && w?.last_seen_at && Date.parse(w.last_seen_at) >= cutoff)
    }

    async function selectedWorker(requested: string) {
      return resolveWorker(await availableWorkers(), requested)
    }

    async function authorizedWorkerIds() {
      return new Set((await availableWorkers()).map((w: any) => String(w.worker_id || '')))
    }

    async function requireAuthorizedTask(taskId: string, columns = 'id,task_id,worker_id,status,created_at,claimed_at,completed_at,error,command') {
      const q = await admin
        .from('hands_commands')
        .select(columns)
        .eq('task_id', taskId)
        .maybeSingle()
      if (q.error) throw new Error(q.error.message)
      if (!q.data) throw new Error(`Task not found: ${taskId}`)
      const allowed = await authorizedWorkerIds()
      if (!allowed.has(String((q.data as any).worker_id || ''))) {
        throw new Error('Task is not owned by an authorized NEXORA Hands worker')
      }
      return q.data as any
    }

    async function submitAndWait(workerId: string, command: any, timeoutSeconds = 300) {
      const task_id = `hands-mcp-${crypto.randomUUID()}`
      const { error } = await supabase.rpc('hands_mcp_command', {
        p_task_id: task_id,
        p_worker_id: workerId,
        p_command: command,
      })
      if (error) throw new Error(error.message)
      const deadline = Date.now() + Math.max(5, Math.min(600, timeoutSeconds)) * 1000
      let latest: any = null
      while (Date.now() < deadline) {
        const r = await supabase.rpc('hands_mcp_status', { p_task_id: task_id })
        if (r.error) throw new Error(r.error.message)
        latest = r.data || null
        const state = String(workerResult(latest)?.status || latest?.command?.status || latest?.status || '')
        if (state === 'completed' || state === 'error' || latest?.result) {
          return { task_id, timeout: false, latest, lifecycle: normalizeStatus(latest) }
        }
        await new Promise((resolve) => setTimeout(resolve, 1000))
      }
      return { task_id, timeout: true, latest, lifecycle: normalizeStatus(latest) }
    }

    server.registerTool(
      'hands_list_workers',
      {
        title: 'List NEXORA Hands transports',
        description: 'List Windows PCs whose NEXORA Hands transport heartbeat is online. This proves transport connectivity only; use hands_health for an executor-level health check.',
        inputSchema: {},
      },
      async () => {
        const raw = await availableWorkers()
        const workers = await Promise.all(raw.map(async (row: any) => {
          const worker = decorateWorker(row)
          try {
            const probe = await submitAndWait(String(worker.worker_id), { operation: 'health' }, 15)
            const payload = workerResult(probe.latest)?.payload || null
            return {
              ...worker,
              executor_online: !probe.timeout && Boolean(payload?.executor_online),
              executor_health: payload || (probe.timeout ? 'health_probe_timeout' : 'health_unavailable'),
              ready: !probe.timeout && Boolean(payload?.ready),
              queue_stalled: payload?.queue_stalled ?? null,
              executor_pid: payload?.executor_pid ?? null,
              active_count: payload?.executor_active_count ?? null,
              max_parallel_commands: payload?.max_parallel_commands ?? null,
            }
          } catch (e) {
            return {
              ...worker,
              executor_online: false,
              executor_health: 'health_probe_failed',
              ready: false,
              health_error: String(e),
            }
          }
        }))
        return {
          content: [{ type: 'text', text: JSON.stringify(workers) }],
          structuredContent: { workers },
        }
      },
    )

    server.registerTool(
      'hands_health',
      {
        title: 'Health-check a NEXORA Hands PC',
        description: 'Run a lightweight end-to-end executor probe. Distinguishes transport heartbeat from a working command executor and reports queue/watchdog state.',
        inputSchema: {
          worker_id: z.string().min(1),
          timeout_seconds: z.number().int().min(5).max(60).optional(),
        },
      },
      async ({ worker_id, timeout_seconds }) => {
        const selected = await selectedWorker(worker_id)
        const probe = await submitAndWait(String(selected.worker_id), { operation: 'health' }, timeout_seconds || 15)
        const payload = workerResult(probe.latest)?.payload || null
        const result = {
          worker_id: selected.worker_id,
          worker_name: selected.name,
          transport_online: true,
          executor_online: !probe.timeout && Boolean(payload),
          ready: !probe.timeout && Boolean(payload?.ready),
          probe_timeout: probe.timeout,
          lifecycle: probe.lifecycle,
          health: payload,
        }
        return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
      },
    )

    server.registerTool(
      'hands_capabilities',
      {
        title: 'List NEXORA Hands capabilities',
        description: 'Return the operations supported by the selected Hands executor and runtime limits.',
        inputSchema: { worker_id: z.string().min(1) },
      },
      async ({ worker_id }) => {
        const selected = await selectedWorker(worker_id)
        const probe = await submitAndWait(String(selected.worker_id), { operation: 'get_capabilities' }, 20)
        const result = {
          worker_id: selected.worker_id,
          worker_name: selected.name,
          timeout: probe.timeout,
          capabilities: workerResult(probe.latest)?.payload || null,
        }
        return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
      },
    )

    server.registerTool(
      'hands_execute',
      {
        title: 'Execute on NEXORA Hands PC',
        description: 'Execute any operation supported by NEXORA Hands on a selected Windows PC. Use hands_capabilities when the operation name or parameters are uncertain.',
        inputSchema: {
          worker_id: z.string().min(1).describe('Worker ID or permanent NEXORA Hands friendly name'),
          command: z.record(z.string(), z.any()),
          timeout_seconds: z.number().int().min(5).max(600).optional(),
        },
      },
      async ({ worker_id, command, timeout_seconds }) => {
        const selected = await selectedWorker(worker_id)
        const run = await submitAndWait(String(selected.worker_id), command, timeout_seconds || 300)
        const result = {
          task_id: run.task_id,
          worker_id: selected.worker_id,
          worker_name: selected.name,
          timeout: run.timeout,
          lifecycle: run.lifecycle,
          result: run.latest,
        }
        return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
      },
    )

    server.registerTool(
      'hands_command_status',
      {
        title: 'Get NEXORA Hands command status',
        description: 'Get lifecycle status, timing, and result of a NEXORA Hands command.',
        inputSchema: { task_id: z.string().min(1) },
      },
      async ({ task_id }) => {
        const { data, error } = await supabase.rpc('hands_mcp_status', { p_task_id: task_id })
        if (error) throw new Error(error.message)
        const result = { task_id, lifecycle: normalizeStatus(data), raw: data }
        return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: { result } }
      },
    )

    server.registerTool(
      'hands_queue',
      {
        title: 'Show NEXORA Hands queue',
        description: 'Show queued and claimed commands for one authorized Windows PC.',
        inputSchema: {
          worker_id: z.string().min(1),
          limit: z.number().int().min(1).max(100).optional(),
        },
      },
      async ({ worker_id, limit }) => {
        const selected = await selectedWorker(worker_id)
        const q = await admin
          .from('hands_commands')
          .select('id,task_id,worker_id,status,created_at,claimed_at,completed_at,error')
          .eq('worker_id', String(selected.worker_id))
          .in('status', ['queued', 'claimed'])
          .order('created_at', { ascending: true })
          .limit(limit || 50)
        if (q.error) throw new Error(q.error.message)
        const rows = q.data || []
        const result = { worker_id: selected.worker_id, worker_name: selected.name, count: rows.length, queue: rows }
        return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
      },
    )

    server.registerTool(
      'hands_cancel_task',
      {
        title: 'Cancel a NEXORA Hands task',
        description: 'Cancel a queued or claimed Hands command. A claimed command may already be executing locally; lease fencing prevents a later stale result from being accepted.',
        inputSchema: { task_id: z.string().min(1) },
      },
      async ({ task_id }) => {
        const existing = await requireAuthorizedTask(task_id, 'id,task_id,status,worker_id')
        const status = String(existing.status || '')
        if (!['queued', 'claimed'].includes(status)) {
          const result = { task_id, cancelled: false, status, reason: 'task_not_cancellable' }
          return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
        }
        const updated = await admin
          .from('hands_commands')
          .update({
            status: 'error',
            error: 'cancelled_by_user',
            completed_at: new Date().toISOString(),
          })
          .eq('task_id', task_id)
          .in('status', ['queued', 'claimed'])
          .select('task_id,status,error,completed_at')
          .maybeSingle()
        if (updated.error) throw new Error(updated.error.message)
        const result = { task_id, cancelled: Boolean(updated.data), row: updated.data || null }
        return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
      },
    )

    server.registerTool(
      'hands_retry_task',
      {
        title: 'Retry a NEXORA Hands task',
        description: 'Create a new Hands task using the worker and command from an earlier task. The original task is left unchanged.',
        inputSchema: {
          task_id: z.string().min(1),
          timeout_seconds: z.number().int().min(5).max(600).optional(),
        },
      },
      async ({ task_id, timeout_seconds }) => {
        const existing = await requireAuthorizedTask(task_id, 'task_id,worker_id,command')
        const newRun = await submitAndWait(String(existing.worker_id), existing.command || {}, timeout_seconds || 300)
        const result = {
          original_task_id: task_id,
          task_id: newRun.task_id,
          timeout: newRun.timeout,
          lifecycle: newRun.lifecycle,
          result: newRun.latest,
        }
        return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
      },
    )

    server.registerTool(
      'hands_prune_tasks',
      {
        title: 'Prune old NEXORA Hands tasks',
        description: 'Delete old completed/error command rows for one authorized Windows PC. Active queued/claimed tasks are never deleted.',
        inputSchema: {
          worker_id: z.string().min(1),
          older_than_hours: z.number().int().min(1).max(8760).optional(),
        },
      },
      async ({ worker_id, older_than_hours }) => {
        const selected = await selectedWorker(worker_id)
        const hours = older_than_hours || 168
        const cutoff = new Date(Date.now() - hours * 3600_000).toISOString()
        const deleted = await admin
          .from('hands_commands')
          .delete()
          .eq('worker_id', String(selected.worker_id))
          .in('status', ['completed', 'error'])
          .lt('created_at', cutoff)
          .select('task_id,status')
        if (deleted.error) throw new Error(deleted.error.message)
        const rows = deleted.data || []
        const result = {
          worker_id: selected.worker_id,
          worker_name: selected.name,
          cutoff,
          deleted_count: rows.length,
        }
        return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
      },
    )

    return server
  })
  return handler.fetch(_req)
}))
