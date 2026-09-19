import "jsr:@supabase/functions-js/edge-runtime.d.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"

const CONTROL_TOKEN = Deno.env.get("NEXORA_TRANSPORT_TOKEN") || ""
const url = Deno.env.get("SUPABASE_URL")!
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" }
  })
}

function authorized(req: Request) {
  const token = req.headers.get("x-nexora-control-token") || ""
  return !!CONTROL_TOKEN && token === CONTROL_TOKEN
}

Deno.serve(async (req) => {
  try {
    if (!authorized(req)) return json({ error: "unauthorized" }, 401)
    if (req.method !== "POST") return json({ error: "POST required" }, 405)

    const db = createClient(url, serviceKey)
    const body = await req.json().catch(() => ({}))
    const action = String(body?.action || "")

    if (action === "list_workers") {
      const { data, error } = await db.rpc("hands_list_workers")
      if (error) throw error
      return json({ ok: true, workers: data || [] })
    }

    if (action === "command") {
      const task_id = String(body?.task_id || "")
      const worker_id = String(body?.worker_id || "")
      const command = body?.command
      if (!task_id || !worker_id || !command || typeof command !== "object") {
        return json({ error: "task_id, worker_id and command are required" }, 400)
      }
      const { data, error } = await db.rpc("hands_control_command", {
        p_task_id: task_id,
        p_worker_id: worker_id,
        p_command: command
      })
      if (error) throw error
      return json({ ok: true, task_id, worker_id, result: data })
    }

    if (action === "status") {
      const task_id = String(body?.task_id || "")
      if (!task_id) return json({ error: "task_id required" }, 400)
      const { data, error } = await db.rpc("hands_command_status", { p_task_id: task_id })
      if (error) throw error
      return json({ ok: true, task_id, status: data })
    }

    return json({ error: "unsupported action" }, 400)
  } catch (e) {
    return json({ error: String((e as any)?.message || e) }, 500)
  }
})
