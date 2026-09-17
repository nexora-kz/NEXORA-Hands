import { createClient } from 'npm:@supabase/supabase-js@2'
import { createMcpHandler, McpServer } from 'npm:@modelcontextprotocol/server'
import * as z from 'npm:zod@4'

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
const CONTROL_TOKEN = Deno.env.get('NEXORA_TRANSPORT_TOKEN') || ''

const db = createClient(SUPABASE_URL, SERVICE_ROLE_KEY)

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
      return {
        content: [{ type: 'text', text: JSON.stringify(data ?? []) }],
        structuredContent: { workers: data ?? [] },
      }
    },
  )

  server.registerTool(
    'hands_execute',
    {
      title: 'Execute on a NEXORA Hands PC',
      description: 'Send one supported NEXORA Hands operation to a selected Windows worker. Use read-only operations when inspection is sufficient; write/process operations change the remote PC.',
      inputSchema: {
        worker_id: z.string().min(1),
        command: z.record(z.string(), z.any()),
      },
    },
    async ({ worker_id, command }) => {
      const task_id = `hands-mcp-${crypto.randomUUID()}`
      const { data, error } = await db.rpc('hands_control_command', {
        p_task_id: task_id,
        p_worker_id: worker_id,
        p_command: command,
      })
      if (error) throw new Error(error.message)
      const result = { task_id, worker_id, queued: true, id: data }
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
