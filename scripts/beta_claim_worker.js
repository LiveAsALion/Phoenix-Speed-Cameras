// SpeedShield Beta Claim Worker
//
// Validates a beta tester email hash and enforces one-device-per-email.
//
// Setup:
//   1. Create a Cloudflare Workers project named "speedshield-beta"
//   2. Create a KV namespace named "BETA_CLAIMS" and bind it to this worker
//   3. Paste this script into the worker editor and deploy
//   4. Update _betaClaimUrl in subscription_service.dart with your worker URL
//
// KV key format:
//   "claim:{hash}" → deviceId string  (set when a tester first claims)
//
// To reset a tester (new phone / factory reset):
//   Delete the KV key "claim:{hash}" via the Cloudflare dashboard or API.
//
// To add/remove testers:
//   Edit beta_testers.json in the Phoenix-Speed-Cameras repo.

const BETA_TESTERS_URL =
  'https://raw.githubusercontent.com/LiveAsALion/Phoenix-Speed-Cameras/main/beta_testers.json';

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    if (request.method !== 'POST') {
      return json({ error: 'Method not allowed' }, 405);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: 'Invalid JSON' }, 400);
    }

    const { hash, deviceId } = body;
    if (!hash || typeof hash !== 'string' || hash.length !== 64) {
      return json({ error: 'Invalid hash' }, 400);
    }
    if (!deviceId || typeof deviceId !== 'string') {
      return json({ error: 'Invalid deviceId' }, 400);
    }

    // Validate hash against the tester list
    const validHashes = await fetchValidHashes(env);
    if (!validHashes.has(hash)) {
      return json({ granted: false, reason: 'not_a_tester' });
    }

    // Check claim status in KV
    const kvKey = `claim:${hash}`;
    const claimedBy = await env.BETA_CLAIMS.get(kvKey);

    if (claimedBy === null) {
      // First claim — bind this hash to the device
      await env.BETA_CLAIMS.put(kvKey, deviceId);
      return json({ granted: true });
    }

    if (claimedBy === deviceId) {
      // Same device reclaiming (reinstall / app data clear)
      return json({ granted: true });
    }

    // Different device — reject
    return json({ granted: false, reason: 'already_claimed' });
  },
};

async function fetchValidHashes(env) {
  // Cache the tester list in KV for 1 hour to avoid hammering GitHub
  const cacheKey = 'cache:valid_hashes';
  const cached = await env.BETA_CLAIMS.get(cacheKey);
  if (cached) {
    return new Set(JSON.parse(cached));
  }

  const resp = await fetch(BETA_TESTERS_URL);
  if (!resp.ok) throw new Error(`Failed to fetch tester list: ${resp.status}`);

  const data = await resp.json();
  const hashes = data.hashes;

  // Cache for 1 hour (3600 seconds)
  await env.BETA_CLAIMS.put(cacheKey, JSON.stringify(hashes), { expirationTtl: 3600 });

  return new Set(hashes);
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders() },
  });
}

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
}
