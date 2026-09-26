import "jsr:@supabase/functions-js/edge-runtime.d.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"

const CONTROL_TOKEN=Deno.env.get("NEXORA_TRANSPORT_TOKEN")||""
const url=Deno.env.get("SUPABASE_URL")!
const serviceKey=Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
const corsHeaders={"Access-Control-Allow-Origin":"*","Access-Control-Allow-Headers":"content-type","Access-Control-Allow-Methods":"POST,OPTIONS"}

type WorkerName={name?:string,aliases?:string[]}
const workerNames:Record<string,WorkerName>=(()=>{
  try{
    const parsed=JSON.parse(Deno.env.get("NEXORA_HANDS_WORKER_NAMES")||"{}")
    return parsed&&typeof parsed==="object"?parsed:{}
  }catch{return {}}
})()

function automaticWorkerName(workerId:string){
  const compact=String(workerId||"").replace(/^nexora-hands-/i,"").replace(/[^a-z0-9]/gi,"")
  return "NEXORA-PC-"+(compact.slice(-8)||"UNKNOWN").toUpperCase()
}
function decorateWorker(worker:any){
  const id=String(worker?.worker_id||"")
  const cfg=workerNames[id]||{}
  const automatic=automaticWorkerName(id)
  const name=cfg.name||automatic
  const aliases=Array.from(new Set([name,automatic,...(cfg.aliases||[])]))
  return {...worker,name,aliases}
}
function json(data:unknown,status=200){
  return new Response(JSON.stringify(data),{status,headers:{...corsHeaders,"content-type":"application/json","cache-control":"no-store"}})
}

Deno.serve(async(req:Request)=>{
  if(req.method==="OPTIONS")return new Response("ok",{headers:corsHeaders})
  try{
    const token=req.headers.get("x-nexora-control-token")||""
    if(!CONTROL_TOKEN||token!==CONTROL_TOKEN)return json({error:"unauthorized"},401)
    if(req.method!=="POST")return json({error:"POST required"},405)
    const db=createClient(url,serviceKey)
    const body=await req.json().catch(()=>({}))
    const action=String(body?.action||"")
    if(action==="list_workers"){
      const {data,error}=await db.rpc("hands_list_workers")
      if(error)throw error
      return json({ok:true,workers:(data||[]).map(decorateWorker)})
    }
    if(action==="command"){
      const task_id=String(body?.task_id||"")
      const worker_id=String(body?.worker_id||"")
      const command=body?.command
      if(!task_id||!worker_id||!command||typeof command!=="object")return json({error:"task_id, worker_id and command are required"},400)
      const {data,error}=await db.rpc("hands_control_command",{p_task_id:task_id,p_worker_id:worker_id,p_command:command})
      if(error)throw error
      return json({ok:true,task_id,worker_id,result:data})
    }
    if(action==="status"){
      const task_id=String(body?.task_id||"")
      if(!task_id)return json({error:"task_id required"},400)
      const {data,error}=await db.rpc("hands_command_status",{p_task_id:task_id})
      if(error)throw error
      return json({ok:true,task_id,status:data})
    }
    if(action==="execute_wait"){
      const worker_id=String(body?.worker_id||"")
      const command=body?.command
      if(!worker_id||!command||typeof command!=="object")return json({error:"worker_id and command are required"},400)
      const timeout=Math.min(Math.max(Number(body?.timeout_seconds||300),5),600)
      const task_id="cc-"+crypto.randomUUID()
      const commandWithTimeout={...command,timeout_seconds:timeout}
      const {data:queued,error:qerr}=await db.rpc("hands_control_command",{p_task_id:task_id,p_worker_id:worker_id,p_command:commandWithTimeout})
      if(qerr)throw qerr
      const deadline=Date.now()+(timeout+30)*1000
      let latest=null
      while(Date.now()<deadline){
        const {data,error}=await db.rpc("hands_command_status",{p_task_id:task_id})
        if(error)throw error
        latest=data||null
        const s:any=latest||{}
        const state=String(s.status||s.command?.status||"")
        if(state==="completed"||state==="error"||s.result)return json({ok:true,task_id,worker_id,queued,result:latest})
        await new Promise(r=>setTimeout(r,1000))
      }
      return json({ok:false,task_id,worker_id,timeout:true,status:latest},504)
    }
    return json({error:"unsupported action"},400)
  }catch(e){
    return json({error:String((e as any)?.message||e)},500)
  }
})
