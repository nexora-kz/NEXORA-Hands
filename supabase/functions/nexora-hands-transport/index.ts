const TOKEN = Deno.env.get("NEXORA_TRANSPORT_TOKEN") || "";
Deno.serve((req) => {
  if (!TOKEN || req.headers.get("x-nexora-hands-token") !== TOKEN) {
    return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  }
  return new Response(JSON.stringify({ ok: true, version: 5 }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
});
