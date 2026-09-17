import { createMcpHandler, McpServer } from 'npm:@modelcontextprotocol/server@^2.0.0'
import { pipeline } from 'npm:@supabase/middleware@^0.5.0'
import { withOAuthProtectedResource, withSupabase } from 'npm:@supabase/server@^1.6.0'
import * as z from 'npm:zod@^4.3.6'

Deno.serve(pipeline([withOAuthProtectedResource(), withSupabase({ auth: 'user' })], async (_req, { supabase }) => {
  const handler = createMcpHandler(() => {
    const server = new McpServer({ name: 'NEXORA Hands', version: '4.0.0' }, { capabilities: { tools: {} } })
    server.registerTool('hands_list_workers', { title: 'List NEXORA Hands workers', description: 'List enabled Windows PCs registered with NEXORA Hands for the authenticated user.', inputSchema: {} }, async () => {
      const { data, error } = await supabase.rpc('hands_mcp_list_workers')
      if (error) throw new Error(error.message)
      const cutoff = Date.now() - 90_000
      const workers = (data || []).filter((w: any) => w?.enabled !== false && w?.last_seen_at && Date.parse(w.last_seen_at) >= cutoff)
      return { content: [{ type: 'text', text: JSON.stringify(workers) }], structuredContent: { workers } }
    })
    server.registerTool('hands_execute', { title: 'Execute on NEXORA Hands PC', description: 'Execute any operation supported by the NEXORA Hands worker on a selected Windows PC.', inputSchema: { worker_id: z.string().min(1), command: z.record(z.string(), z.any()), timeout_seconds: z.number().int().min(5).max(600).optional() } }, async ({ worker_id, command, timeout_seconds }) => {
      const task_id = `hands-mcp-${crypto.randomUUID()}`
      const { error } = await supabase.rpc('hands_mcp_command', { p_task_id: task_id, p_worker_id: worker_id, p_command: command })
      if (error) throw new Error(error.message)
      const deadline = Date.now() + (timeout_seconds || 300) * 1000
      let latest = null
      while (Date.now() < deadline) {
        const r = await supabase.rpc('hands_mcp_status', { p_task_id: task_id })
        if (r.error) throw new Error(r.error.message)
        latest = r.data || null
        const state = String(latest?.status || latest?.command?.status || '')
        if (state === 'completed' || state === 'error' || latest?.result) return { content: [{ type: 'text', text: JSON.stringify({ task_id, worker_id, result: latest }) }], structuredContent: { task_id, worker_id, result: latest } }
        await new Promise((r) => setTimeout(r, 1000))
      }
      return { content: [{ type: 'text', text: JSON.stringify({ task_id, worker_id, timeout: true, status: latest }) }], structuredContent: { task_id, worker_id, timeout: true, status: latest } }
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
