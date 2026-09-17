import { createMcpHandler, McpServer } from 'npm:@modelcontextprotocol/server@^2.0.0'
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
  return { ...worker, name, aliases }
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

Deno.serve(pipeline([withOAuthProtectedResource(), withSupabase({ auth: 'user' })], async (_req, { supabase }) => {
  const handler = createMcpHandler(() => {
    const server = new McpServer({ name: 'NEXORA Hands', version: '4.0.0' }, { capabilities: { tools: {} } })
    server.registerTool('hands_list_workers', { title: 'List NEXORA Hands workers', description: 'List enabled Windows PCs registered with NEXORA Hands for the authenticated user.', inputSchema: {} }, async () => {
      const { data, error } = await supabase.rpc('hands_mcp_list_workers')
      if (error) throw new Error(error.message)
      const cutoff = Date.now() - 90_000
      const workers = (data || [])
        .filter((w: any) => w?.enabled !== false && w?.last_seen_at && Date.parse(w.last_seen_at) >= cutoff)
        .map(decorateWorker)
      return { content: [{ type: 'text', text: JSON.stringify(workers) }], structuredContent: { workers } }
    })
    server.registerTool('hands_execute', { title: 'Execute on NEXORA Hands PC', description: 'Execute any operation supported by NEXORA Hands on a selected Windows PC. worker_id may be the real worker id or a permanent friendly name returned by hands_list_workers, for example NEXORA-LAPTOP.', inputSchema: { worker_id: z.string().min(1).describe('Worker ID or permanent NEXORA Hands friendly name'), command: z.record(z.string(), z.any()), timeout_seconds: z.number().int().min(5).max(600).optional() } }, async ({ worker_id, command, timeout_seconds }) => {
      const list = await supabase.rpc('hands_mcp_list_workers')
      if (list.error) throw new Error(list.error.message)
      const cutoff = Date.now() - 90_000
      const available = (list.data || []).filter((w: any) => w?.enabled !== false && w?.last_seen_at && Date.parse(w.last_seen_at) >= cutoff)
      const selected = resolveWorker(available, worker_id)
      const resolved_worker_id = String(selected.worker_id)
      const task_id = `hands-mcp-${crypto.randomUUID()}`
      const { error } = await supabase.rpc('hands_mcp_command', { p_task_id: task_id, p_worker_id: resolved_worker_id, p_command: command })
      if (error) throw new Error(error.message)
      const deadline = Date.now() + (timeout_seconds || 300) * 1000
      let latest = null
      while (Date.now() < deadline) {
        const r = await supabase.rpc('hands_mcp_status', { p_task_id: task_id })
        if (r.error) throw new Error(r.error.message)
        latest = r.data || null
        const state = String(latest?.status || latest?.command?.status || '')
        if (state === 'completed' || state === 'error' || latest?.result) return { content: [{ type: 'text', text: JSON.stringify({ task_id, worker_id: resolved_worker_id, worker_name: selected.name, result: latest }) }], structuredContent: { task_id, worker_id: resolved_worker_id, worker_name: selected.name, result: latest } }
        await new Promise((r) => setTimeout(r, 1000))
      }
      return { content: [{ type: 'text', text: JSON.stringify({ task_id, worker_id: resolved_worker_id, worker_name: selected.name, timeout: true, status: latest }) }], structuredContent: { task_id, worker_id: resolved_worker_id, worker_name: selected.name, timeout: true, status: latest } }
    })
    server.registerTool('hands_command_status', { title: 'Get NEXORA Hands command status', description: 'Get status and result of a NEXORA Hands command.', inputSchema: { task_id: z.string().min(1) } }, async ({ task_id }) => {
      const { data, error } = await supabase.rpc('hands_mcp_status', { p_task_id: task_id })
      if (error) throw new Error(error.message)
      return { content: [{ type: 'text', text: JSON.stringify(data) }], structuredContent: { result: data } }
    })
    return server
  })
  return handler.fetch(_req)
}))
