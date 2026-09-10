import postgres from "postgres";

const DB_URL = Deno.env.get("SUPABASE_DB_URL") ?? "";
const EXPECTED_BOT_USERNAME = "junior_jobs_channel_bot";
const PROTOCOL_V2 = 2;
// SHA-256 of the dedicated high-entropy Render -> Edge bridge key. The raw key
// exists only in Render environment variables and is never committed or sent to Vercel.
const RENDER_BRIDGE_KEY_SHA256 = "cbd26e6af077ca9b8ccb7baae4555ab5a90e2b4a8c824a3df9c5c174e720b213";
const MAX_PRIVATE_SQL_BYTES = 40_000;
const MAX_PRIVATE_SQL_PARAMS = 80;
const sql = postgres(DB_URL, { prepare: false, max: 1, idle_timeout: 20, connect_timeout: 10 });
const telegramAuthCache = new Map<string, number>();

const jsonHeaders = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
};

function clampInt(value: string | null, fallback: number, min: number, max: number): number {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(parsed, max));
}

function jsonSafe(value: unknown): unknown {
  if (typeof value === "bigint") return Number.isSafeInteger(Number(value)) ? Number(value) : value.toString();
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(jsonSafe);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) out[key] = jsonSafe(item);
    return out;
  }
  return value;
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}

async function renderBridgeAuthorized(req: Request): Promise<boolean> {
  const key = (req.headers.get("x-growth-key") ?? "").trim();
  if (!key || key.length > 256 || !RENDER_BRIDGE_KEY_SHA256) return false;
  return constantTimeEqual(await sha256Hex(key), RENDER_BRIDGE_KEY_SHA256);
}

async function telegramBotAuthorized(token: string): Promise<boolean> {
  if (!/^[0-9]{5,20}:[A-Za-z0-9_-]{20,200}$/.test(token)) return false;
  const tokenHash = await sha256Hex(token);
  const now = Date.now();
  if ((telegramAuthCache.get(tokenHash) ?? 0) > now) return true;
  try {
    const response = await fetch(`https://api.telegram.org/bot${token}/getMe`, {
      method: "GET",
      headers: { "Accept": "application/json" },
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return false;
    const body = await response.json() as { ok?: boolean; result?: { is_bot?: boolean; username?: string } };
    const valid = Boolean(
      body.ok && body.result?.is_bot === true && body.result?.username === EXPECTED_BOT_USERNAME
    );
    if (valid) telegramAuthCache.set(tokenHash, now + 5 * 60_000);
    return valid;
  } catch {
    return false;
  }
}

function parseJobHash(value: unknown): string {
  const hash = typeof value === "string" ? value.trim() : "";
  return /^[A-Za-z0-9_-]{1,100}$/.test(hash) ? hash : "";
}

function parseClaimToken(value: unknown): string {
  const token = typeof value === "string" ? value.trim() : "";
  return /^[0-9a-fA-F-]{36}$/.test(token) ? token : "";
}

function validatePrivateGrowthSql(value: unknown): string {
  const query = typeof value === "string" ? value.trim() : "";
  if (!query || new TextEncoder().encode(query).length > MAX_PRIVATE_SQL_BYTES) return "";
  if (/--|\/\*|\*\//.test(query)) return "";
  const withoutTrailingSemicolon = query.replace(/;\s*$/, "");
  if (withoutTrailingSemicolon.includes(";")) return "";

  const normalized = withoutTrailingSemicolon.replace(/\s+/g, " ").trim();
  const lower = normalized.toLowerCase();
  if (!/^(select\b|with\b|insert\b|update\b|delete\b|create table if not exists\b|create index if not exists\b)/i.test(normalized)) return "";

  // Defense in depth for a backend-only bridge key. Even with the key, this
  // endpoint cannot reach Supabase/Auth/system schemas or execute privileged SQL.
  const forbidden = /\b(pg_catalog|information_schema|auth\.|storage\.|vault\.|extensions\.|realtime\.|graphql\.|supabase_|create\s+(extension|function|procedure|schema|role|view)|alter\b|drop\b|truncate\b|grant\b|revoke\b|copy\b|call\b|do\s+\$|prepare\b|execute\b|deallocate\b|listen\b|notify\b|vacuum\b|set_config\b|pg_sleep\b|pg_read_file\b|pg_ls_dir\b|dblink\b|lo_import\b|lo_export\b)/i;
  if (forbidden.test(lower)) return "";

  const ctes = new Set<string>();
  for (const match of normalized.matchAll(/\b([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(/gi)) {
    ctes.add(match[1].toLowerCase());
  }

  const relations: string[] = [];
  const relationPatterns = [
    /\bFROM\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bJOIN\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bINTO\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bUPDATE\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bDELETE\s+FROM\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bCREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+[A-Za-z_][A-Za-z0-9_]*\s+ON\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
  ];
  for (const pattern of relationPatterns) {
    for (const match of normalized.matchAll(pattern)) relations.push(match[1].toLowerCase());
  }
  if (!relations.length) return "";

  for (const relation of relations) {
    const parts = relation.split(".");
    if (parts.length > 2) return "";
    if (parts.length === 2 && parts[0] !== "public") return "";
    const name = parts.at(-1) ?? "";
    if (!name.startsWith("growth_") && !ctes.has(name)) return "";
  }
  return withoutTrailingSemicolon;
}

async function privateGrowthSql(req: Request): Promise<Response> {
  if (!(await renderBridgeAuthorized(req))) {
    return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401, headers: jsonHeaders });
  }
  let body: { query?: unknown; params?: unknown };
  try {
    body = await req.json() as { query?: unknown; params?: unknown };
  } catch {
    return new Response(JSON.stringify({ error: "invalid json" }), { status: 400, headers: jsonHeaders });
  }
  const query = validatePrivateGrowthSql(body.query);
  const params = Array.isArray(body.params) ? body.params : [];
  if (!query || params.length > MAX_PRIVATE_SQL_PARAMS) {
    return new Response(JSON.stringify({ error: "query rejected" }), { status: 400, headers: jsonHeaders });
  }

  try {
    const result = await sql.unsafe(query, params);
    const columns = (result.columns ?? []).map((column: { name: string }) => String(column.name));
    const fallbackColumns = columns.length ? columns : (result.length ? Object.keys(result[0] as Record<string, unknown>) : []);
    const rows = result.map((row: Record<string, unknown>) => fallbackColumns.map((name) => jsonSafe(row[name])));
    return new Response(JSON.stringify({
      rows,
      columns: fallbackColumns,
      rowcount: Number(result.count ?? result.length ?? 0),
    }), { status: 200, headers: jsonHeaders });
  } catch (error) {
    console.error("private growth SQL failed", error instanceof Error ? error.name : "unknown");
    return new Response(JSON.stringify({ error: "query failed" }), { status: 503, headers: jsonHeaders });
  }
}

async function publicJobs(url: URL): Promise<Response> {
  const days = clampInt(url.searchParams.get("days"), 14, 1, 45);
  const limit = clampInt(url.searchParams.get("limit"), 120, 1, 300);
  try {
    const rows = await sql`
      SELECT hash, payload, updated_at
      FROM public.growth_job_payloads
      WHERE publication_state = 'published'
        AND updated_at >= NOW() - (${days} * INTERVAL '1 day')
      ORDER BY updated_at DESC
      LIMIT ${limit}
    `;
    return new Response(JSON.stringify({ rows: jsonSafe(rows) }), {
      status: 200,
      headers: {
        ...jsonHeaders,
        "Cache-Control": "public, max-age=60, s-maxage=60, stale-while-revalidate=300",
      },
    });
  } catch (error) {
    console.error("public jobs query failed", error instanceof Error ? error.name : "unknown");
    return new Response(JSON.stringify({ error: "store unavailable" }), { status: 503, headers: jsonHeaders });
  }
}

async function parseAuthedJson(req: Request): Promise<{ ok: true; body: Record<string, unknown> } | { ok: false; response: Response }> {
  const token = req.headers.get("x-telegram-bot-token") ?? "";
  if (!(await telegramBotAuthorized(token))) {
    return { ok: false, response: new Response(JSON.stringify({ error: "unauthorized" }), { status: 401, headers: jsonHeaders }) };
  }
  let text = "";
  try { text = await req.text(); } catch {
    return { ok: false, response: new Response(JSON.stringify({ error: "invalid body" }), { status: 400, headers: jsonHeaders }) };
  }
  if (!text || text.length > 300_000) {
    return { ok: false, response: new Response(JSON.stringify({ error: "invalid body size" }), { status: 400, headers: jsonHeaders }) };
  }
  try {
    const body = JSON.parse(text);
    if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("bad body");
    return { ok: true, body: body as Record<string, unknown> };
  } catch {
    return { ok: false, response: new Response(JSON.stringify({ error: "invalid json" }), { status: 400, headers: jsonHeaders }) };
  }
}

async function claimJobPayload(req: Request): Promise<Response> {
  const parsed = await parseAuthedJson(req);
  if (!parsed.ok) return parsed.response;
  const hash = parseJobHash(parsed.body.hash);
  if (!hash) return new Response(JSON.stringify({ error: "invalid hash" }), { status: 400, headers: jsonHeaders });
  const payload = parsed.body.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return new Response(JSON.stringify({ error: "invalid payload" }), { status: 400, headers: jsonHeaders });
  }
  const protocol = Number(parsed.body.protocol_version ?? 1);

  try {
    if (protocol !== PROTOCOL_V2) {
      const inserted = await sql`
        INSERT INTO public.growth_job_payloads
          (hash, payload, created_at, updated_at, publication_state, published_at)
        VALUES (${hash}, ${sql.json(payload as Record<string, unknown>)}, NOW(), NOW(), 'published', NOW())
        ON CONFLICT (hash) DO NOTHING
        RETURNING hash
      `;
      const created = inserted.length === 1;
      return new Response(JSON.stringify({ ok: true, hash, created }), { status: 200, headers: jsonHeaders });
    }

    const claimToken = crypto.randomUUID();
    const claimed = await sql`
      INSERT INTO public.growth_job_payloads
        (hash, payload, created_at, updated_at, publication_state, claim_token, lease_expires_at, published_at)
      VALUES (
        ${hash}, ${sql.json(payload as Record<string, unknown>)}, NOW(), NOW(),
        'pending', ${claimToken}, NOW() + INTERVAL '15 minutes', NULL
      )
      ON CONFLICT (hash) DO UPDATE SET
        payload = EXCLUDED.payload,
        updated_at = NOW(),
        publication_state = 'pending',
        claim_token = EXCLUDED.claim_token,
        lease_expires_at = EXCLUDED.lease_expires_at,
        published_at = NULL
      WHERE public.growth_job_payloads.publication_state = 'pending'
        AND public.growth_job_payloads.lease_expires_at IS NOT NULL
        AND public.growth_job_payloads.lease_expires_at <= NOW()
      RETURNING hash, claim_token, publication_state, lease_expires_at
    `;
    if (claimed.length === 1) {
      const row = claimed[0] as Record<string, unknown>;
      return new Response(JSON.stringify({
        ok: true,
        hash,
        created: true,
        claimed: true,
        claim_token: row.claim_token,
        publication_state: row.publication_state,
        lease_expires_at: jsonSafe(row.lease_expires_at),
      }), { status: 200, headers: jsonHeaders });
    }

    const existing = await sql`
      SELECT publication_state
      FROM public.growth_job_payloads
      WHERE hash = ${hash}
      LIMIT 1
    `;
    const state = existing.length ? String(existing[0].publication_state ?? "") : "";
    return new Response(JSON.stringify({
      ok: true,
      hash,
      created: false,
      claimed: false,
      publication_state: state,
    }), { status: 200, headers: jsonHeaders });
  } catch (error) {
    console.error("job payload claim failed", error instanceof Error ? error.name : "unknown");
    return new Response(JSON.stringify({ error: "store unavailable" }), { status: 503, headers: jsonHeaders });
  }
}

async function beginSending(req: Request): Promise<Response> {
  const parsed = await parseAuthedJson(req);
  if (!parsed.ok) return parsed.response;
  const hash = parseJobHash(parsed.body.hash);
  const claimToken = parseClaimToken(parsed.body.claim_token);
  if (!hash || !claimToken) return new Response(JSON.stringify({ error: "invalid claim" }), { status: 400, headers: jsonHeaders });

  try {
    const updated = await sql`
      UPDATE public.growth_job_payloads
      SET publication_state = 'sending', lease_expires_at = NULL, updated_at = NOW()
      WHERE hash = ${hash}
        AND claim_token = ${claimToken}
        AND publication_state = 'pending'
        AND lease_expires_at > NOW()
      RETURNING hash
    `;
    if (updated.length === 1) {
      return new Response(JSON.stringify({ ok: true, hash, sending: true }), { status: 200, headers: jsonHeaders });
    }
    const existing = await sql`
      SELECT publication_state
      FROM public.growth_job_payloads
      WHERE hash = ${hash} AND claim_token = ${claimToken}
      LIMIT 1
    `;
    const already = existing.length === 1 && existing[0].publication_state === 'sending';
    return new Response(JSON.stringify({ ok: true, hash, sending: already }), { status: 200, headers: jsonHeaders });
  } catch (error) {
    console.error("begin sending failed", error instanceof Error ? error.name : "unknown");
    return new Response(JSON.stringify({ error: "store unavailable" }), { status: 503, headers: jsonHeaders });
  }
}

async function markPublished(req: Request): Promise<Response> {
  const parsed = await parseAuthedJson(req);
  if (!parsed.ok) return parsed.response;
  const hash = parseJobHash(parsed.body.hash);
  const claimToken = parseClaimToken(parsed.body.claim_token);
  if (!hash || !claimToken) return new Response(JSON.stringify({ error: "invalid claim" }), { status: 400, headers: jsonHeaders });

  try {
    const updated = await sql`
      UPDATE public.growth_job_payloads
      SET publication_state = 'published', published_at = COALESCE(published_at, NOW()),
          lease_expires_at = NULL, updated_at = NOW()
      WHERE hash = ${hash}
        AND claim_token = ${claimToken}
        AND publication_state = 'sending'
      RETURNING hash
    `;
    if (updated.length === 1) {
      return new Response(JSON.stringify({ ok: true, hash, published: true }), { status: 200, headers: jsonHeaders });
    }
    const existing = await sql`
      SELECT publication_state
      FROM public.growth_job_payloads
      WHERE hash = ${hash} AND claim_token = ${claimToken}
      LIMIT 1
    `;
    const already = existing.length === 1 && existing[0].publication_state === 'published';
    return new Response(JSON.stringify({ ok: true, hash, published: already }), { status: 200, headers: jsonHeaders });
  } catch (error) {
    console.error("mark published failed", error instanceof Error ? error.name : "unknown");
    return new Response(JSON.stringify({ error: "store unavailable" }), { status: 503, headers: jsonHeaders });
  }
}

async function releaseJobPayload(req: Request): Promise<Response> {
  const parsed = await parseAuthedJson(req);
  if (!parsed.ok) return parsed.response;
  const hash = parseJobHash(parsed.body.hash);
  if (!hash) return new Response(JSON.stringify({ error: "invalid hash" }), { status: 400, headers: jsonHeaders });
  const claimToken = parseClaimToken(parsed.body.claim_token);

  try {
    let deleted;
    if (claimToken) {
      deleted = await sql`
        DELETE FROM public.growth_job_payloads
        WHERE hash = ${hash}
          AND claim_token = ${claimToken}
          AND publication_state = 'pending'
        RETURNING hash
      `;
    } else {
      // Protocol-v1 rollout compatibility. Protocol-v2 callers always send a token.
      deleted = await sql`
        DELETE FROM public.growth_job_payloads
        WHERE hash = ${hash}
          AND claim_token IS NULL
          AND publication_state = 'published'
        RETURNING hash
      `;
    }
    return new Response(JSON.stringify({ ok: true, hash, released: deleted.length === 1 }), { status: 200, headers: jsonHeaders });
  } catch (error) {
    console.error("job payload release failed", error instanceof Error ? error.name : "unknown");
    return new Response(JSON.stringify({ error: "store unavailable" }), { status: 503, headers: jsonHeaders });
  }
}

Deno.serve(async (req: Request) => {
  const url = new URL(req.url);
  if (req.method === "GET" && url.pathname.endsWith("/health")) {
    return new Response(JSON.stringify({
      ok: Boolean(DB_URL),
      service: "growth-proxy",
      protocol: PROTOCOL_V2,
      render_bridge: Boolean(RENDER_BRIDGE_KEY_SHA256),
    }), { status: DB_URL ? 200 : 503, headers: jsonHeaders });
  }
  if (req.method === "GET" && url.pathname.endsWith("/public-jobs")) return publicJobs(url);
  if (req.method === "POST" && url.pathname.endsWith("/job-payloads/sending")) return beginSending(req);
  if (req.method === "POST" && url.pathname.endsWith("/job-payloads/published")) return markPublished(req);
  if (req.method === "POST" && url.pathname.endsWith("/job-payloads/release")) return releaseJobPayload(req);
  if (req.method === "POST" && url.pathname.endsWith("/job-payloads")) return claimJobPayload(req);
  if (req.method === "POST" && /\/growth-proxy\/?$/.test(url.pathname)) return privateGrowthSql(req);
  return new Response(JSON.stringify({ error: "not found" }), { status: 404, headers: jsonHeaders });
});
