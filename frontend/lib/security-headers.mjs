// Browser security header policy, consumed by next.config.mjs.  Kept as a
// plain ESM module so it is unit-testable (lib/security-headers.test.ts).
//
// CSP rollout: we cannot discover every runtime need (Google Picker iframes,
// future integrations, browser extensions) without real traffic, so the FULL
// policy ships as Content-Security-Policy-Report-Only (violations are logged
// in the browser console, nothing breaks).  Only `frame-ancestors 'none'` is
// ENFORCED, because it is safe by construction and gives clickjacking
// protection now.  Promote the report-only policy to enforcing once clean.

const DEFAULT_API_URL = "http://localhost:8000";

function apiSources(apiUrl) {
  let origin;
  try {
    origin = new URL(apiUrl || DEFAULT_API_URL).origin;
  } catch {
    origin = DEFAULT_API_URL;
  }
  const ws = origin.replace(/^http/, "ws"); // http->ws, https->wss
  return [origin, ws];
}

/**
 * @param {{ nodeEnv?: string, apiUrl?: string }} [env]
 * @returns {{ key: string, value: string }[]}
 */
export function buildSecurityHeaders({
  nodeEnv = process.env.NODE_ENV,
  apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL,
} = {}) {
  const isProd = nodeEnv === "production";

  const scriptSrc = [
    "'self'",
    "'unsafe-inline'", // Next.js inline bootstrap/hydration scripts
    ...(isProd ? [] : ["'unsafe-eval'"]), // React Fast Refresh in dev only
    "https://apis.google.com", // Google Drive Picker (gapi loader)
    "https://www.gstatic.com",
  ];

  const csp = [
    "default-src 'self'",
    `script-src ${scriptSrc.join(" ")}`,
    "style-src 'self' 'unsafe-inline'", // Tailwind / React Flow inline styles
    "img-src 'self' data: blob: https://*.googleusercontent.com https://drive.google.com https://*.gstatic.com",
    "font-src 'self' data:", // next/font self-hosts Plus Jakarta Sans at build
    `connect-src 'self' ${apiSources(apiUrl).join(" ")} https://apis.google.com https://content.googleapis.com https://www.googleapis.com`,
    "frame-src 'self' https://docs.google.com https://drive.google.com https://apis.google.com https://accounts.google.com",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
  ].join("; ");

  const headers = [
    // Enforced: clickjacking protection only.
    { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
    // Report-only: the full policy, see header comment.
    { key: "Content-Security-Policy-Report-Only", value: csp },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    {
      key: "Permissions-Policy",
      value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    },
  ];

  if (isProd) {
    headers.push({
      key: "Strict-Transport-Security",
      value: "max-age=31536000; includeSubDomains",
    });
  }
  return headers;
}
