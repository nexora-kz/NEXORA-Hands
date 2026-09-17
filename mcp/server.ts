import { createClient } from 'npm:@supabase/supabase-js@2'
import { createMcpHandler, McpServer } from 'npm:@modelcontextprotocol/server'
import * as z from 'npm:zod@4'

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
const CONTROL_TOKEN = Deno.env.get('NEXORA_TRANSPORT_TOKEN') || ''

const db = createClient(SUPABASE_URL, SERVICE_ROLE_KEY)


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

function authorized(request: Request) {
  if (!CONTROL_TOKEN) return false
  const bearer = request.headers.get('authorization') || ''
  const token = request.headers.get('x-nexora-token') || ''
  return bearer === `Bearer ${CONTROL_TOKEN}` || token === CONTROL_TOKEN
}

function buildServer() {
  const server = new McpServer(
    { name: 'NEXORA Hands', version: '1.0.0' },
    { capabilities: { tools: {} } },
  )

  server.registerTool(
    'hands_list_workers',
    {
      title: 'List NEXORA Hands workers',
      description: 'List enabled Windows PCs registered with NEXORA Hands.',
      inputSchema: {},
    },
    async () => {
      const { data, error } = await db.rpc('hands_list_workers')
      if (error) throw new Error(error.message)
      const workers = (data ?? []).map(decorateWorker)
      return {
        content: [{ type: 'text', text: JSON.stringify(workers) }],
        structuredContent: { workers },
      }
    },
  )

  server.registerTool(
    'hands_execute',
    {
      title: 'Execute on a NEXORA Hands PC',
      description: 'Send one supported NEXORA Hands operation to a selected Windows worker. worker_id may be a real worker id or a permanent friendly name returned by hands_list_workers.',
      inputSchema: {
        worker_id: z.string().min(1),
        command: z.record(z.string(), z.any()),
      },
    },
    async ({ worker_id, command }) => {
      const listing = await db.rpc('hands_list_workers')
      if (listing.error) throw new Error(listing.error.message)
      const selected = resolveWorker(listing.data ?? [], worker_id)
      const resolved_worker_id = String(selected.worker_id)
      const task_id = `hands-mcp-${crypto.randomUUID()}`
      const { data, error } = await db.rpc('hands_control_command', {
        p_task_id: task_id,
        p_worker_id: resolved_worker_id,
        p_command: command,
      })
      if (error) throw new Error(error.message)
      const result = { task_id, worker_id: resolved_worker_id, worker_name: selected.name, queued: true, id: data }
      return {
        content: [{ type: 'text', text: JSON.stringify(result) }],
        structuredContent: result,
      }
    },
  )

  server.registerTool(
    'hands_command_status',
    {
      title: 'Get NEXORA Hands command status',
      description: 'Get the current status and result of a NEXORA Hands command by task id.',
      inputSchema: {
        task_id: z.string().min(1),
      },
    },
    async ({ task_id }) => {
      const { data, error } = await db.rpc('hands_command_status', { p_task_id: task_id })
      if (error) throw new Error(error.message)
      const result = data ?? null
      return {
        content: [{ type: 'text', text: JSON.stringify(result) }],
        structuredContent: { result },
      }
    },
  )

  return server
}

const mcp = createMcpHandler(buildServer)

export default {
  async fetch(request: Request) {
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: {
          'access-control-allow-origin': '*',
          'access-control-allow-methods': 'GET,POST,DELETE,OPTIONS',
          'access-control-allow-headers': 'authorization,content-type,mcp-session-id,mcp-protocol-version,x-nexora-token',
          'access-control-expose-headers': 'mcp-session-id,mcp-protocol-version',
        },
      })
    }

    if (!authorized(request)) {
      return new Response(JSON.stringify({ error: 'unauthorized' }), {
        status: 401,
        headers: { 'content-type': 'application/json', 'access-control-allow-origin': '*' },
      })
    }

    const response = await mcp.fetch(request)
    const headers = new Headers(response.headers)
    headers.set('access-control-allow-origin', '*')
    headers.set('access-control-expose-headers', 'mcp-session-id,mcp-protocol-version')
    return new Response(response.body, { status: response.status, headers })
  },
}
