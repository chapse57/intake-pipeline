/**
 * forward.js — sign a payload and post it to the n8n webhook.
 *
 * Shared by the relay (a fresh inquiry) and the queue (an inquiry held while
 * n8n was down). The signature is always made at send time: WF-4 refuses a
 * timestamp older than five minutes, so an inquiry held overnight has to be
 * signed again when it is finally delivered, never replayed with its old one.
 */

const enc = new TextEncoder();
export const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');

export const FORWARD_TIMEOUT_MS = 15_000;

export async function hmacHex(secret, message) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, enc.encode(message)));
}

export async function sha256Hex(s) {
  return hex(await crypto.subtle.digest('SHA-256', enc.encode(s)));
}

// ngrok answers 404 when the tunnel is down (PC off); 5xx is n8n or the
// tunnel failing. Both are downtime, not a bad request.
export const isDown = (status) => status === 404 || status >= 500;

/**
 * @returns {Promise<{delivered: boolean, down: boolean, status: number, error?: string}>}
 */
export async function forward(env, payload, upstream = fetch, now = Date.now()) {
  const ts = String(now);
  const signature = await hmacHex(env.INQUIRY_SECRET, ts + '.' + payload);
  try {
    const res = await upstream(env.N8N_WEBHOOK_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Ether-Timestamp': ts,
        'X-Ether-Signature': signature,
        'ngrok-skip-browser-warning': '1',
      },
      body: JSON.stringify({ payload }),
      signal: AbortSignal.timeout(FORWARD_TIMEOUT_MS),
    });
    return { delivered: res.ok, down: isDown(res.status), status: res.status };
  } catch (e) {
    return { delivered: false, down: true, status: 0, error: String(e) };
  }
}
