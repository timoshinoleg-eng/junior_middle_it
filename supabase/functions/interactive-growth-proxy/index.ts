import postgres from "postgres";

const DB_URL = Deno.env.get("SUPABASE_DB_URL") ?? "";
const EXPECTED_BOT_USERNAME = "junior_jobs_channel_bot";
const MAX_SQL_BYTES = 40_000;
const MAX_PARAMS = 80;
const MAX_RESPONSE_BYTES = 2_000_000;
const AUTH_CACHE_MS = 5 * 60_000;
const sql = postgres(DB_URL, { prepare: false, max: 1, idle_timeout: 20, connect_timeout: 10 });
const telegramAuthCache = new Map<string, number>();

const jsonHeaders = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
};

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

async function telegramBotAuthorized(token: string): Promise<boolean> {
  if (!/^[0-9]{5,20}:[A-Za-z0-9_-]{20,200}$/.test(token)) return false;
  const tokenHash = await sha256Hex(token);
  const now = Date.now();
  if ((telegramAuthCache.get(tokenHash) ?? 0) > now) return true;

  try {
    const cached = await sql`
      SELECT 1
      FROM public.growth_proxy_auth_cache
      WHERE token_hash = ${tokenHash}
        AND bot_username = ${EXPECTED_BOT_USERNAME}
        AND expires_at > NOW()
      LIMIT 1
    `;
    if (cached.length) {
      telegramAuthCache.set(tokenHash, now + AUTH_CACHE_MS);
      return true;
    }
  } catch {
    // Cache failure must not make a valid bot unavailable.
  }

  try {
    const response = await fetch(`https://api.telegram.org/bot${token}/getMe`, {
      method: "GET",
      headers: { "Accept": "application/json" },
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return false;
    const body = await response.json() as { ok?: boolean; result?: { is_bot?: boolean; username?: string } };
    const valid = Boolean(body.ok && body.result?.is_bot === true && body.result?.username === EXPECTED_BOT_USERNAME);
    if (!valid) return false;

    telegramAuthCache.set(tokenHash, now + AUTH_CACHE_MS);
    try {
      await sql`
        INSERT INTO public.growth_proxy_auth_cache
          (token_hash, bot_username, verified_at, expires_at)
        VALUES
          (${tokenHash}, ${EXPECTED_BOT_USERNAME}, NOW(), NOW() + INTERVAL '5 minutes')
        ON CONFLICT (token_hash) DO UPDATE SET
          bot_username = EXCLUDED.bot_username,
          verified_at = EXCLUDED.verified_at,
          expires_at = EXCLUDED.expires_at
      `;
      await sql`DELETE FROM public.growth_proxy_auth_cache WHERE expires_at < NOW() - INTERVAL '1 day'`;
    } catch {
      // Telegram already proved authorization; persistence is only a latency optimization.
    }
    return true;
  } catch {
    return false;
  }
}

function validateGrowthSql(value: unknown): string {
  const query = typeof value === "string" ? value.trim() : "";
  if (!query || new TextEncoder().encode(query).length > MAX_SQL_BYTES) return "";
  if (/--|\/\*|\*\//.test(query)) return "";
  const statement = query.replace(/;\s*$/, "");
  if (statement.includes(";")) return "";
  const normalized = statement.replace(/\s+/g, " ").trim();
  const lower = normalized.toLowerCase();

  if (!/^(select\b|with\b|insert\b|update\b|delete\b)/i.test(normalized)) return "";
  const forbidden = /\b(pg_catalog|information_schema|auth\.|storage\.|vault\.|extensions\.|realtime\.|graphql\.|supabase_|create\b|alter\b|drop\b|truncate\b|grant\b|revoke\b|copy\b|call\b|do\s+\$|prepare\b|execute\b|deallocate\b|listen\b|notify\b|vacuum\b|set_config\b|pg_sleep\b|pg_read_file\b|pg_ls_dir\b|dblink\b|lo_import\b|lo_export\b)/i;
  if (forbidden.test(lower)) return "";

  const ctes = new Set<string>();
  for (const match of normalized.matchAll(/\b([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(/gi)) ctes.add(match[1].toLowerCase());

  const relations: string[] = [];
  const relationPatterns = [
    /\bFROM\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bJOIN\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bINTO\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bDELETE\s+FROM\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
    /\bUPDATE\s+(?!SET\b)((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)/gi,
  ];
  for (const pattern of relationPatterns) for (const match of normalized.matchAll(pattern)) relations.push(match[1].toLowerCase());
  if (!relations.length) return "";

  for (const relation of relations) {
    const parts = relation.split(".");
    if (parts.length > 2) return "";
    if (parts.length === 2 && parts[0] !== "public") return "";
    const name = parts.at(-1) ?? "";
    if (name === "growth_proxy_auth_cache") return "";
    if (!name.startsWith("growth_") && !ctes.has(name)) return "";
  }
  return statement;
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return new Response(JSON.stringify({ error: "method not allowed" }), { status: 405, headers: jsonHeaders });
  const token = (req.headers.get("x-telegram-bot-token") ?? "").trim();
  if (!(await telegramBotAuthorized(token))) return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401, headers: jsonHeaders });

  let text = "";
  try { text = await req.text(); } catch {
    return new Response(JSON.stringify({ error: "invalid body" }), { status: 400, headers: jsonHeaders });
  }
  if (!text || new TextEncoder().encode(text).length > MAX_SQL_BYTES + 32_000) {
    return new Response(JSON.stringify({ error: "invalid body size" }), { status: 400, headers: jsonHeaders });
  }

  let body: { query?: unknown; params?: unknown };
  try { body = JSON.parse(text) as { query?: unknown; params?: unknown }; } catch {
    return new Response(JSON.stringify({ error: "invalid json" }), { status: 400, headers: jsonHeaders });
  }
  const query = validateGrowthSql(body.query);
  const params = Array.isArray(body.params) ? body.params : [];
  if (!query || params.length > MAX_PARAMS) return new Response(JSON.stringify({ error: "query rejected" }), { status: 400, headers: jsonHeaders });

  try {
    const result = await sql.unsafe(query, params);
    const columns = (result.columns ?? []).map((column: { name: string }) => String(column.name));
    const fallbackColumns = columns.length ? columns : (result.length ? Object.keys(result[0] as Record<string, unknown>) : []);
    const rows = result.map((row: Record<string, unknown>) => fallbackColumns.map((name) => jsonSafe(row[name])));
    const responseBody = JSON.stringify({ rows, columns: fallbackColumns, rowcount: Number(result.count ?? result.length ?? 0) });
    if (new TextEncoder().encode(responseBody).length > MAX_RESPONSE_BYTES) return new Response(JSON.stringify({ error: "response too large" }), { status: 413, headers: jsonHeaders });
    return new Response(responseBody, { status: 200, headers: jsonHeaders });
  } catch (error) {
    console.error("interactive growth SQL failed", error instanceof Error ? error.name : "unknown");
    return new Response(JSON.stringify({ error: "query failed" }), { status: 503, headers: jsonHeaders });
  }
});
